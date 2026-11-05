import json
import threading
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from arcgis_agent.agent import Action, Agent, PlanPlanner
from arcgis_agent.chat import ChatSession
from arcgis_agent.config import Settings
from arcgis_agent.deepseek import tool_schemas
from arcgis_agent.gis_tools import export_selected_ids
from arcgis_agent.runtime import ArcPyRuntime, HostBridgeRuntime, MockRuntime
from arcgis_agent.interfaces import rpc
from arcgis_agent.selections import MAX_SELECTION, SelectionStore, validate_snapshot
from arcgis_agent.tools import TOOLS, approval_details, execute, validate_call
from approval_fixtures import RPCInput, RPCOutput


def selection(ids=None, **extra):
    return {"dataset": "E:/GIS/input.gdb/points", "object_ids": [9, 2] if ids is None else ids,
            "layer_name": "测试点", "map_uri": "map-1", "layer_uri": "layer-1", **extra}


ARGUMENTS = {"map_uri": "map-1", "layer_uri": "layer-1"}


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.store = SelectionStore()

    def test_snapshot_freezes_ids_and_never_exposes_bulk_ids(self):
        source = selection()
        public = self.store.capture(source, ARGUMENTS)
        source["object_ids"].append(20)
        snapshot = self.store.get(public["selection_token"])
        self.assertEqual(snapshot.object_ids, (2, 9))
        self.assertEqual(public["selected_count"], 2)
        self.assertNotIn("object_ids", public)
        self.assertEqual(len(public["object_id_digest"]), 64)
        copied = snapshot.payload()
        copied["object_ids"].append(99)
        self.assertEqual(snapshot.object_ids, (2, 9))

    def test_empty_selection_does_not_create_exportable_handle(self):
        public = self.store.capture(selection([]), ARGUMENTS)
        self.assertEqual(public["selected_count"], 0)
        self.assertIsNone(public["selection_token"])
        self.assertEqual(len(self.store.snapshots), 0)

    def test_invalid_or_mismatched_host_selection_is_rejected(self):
        for data in (selection([True]), selection([-1]), selection([2, 2]), selection([2**63]),
                     selection(list(range(MAX_SELECTION + 1))), selection(dataset="relative/points"),
                     selection(dataset="https://example.invalid/service"), selection(map_uri="another-map"),
                     selection(layer_uri="another-layer"), selection(object_ids="1,2")):
            with self.subTest(data_type=type(data["object_ids"]).__name__), self.assertRaises(ValueError):
                self.store.capture(data, ARGUMENTS)

    def test_expired_evicted_reset_and_foreign_handles_are_invalid(self):
        now = [100]
        store = SelectionStore(clock=lambda: now[0])
        first = store.capture(selection(), ARGUMENTS)["selection_token"]
        for _ in range(8):
            last = store.capture(selection(), ARGUMENTS)["selection_token"]
        self.assertEqual(len(store.snapshots), 8)
        with self.assertRaises(ValueError):
            store.get(first)
        with self.assertRaises(ValueError):
            self.store.get(last)
        now[0] += 1800
        with self.assertRaises(ValueError):
            store.get(last)
        fresh = store.capture(selection(), ARGUMENTS)["selection_token"]
        store.clear()
        with self.assertRaises(ValueError):
            store.get(fresh)

    def test_selection_tools_are_host_only_typed_and_preview_output(self):
        declared = {item["function"]["name"] for item in tool_schemas()}
        self.assertFalse({"get_selection", "export_selection"} & declared)
        self.assertTrue({"get_selection", "export_selection"}.issubset({item["function"]["name"] for item in tool_schemas(set(TOOLS))}))
        for args in ({"selection_token": "invented", "output_name": "result"},
                     {"selection_token": "a" * 32, "output_name": "../escape"},
                     {"selection_token": "a" * 32, "output_name": "result", "object_ids": [2]}):
            with self.assertRaises(ValueError):
                validate_call("export_selection", args)
        preview = approval_details("export_selection", {"selection_token": "a" * 32, "output_name": "result"}, self.root)
        self.assertEqual(preview["expected_outputs"], [str(self.root / "results.gdb/result")])
        self.assertTrue(preview["writes"])

    def test_export_preview_includes_frozen_source_count_and_deny_never_exports(self):
        base = Mock(wraps=MockRuntime())
        host = HostBridgeRuntime(base, None, True, self.store)
        token = self.store.capture(selection(), ARGUMENTS)["selection_token"]
        approvals = []
        def deny(request):
            approvals.append(request)
            self.assertEqual(request["selection"]["dataset"], "E:/GIS/input.gdb/points")
            self.assertEqual(request["selection"]["selected_count"], 2)
            self.assertNotIn("object_ids", request["selection"])
            return {"decision": "deny"}
        result = Agent(host, self.root).run(PlanPlanner([{"tool": "export_selection", "arguments": {
            "selection_token": token, "output_name": "result"}}]), approve=deny, allow_writes=True)
        self.assertEqual(result["status"], "denied")
        self.assertEqual(len(approvals), 1)
        base.export_selection.assert_not_called()

    def test_capture_then_export_requires_two_approvals_and_strips_ids_from_audit(self):
        base = Mock(wraps=MockRuntime())
        broker = Mock()
        broker.request.return_value = {"ok": True, "data": selection()}
        host = HostBridgeRuntime(base, broker, True, self.store)
        class Planner:
            def next_action(self, observations):
                if not observations:
                    return Action("get_selection", ARGUMENTS)
                if len(observations) == 1:
                    return Action("export_selection", {"selection_token": observations[0]["data"]["selection_token"], "output_name": "selected"})
                return None
        approvals = []
        def allow(request):
            approvals.append(request)
            self.assertEqual(base.export_selection.call_count, 0)
            return {"decision": "allow", "allow_writes": request["writes"]}
        result = Agent(host, self.root).run(Planner(), approve=allow)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["simulated"])
        self.assertEqual([request["tool"] for request in approvals], ["get_selection", "export_selection"])
        broker.request.assert_called_once()
        self.assertEqual(base.export_selection.call_args.args[0].object_ids, (2, 9))
        self.assertNotIn('"object_ids"', (Path(result["run_dir"]) / "events.jsonl").read_text(encoding="utf-8"))
        self.assertNotIn('"object_ids"', json.dumps(result))

    def test_export_missing_handler_expired_token_or_host_disabled_never_dispatches(self):
        base = Mock(wraps=MockRuntime())
        token = self.store.capture(selection(), ARGUMENTS)["selection_token"]
        operation = [{"tool": "export_selection", "arguments": {"selection_token": token, "output_name": "chosen"}}]
        host = HostBridgeRuntime(base, None, True, self.store)
        self.assertEqual(Agent(host, self.root).run(PlanPlanner(operation))["status"], "approval_required")
        self.store.clear()
        self.assertEqual(Agent(host, self.root).run(PlanPlanner(operation), approve=lambda request: {"decision": "allow"})["status"], "failed")
        self.assertFalse(HostBridgeRuntime(base, None, False).call("export_selection", operation[0]["arguments"], self.root)["ok"])
        base.export_selection.assert_not_called()

    def test_worker_payload_is_separate_from_public_arguments_and_token_bound(self):
        public = self.store.capture(selection(), ARGUMENTS)
        snapshot = self.store.get(public["selection_token"])
        args = {"selection_token": snapshot.token, "output_name": "selected"}
        def worker(command, **kwargs):
            request = json.loads(Path(command[-2]).read_text(encoding="utf-8"))
            self.assertEqual(request["arguments"], args)
            self.assertEqual(request["selection_snapshot"]["object_ids"], [2, 9])
            self.assertEqual(validate_snapshot(request["selection_snapshot"], args["selection_token"])["object_ids"], (2, 9))
            Path(command[-1]).write_text('{"ok":true,"data":{"count":2}}', encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")
        with patch("arcgis_agent.runtime.subprocess.run", side_effect=worker):
            result = ArcPyRuntime(Path("python.exe")).export_selection(snapshot, args, self.root)
        self.assertEqual(result["data"]["count"], 2)
        with self.assertRaises(ValueError):
            validate_snapshot(snapshot.payload(), "b" * 32)
        with self.assertRaises(ValueError):
            execute("export_selection", args, self.root)

    def test_token_expiring_during_approval_cannot_execute(self):
        now = [0]
        store = SelectionStore(clock=lambda: now[0])
        token = store.capture(selection(), ARGUMENTS)["selection_token"]
        base = Mock(wraps=MockRuntime())
        host = HostBridgeRuntime(base, None, True, store)
        def allow(request):
            now[0] = 1801
            return {"decision": "allow", "allow_writes": True}
        result = Agent(host, self.root).run(PlanPlanner([{"tool": "export_selection", "arguments": {
            "selection_token": token, "output_name": "chosen"}}]), approve=allow)
        self.assertEqual(result["status"], "failed")
        base.export_selection.assert_not_called()

    def test_rpc_selection_capture_and_export_roundtrip(self):
        class Client:
            cancel_event = threading.Event()
            available_tools = set()
            def complete(self, messages):
                results = [json.loads(message["content"]) for message in messages if message["role"] == "tool"]
                if len(results) == 0:
                    name, args = "get_selection", ARGUMENTS
                elif len(results) == 1:
                    name, args = "export_selection", {"selection_token": results[0]["data"]["selection_token"], "output_name": "chosen"}
                else:
                    return {"role": "assistant", "content": "模拟：已导出选择集。"}
                return {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "id": name,
                    "function": {"name": name, "arguments": json.dumps(args)}}]}
        base = Mock(wraps=MockRuntime())
        session = ChatSession(Client(), base, self.root)
        incoming = RPCInput({"type": "send", "message": "导出选中要素", "host_tools": True})
        approvals, hosts = [], []
        def handle(message, stream):
            if message["type"] != "event":
                return
            event = message["event"]
            if event["event"] == "approval_requested":
                approvals.append(event)
                stream.put({"type": "approval_response", "request_id": event["request_id"],
                            "response": {"decision": "allow", "allow_writes": event["writes"]}})
            elif event["event"] == "host_requested":
                hosts.append(event)
                stream.put({"type": "host_result", "request_id": event["request_id"], "response": {"ok": True, "data": selection()}})
        outgoing = RPCOutput(incoming, handle)
        with patch("arcgis_agent.interfaces.create_session", return_value=session), patch("sys.stdin", incoming), patch("sys.stdout", outgoing):
            self.assertEqual(rpc(Settings(workspace=self.root)), 0)
        records = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual(records[-1]["result"]["status"], "completed")
        self.assertEqual(len(hosts), 1)  # Export is handled by the ArcPy worker, not SDK.
        self.assertEqual([event["tool"] for event in approvals], ["get_selection", "export_selection"])
        self.assertEqual(approvals[1]["selection"]["selected_count"], 2)
        base.export_selection.assert_called_once()
        self.assertNotIn('"object_ids"', outgoing.getvalue())

    def test_selection_handle_survives_wrapper_change_until_session_reset(self):
        broker = Mock()
        broker.request.return_value = {"ok": True, "data": selection()}
        first = HostBridgeRuntime(MockRuntime(), broker, True, self.store)
        result = first.call("get_selection", ARGUMENTS, self.root)
        token = result["data"]["selection_token"]
        second = HostBridgeRuntime(MockRuntime(), broker, True, self.store)
        self.assertEqual(second.approval_context("export_selection", {"selection_token": token})["selected_count"], 2)
        self.store.clear()
        with self.assertRaises(ValueError):
            second.approval_context("export_selection", {"selection_token": token})

    def test_export_refuses_missing_ids_before_creating_outputs(self):
        from types import SimpleNamespace
        class Cursor:
            def __enter__(self):
                return iter([(2,)])
            def __exit__(self, *args):
                return False
        arcpy = Mock()
        arcpy.Describe.return_value = SimpleNamespace(hasOID=True, shapeType="Point", OIDFieldName="OBJECTID")
        arcpy.AddFieldDelimiters.return_value = '"OBJECTID"'
        arcpy.management.MakeFeatureLayer.return_value = ["own-temporary-layer"]
        arcpy.da.SearchCursor.return_value = Cursor()
        arcpy.EnvManager.return_value.__enter__ = Mock()
        arcpy.EnvManager.return_value.__exit__ = Mock(return_value=False)
        with self.assertRaises(ValueError):
            export_selected_ids(arcpy, {"dataset": "E:/GIS/points", "object_ids": (2, 9)}, "selected", self.root / "outputs")
        self.assertFalse((self.root / "outputs").exists())
        arcpy.management.CopyFeatures.assert_not_called()
        arcpy.management.Delete.assert_called_once_with("own-temporary-layer")


if __name__ == "__main__":
    unittest.main()
