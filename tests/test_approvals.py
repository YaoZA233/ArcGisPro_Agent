import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from arcgis_agent.agent import Action, Agent, PlanPlanner
from arcgis_agent.approvals import RequestBroker, terminal_approval
from arcgis_agent.chat import ChatSession
from arcgis_agent.cli import main
from arcgis_agent.config import Settings
from arcgis_agent.deepseek import Cancelled, tool_schemas
from arcgis_agent.gis_tools import where_clause
from arcgis_agent.interfaces import rpc
from arcgis_agent.runtime import MockRuntime
from arcgis_agent.tools import TOOLS, approval_details, validate_call
from approval_fixtures import RPCInput, RPCOutput


def step(name="environment", **arguments):
    return {"tool": name, "arguments": arguments}


class FakeClient:
    def __init__(self, calls):
        self.calls = calls
        self.cancel_event = threading.Event()

    def complete(self, messages):
        if self.calls:
            calls, self.calls = self.calls, []
            return {"role": "assistant", "content": "", "tool_calls": [
                {"type": "function", "id": "c" + str(i), "function": {"name": s["tool"], "arguments": json.dumps(s["arguments"])}} for i, s in enumerate(calls)]}
        return {"role": "assistant", "content": "已完成实际请求的工具步骤。"}


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.runtime = Mock(wraps=MockRuntime())

    def run_plan(self, steps, **kwargs):
        return Agent(self.runtime, self.root).run(PlanPlanner(steps), **kwargs)

    def test_no_handler_fails_closed_even_with_global_writes_enabled(self):
        for operation in (step(), step("buffer", dataset="x", distance="1 Meters", output_name="test")):
            result = self.run_plan([operation], allow_writes=True)
            self.assertEqual(result["status"], "approval_required")
            self.assertFalse((Path(result["run_dir"]) / "outputs").exists())
        self.runtime.call.assert_not_called()

    def test_each_step_gets_new_request_and_denial_stops_later_steps(self):
        requests = []
        def approve(request):
            requests.append(request)
            self.assertEqual(self.runtime.call.call_count, len(requests) - 1)
            return {"decision": "allow" if len(requests) == 1 else "deny"}
        result = self.run_plan([step(), step(), step()], approve=approve)
        self.assertEqual(result["status"], "denied")
        self.assertEqual(self.runtime.call.call_count, 1)
        self.assertEqual(len(requests), 2)
        self.assertNotEqual(requests[0]["request_id"], requests[1]["request_id"])
        records = [json.loads(line) for line in (Path(result["run_dir"]) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["decision"] for r in records if r["event"] == "approval_decided"], ["allow", "deny"])

    def test_single_write_grant_does_not_grant_next_operation(self):
        decisions = iter([{"decision": "allow", "allow_writes": True}, {"decision": "deny"}])
        steps = [step("buffer", dataset="x", distance="1 Meters", output_name="a"), step("buffer", dataset="x", distance="2 Meters", output_name="b")]
        result = self.run_plan(steps, approve=lambda request: next(decisions))
        self.assertEqual(result["status"], "denied")
        self.assertEqual(self.runtime.call.call_count, 1)

    def test_preview_mutation_cannot_change_the_executed_arguments(self):
        arguments = {"dataset": "original"}
        class Planner:
            def next_action(self, observations):
                return Action("describe", arguments) if not observations else None
        def approve(request):
            request["arguments"]["dataset"] = "changed-preview"
            arguments["dataset"] = "changed-planner"
            return {"decision": "allow"}
        result = Agent(self.runtime, self.root).run(Planner(), approve=approve)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.runtime.call.call_args.args[1], {"dataset": "original"})

    def test_cancel_before_dispatch_overrides_allowed_response(self):
        cancelled = threading.Event()
        def approve(request):
            cancelled.set()
            return {"decision": "allow"}
        result = self.run_plan([step()], approve=approve, cancel_event=cancelled)
        self.assertEqual(result["status"], "cancelled")
        self.runtime.call.assert_not_called()

    def test_expired_cancelled_and_invalid_decisions_never_dispatch(self):
        for decision, status in (({"decision": "expired"}, "approval_expired"), ({"decision": "cancel"}, "cancelled"),
                                 (True, "failed"), ({"decision": "allow", "allow_writes": "true"}, "failed")):
            result = self.run_plan([step()], approve=lambda request: decision)
            self.assertEqual(result["status"], status)
        self.runtime.call.assert_not_called()

    def test_approval_outputs_match_task_outputs(self):
        path = self.root / "runs/example/outputs"
        details = approval_details("buffer", {"output_name": "buffered"}, path)
        self.assertEqual(details["expected_outputs"], [str(path / "results.gdb/buffered")])
        self.assertTrue(details["writes"])
        self.assertEqual(approval_details("clip_raster", {"output_name": "cut"}, path)["expected_outputs"], [str(path / "cut.tif")])

    def test_broker_single_use_type_checks_and_replay_rejection(self):
        cancelled = threading.Event()
        broker = None
        def emit(message):
            ident = message["event"]["request_id"]
            with self.assertRaises(ValueError):
                broker.respond("host", ident, {"ok": True})
            with self.assertRaises(ValueError):
                broker.respond("approval", ident, {"decision": "allow", "allow_writes": "yes"})
            broker.respond("approval", ident, {"decision": "allow"})
            with self.assertRaises(ValueError):
                broker.respond("approval", ident, {"decision": "allow"})
        broker = RequestBroker(emit, cancelled)
        self.assertEqual(broker.request("approval", {"request_id": "one"}), {"decision": "allow"})
        with self.assertRaises(ValueError):
            broker.respond("approval", "one", {"decision": "allow"})

    def test_broker_timeout_disconnect_and_cancel_fail_closed(self):
        broker = RequestBroker(lambda event: None, threading.Event())
        self.assertEqual(broker.request("approval", {"request_id": "old"}, timeout=0), {"decision": "expired"})
        with self.assertRaises(ValueError):
            broker.respond("approval", "old", {"decision": "allow"})
        broker.close()
        with self.assertRaises(Cancelled):
            broker.request("approval", {})
        event = threading.Event()
        broker = RequestBroker(lambda value: event.set(), event)
        with self.assertRaises(Cancelled):
            broker.request("approval", {})

    def rpc_session(self, calls, handler, **request):
        session = ChatSession(FakeClient(calls), self.runtime, self.root)
        incoming = RPCInput({"type": "send", "message": "执行任务", **request})
        outgoing = RPCOutput(incoming, handler)
        with patch("arcgis_agent.interfaces.create_session", return_value=session), patch("sys.stdin", incoming), patch("sys.stdout", outgoing):
            self.assertEqual(rpc(Settings(workspace=self.root)), 0)
        return [json.loads(line) for line in outgoing.getvalue().splitlines()]

    def test_rpc_waits_for_each_parallel_model_call_and_denial_stops(self):
        approvals = []
        def handler(message, incoming):
            if message["type"] == "event" and message["event"]["event"] == "approval_requested":
                item = message["event"]
                self.assertEqual(self.runtime.call.call_count, len(approvals))
                approvals.append(item)
                incoming.put({"type": "approval_response", "request_id": item["request_id"], "response": {"decision": "allow" if len(approvals) == 1 else "deny"}})
        records = self.rpc_session([step(), step()], handler)
        self.assertEqual(records[-1]["result"]["status"], "denied")
        self.assertEqual(self.runtime.call.call_count, 1)
        self.assertEqual(len(approvals), 2)

    def test_rpc_host_bridge_executes_only_after_approval(self):
        approvals, host_calls = [], []
        def handler(message, incoming):
            if message["type"] != "event":
                return
            item = message["event"]
            if item["event"] == "approval_requested":
                self.assertEqual(host_calls, [])
                approvals.append(item)
                incoming.put({"type": "approval_response", "request_id": item["request_id"], "response": {"decision": "allow"}})
            elif item["event"] == "host_requested":
                self.assertEqual(len(approvals), 1)
                host_calls.append(item)
                incoming.put({"type": "host_result", "request_id": item["request_id"], "response": {"ok": True, "data": {"maps": [], "active_map_uri": ""}}})
        records = self.rpc_session([step("map_context")], handler, host_tools=True)
        self.assertEqual(records[-1]["result"]["status"], "completed")
        self.assertEqual(len(host_calls), 1)
        self.runtime.call.assert_not_called()

    def test_rpc_cancel_and_disconnected_input_do_not_dispatch(self):
        def handler(message, incoming):
            if message["type"] == "event" and message["event"]["event"] == "approval_requested":
                incoming.put({"type": "cancel"})
        records = self.rpc_session([step()], handler)
        self.assertEqual(records[-1]["result"]["status"], "cancelled")
        session = ChatSession(FakeClient([step()]), self.runtime, self.root)
        with patch("arcgis_agent.interfaces.create_session", return_value=session), patch("sys.stdin", io.StringIO('{"type":"send","message":"test"}\n')), patch("sys.stdout", io.StringIO()):
            self.assertEqual(rpc(Settings(workspace=self.root)), 0)
        self.runtime.call.assert_not_called()

    def test_terminal_defaults_to_reject_and_eof_cancels(self):
        request = {"title": "查看属性", "effect": "只读", "arguments": {}, "writes": False}
        with patch("builtins.input", return_value=""), patch("sys.stdout", io.StringIO()):
            self.assertEqual(terminal_approval(request)["decision"], "deny")
        with patch("builtins.input", side_effect=EOFError), patch("sys.stdout", io.StringIO()):
            self.assertEqual(terminal_approval(request)["decision"], "cancel")

    def test_cli_plan_prompts_separately_and_global_flag_does_not_skip(self):
        plan = self.root / "plan.json"
        plan.write_text(json.dumps({"version": 1, "steps": [step(), step()]}), encoding="utf-8")
        with patch("arcgis_agent.config.Settings.load", return_value=Settings(workspace=self.root)), patch("builtins.input", side_effect=["y", "n"]) as prompt, patch("sys.stdout", io.StringIO()) as output:
            self.assertEqual(main(["run", str(plan), "--mock", "--allow-writes"]), 1)
        self.assertEqual(prompt.call_count, 2)
        self.assertIn('"status": "denied"', output.getvalue())

    def test_typed_catalog_filters_names_and_rejects_code_payloads(self):
        self.assertEqual(len(TOOLS), 62)
        for name, args in (("read_rows", {"dataset": "x", "limit": 501}), ("project", {"dataset": "x", "target_wkid": True, "output_name": "out"}),
                           ("intersect", {"datasets": ["one"], "output_name": "out"}), ("set_field_value", {"dataset": "x", "field": "name", "expression": "exec()", "output_name": "out"}),
                           ("calculate_geometry", {"dataset": "x", "field_name": "area", "measurement": "AREA_GEODESIC", "unit": "METERS", "output_name": "out"})):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_call(name, args)
        cli_tools = {entry["function"]["name"] for entry in tool_schemas()}
        self.assertNotIn("map_context", cli_tools)
        self.assertIn("map_context", {entry["function"]["name"] for entry in tool_schemas(set(TOOLS))})

    def test_attribute_filter_escapes_values_and_checks_real_field_names(self):
        field = SimpleNamespace(name="name", type="String", length=100, isNullable=True)
        arcpy = SimpleNamespace(ListFields=lambda dataset: [field], AddFieldDelimiters=lambda dataset, name: '"' + name + '"')
        clause = where_clause(arcpy, "source", {"field": "name", "operator": "EQ", "value": "O'Brien' OR 1=1 --"})
        self.assertEqual(clause, '"name" = \'O\'\'Brien\'\' OR 1=1 --\'')
        with self.assertRaises(ValueError):
            where_clause(arcpy, "source", {"field": "name OR 1=1", "operator": "EQ", "value": "x"})


if __name__ == "__main__":
    unittest.main()
