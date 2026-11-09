import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from arcgis_agent.chat import ChatSession
from arcgis_agent.deepseek import DeepSeekClient, DeepSeekError, DeepSeekPlanner, read_api_key
from arcgis_agent.runtime import MockRuntime
from arcgis_agent.interfaces import create_session, rpc, setup_key
from arcgis_agent.config import Settings
from arcgis_agent.memory import MemoryStore
from approval_fixtures import fixture_approval, RPCInput, RPCOutput


def assistant(content=None, calls=None):
    result = {"role": "assistant", "content": content}
    if calls is not None:
        result["tool_calls"] = calls
    return result


def call(name, args, ident="call_1"):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def complete(self, messages):
        self.requests.append(json.loads(json.dumps(messages)))
        return next(self.responses)


class FailingRuntime:
    """Offline boundary with distinct failures to detect stale error persistence."""
    def __init__(self):
        self.calls = []

    def call(self, name, arguments, output_dir, on_progress=None):
        self.calls.append((name, dict(arguments)))
        attempt = len(self.calls)
        return {"ok": False, "simulated": True,
                "error": {"type": "ArcPy", "message": f"fixture failure {attempt}",
                          "hint": f"检查路径 {attempt}"}}


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_tool_roundtrip_and_multiturn_history(self):
        client = FakeClient([assistant(calls=[call("describe", {"dataset": "C:/a.gdb/roads"})]),
                             assistant("数据已检查。"), assistant("这是第二轮。")])
        session = ChatSession(client, MockRuntime(), self.root)
        events = []
        first = session.send("检查道路", approve=fixture_approval, on_event=events.append)
        self.assertEqual(first["reply"], "数据已检查。")
        self.assertTrue(first["simulated"])
        tool_message = client.requests[1][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(tool_message["tool_call_id"], "call_1")
        self.assertIn("simulated", tool_message["content"])
        self.assertIn("tool_requested", [e["event"] for e in events])
        session.send("继续", approve=fixture_approval)
        self.assertEqual([m["role"] for m in client.requests[2]], ["system", "user", "assistant", "tool", "assistant", "user"])
        session.reset()
        self.assertEqual(session.history, [])

    def test_multiple_tools_preserve_call_ids_and_observation_order(self):
        client = FakeClient([assistant(calls=[call("environment", {}, "a"), call("describe", {"dataset": "x"}, "b")]), assistant("完成")])
        approvals = []
        def approve(request):
            approvals.append(request)
            return fixture_approval(request)
        result = ChatSession(client, MockRuntime(), self.root).send("检查", approve=approve)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(client.requests), 2)
        self.assertEqual([m["tool_call_id"] for m in client.requests[1] if m["role"] == "tool"], ["a", "b"])
        self.assertEqual([item["tool"] for item in result["observations"]], ["environment", "describe"])
        self.assertTrue(all(item["ok"] for item in result["observations"]))
        self.assertEqual([request["tool"] for request in approvals], ["environment", "describe"])

    def assert_closed_calls(self, messages):
        pending = []
        for message in messages:
            if message["role"] == "tool":
                self.assertTrue(pending, "Unexpected or duplicate tool reply")
                self.assertEqual(message["tool_call_id"], pending.pop(0))
            else:
                self.assertEqual(pending, [], "Assistant/user message before tool batch closure")
                pending = [item["id"] for item in message.get("tool_calls", [])]
        self.assertEqual(pending, [])

    def test_failure_budget_flows_through_chat_and_persists_closed_history(self):
        client = FakeClient([
            assistant(calls=[call("describe", {"dataset": f"bad-{i}"}, f"failed-{i}"),
                             call("environment", {}, f"stale-{i}")])
            for i in (1, 2, 3)
        ])
        runtime = FailingRuntime()
        session = ChatSession(client, runtime, self.root, 8, None, 2)
        events = []
        result = session.send("检查数据", approve=fixture_approval, on_event=events.append)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(runtime.calls, [("describe", {"dataset": "bad-1"}),
                                         ("describe", {"dataset": "bad-2"})])
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(session.failure_budget, 2)
        self.assertTrue(result["simulated"])
        self.assertEqual([item["ok"] for item in result["observations"]], [False, False])
        last_error = {"type": "ArcPy", "message": "fixture failure 2", "hint": "检查路径 2"}
        self.assertEqual(result["observations"][-1]["error"], last_error)
        self.assertEqual(result["error"]["type"], "ArcPy")
        self.assertIn("连续 2 次", result["error"]["message"])
        self.assertIn("fixture failure 2", result["reply"])
        self.assertEqual(result["error"]["hint"], "检查路径 2")
        self.assertEqual([e["step"] for e in events if e["event"] == "approval_requested"], [1, 2])
        self.assertEqual([e["step"] for e in events if e["event"] == "recovery_exhausted"], [2])
        feedback = [m for m in client.requests[1] if m["role"] == "tool"]
        self.assertEqual(json.loads(feedback[0]["content"])["error"],
                         {"type": "ArcPy", "message": "fixture failure 1", "hint": "检查路径 1"})
        replies = [m for m in session.history if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in replies], ["failed-1", "stale-1", "failed-2", "stale-2"])
        self.assertEqual(json.loads(replies[2]["content"])["error"], last_error)
        for message in (replies[1], replies[3]):
            self.assertEqual(json.loads(message["content"])["error"]["type"], "NotExecuted")
        summary = json.loads((Path(result["run_dir"]) / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary, result)
        saved = json.loads((self.root / "chats" / (session.session_id + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(saved["history"], session.history)
        self.assertEqual(saved["turns"][0]["status"], "failed")
        self.assertEqual(saved["turns"][0]["observations"][-1]["error"], last_error)
        restored = ChatSession(FakeClient([]), MockRuntime(), self.root)
        restored.load(session.session_id)
        self.assertEqual(restored.history, session.history)
        for messages in (*client.requests, session.history, saved["history"], restored.history):
            self.assert_closed_calls(messages)

    def test_chat_default_failure_budget_stops_after_three_failures(self):
        client = FakeClient([assistant(calls=[call("environment", {}, f"failure-{i}")]) for i in range(4)])
        runtime = FailingRuntime()
        session = ChatSession(client, runtime, self.root)
        result = session.send("检查", approve=fixture_approval)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(runtime.calls), 3)
        self.assertEqual(len(client.requests), 3)
        self.assertEqual(session.failure_budget, 3)
        self.assert_closed_calls(session.history)

    def test_chat_preserves_fifth_positional_memory_argument(self):
        memory = MemoryStore(self.root / "custom-memory.json")
        memory.add("默认使用米作为距离单位")
        client = FakeClient([assistant("已读取偏好")])
        session = ChatSession(client, MockRuntime(), self.root, 4, memory)
        self.assertIs(session.memory, memory)
        self.assertEqual(session.send("检查偏好", approve=fixture_approval)["status"], "completed")
        self.assertIn("默认使用米作为距离单位", client.requests[0][-1]["content"])
        self.assertEqual(session.max_steps, 4)
        self.assertEqual(session.failure_budget, 3)
        self.assertFalse((self.root / "memory.json").exists())

    def test_chat_rejects_invalid_failure_budget_before_dispatch(self):
        for value in (0, 21, True, False, 1.0, "3"):
            with self.subTest(value=value):
                client, runtime = FakeClient([]), FailingRuntime()
                session = ChatSession(client, runtime, self.root, failure_budget=value)
                with self.assertRaisesRegex(ValueError, "failure_budget"):
                    session.send("检查", approve=fixture_approval)
                self.assertEqual(client.requests, [])
                self.assertEqual(runtime.calls, [])
        self.assertFalse((self.root / "runs").exists())

    def test_create_session_forwards_configured_failure_budget(self):
        config = self.root / "agent.toml"
        config.write_text('[agent]\nworkspace = "outputs"\nmax_steps = 5\nfailure_budget = 2\n', encoding="utf-8")
        settings = Settings.load(config)
        client = FakeClient([assistant(calls=[call("environment", {}, f"failure-{i}")]) for i in range(3)])
        runtime = FailingRuntime()
        # ProviderStore.client reads credentials; discovery probes Pro; ArcPyRuntime
        # launches workers. Replace only those boundaries, never ChatSession/Agent.
        with patch("arcgis_agent.interfaces.ROOT", self.root), \
                patch("arcgis_agent.interfaces.ProviderStore", autospec=True) as providers, \
                patch("arcgis_agent.interfaces.discover_python", return_value=self.root / "missing/python.exe"), \
                patch("arcgis_agent.interfaces.ArcPyRuntime", return_value=runtime):
            store = providers.return_value
            store.active, store.options = "deepseek", {"deepseek": {}}
            store.client.return_value = client
            session = create_session(settings)
            result = session.send("检查", approve=fixture_approval)
        self.assertIsInstance(session, ChatSession)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(runtime.calls), 2)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(session.failure_budget, 2)
        self.assertEqual(session.max_steps, 5)
        self.assert_closed_calls(session.history)

    def test_create_session_uses_persistent_data_home_when_configured(self):
        data_home = self.root / "persistent-user-data"
        settings = Settings(workspace=self.root / "assembly-cache" / "workspace")
        runtime = FailingRuntime()
        with patch.dict("os.environ", {"ARCGIS_AGENT_DATA_HOME": str(data_home)}), \
                patch("arcgis_agent.interfaces.ProviderStore", autospec=True) as providers, \
                patch("arcgis_agent.interfaces.discover_python", return_value=self.root / "python.exe"), \
                patch("arcgis_agent.interfaces.ArcPyRuntime", return_value=runtime):
            providers.return_value.active = "deepseek"
            providers.return_value.options = {"deepseek": {}}
            providers.return_value.client.return_value = FakeClient([])
            session = create_session(settings)

        expected_home = data_home.resolve()
        expected_workspace = expected_home / "workspace"
        self.assertEqual(session.workspace, expected_workspace)
        self.assertEqual(providers.call_args.args[:2], (expected_home, expected_workspace))

    def test_chat_default_allows_sixteen_successful_steps_but_not_seventeen(self):
        for requested, status in ((16, "completed"), (17, "step_limit")):
            with self.subTest(requested=requested):
                client = FakeClient([
                    *[assistant(calls=[call("environment", {}, f"step-{i}")]) for i in range(requested)],
                    assistant("检查完成"),
                ])
                session = ChatSession(client, MockRuntime(), self.root)
                events = []
                result = session.send("长任务", approve=fixture_approval, on_event=events.append)
                self.assertEqual(result["status"], status)
                self.assertEqual(len(result["observations"]), 16)
                self.assertTrue(all(item["ok"] for item in result["observations"]))
                self.assertTrue(result["simulated"])
                self.assertEqual(len([e for e in events if e["event"] == "tool_executing"]), 16)
                self.assertEqual(len(client.requests), 17)
                self.assert_closed_calls(session.history)

    def test_chat_explicit_shorter_step_limit_is_honored(self):
        for requested, status in ((2, "completed"), (3, "step_limit")):
            with self.subTest(requested=requested):
                client = FakeClient([
                    *[assistant(calls=[call("environment", {}, f"step-{i}")]) for i in range(requested)],
                    assistant("检查完成"),
                ])
                session = ChatSession(client, MockRuntime(), self.root, max_steps=2)
                result = session.send("短任务", approve=fixture_approval)
                self.assertEqual(result["status"], status)
                self.assertEqual(len(result["observations"]), 2)
                self.assertTrue(all(item["ok"] for item in result["observations"]))
                self.assertEqual(len(client.requests), 3)
                self.assert_closed_calls(session.history)

    def test_failed_batch_skips_stale_write_before_model_corrects_dataset(self):
        failure = {"ok": False, "error": {"type": "ArcPy", "message": "ERROR 000732: bad path", "hint": "检查数据路径"}}
        class DescribeRuntime:
            def __init__(self):
                self.calls = []

            def call(self, name, arguments, output_dir, on_progress=None):
                self.calls.append((name, arguments))
                if name != "describe":
                    raise AssertionError("Stale write must never execute")
                if arguments == {"dataset": "bad"}:
                    return failure
                if arguments == {"dataset": "corrected"}:
                    return {"ok": True, "data": {"path": "corrected"}, "simulated": True}
                raise AssertionError("Unexpected dataset")

        client = FakeClient([
            assistant(calls=[call("describe", {"dataset": "bad"}, "bad-id"),
                             call("buffer", {"dataset": "bad", "distance": "1 Meters", "output_name": "stale"}, "stale-id")]),
            assistant(calls=[call("describe", {"dataset": "corrected"}, "corrected-id")]),
            assistant("已修正路径并检查数据，未创建缓冲区。"),
        ])
        runtime = DescribeRuntime()
        session = ChatSession(client, runtime, self.root)
        requests, events = [], []
        def approve(request):
            requests.append(request)
            return {"decision": "allow", "allow_writes": True}
        result = session.send("检查数据后缓冲", approve=approve, on_event=events.append)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reply"], "已修正路径并检查数据，未创建缓冲区。")
        self.assertEqual(runtime.calls, [("describe", {"dataset": "bad"}), ("describe", {"dataset": "corrected"})])
        self.assertEqual([item["ok"] for item in result["observations"]], [False, True])
        self.assertEqual(result["observations"][0]["error"], failure["error"])
        self.assertEqual([request["arguments"]["dataset"] for request in requests], ["bad", "corrected"])
        self.assertNotEqual(requests[0]["request_id"], requests[1]["request_id"])
        self.assertEqual([e["tool"] for e in events if e["event"] == "tool_requested"], ["describe", "describe"])
        self.assertEqual([e["tool"] for e in events if e["event"] == "tool_executing"], ["describe", "describe"])
        self.assertEqual(len(client.requests), 3)
        feedback = [m for m in client.requests[1] if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in feedback], ["bad-id", "stale-id"])
        self.assertEqual(json.loads(feedback[0]["content"]), result["observations"][0])
        skipped = json.loads(feedback[1]["content"])
        self.assertFalse(skipped["ok"])
        self.assertEqual(skipped["error"]["type"], "NotExecuted")
        self.assertIn("未执行", skipped["error"]["message"])
        for messages in (*client.requests, session.history):
            self.assert_closed_calls(messages)
        saved = json.loads((self.root / "chats" / (session.session_id + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(saved["history"], session.history)
        self.assertEqual(saved["turns"][0]["observations"], result["observations"])

    def test_planner_closes_every_pending_call_before_requesting_recovery(self):
        client = FakeClient([
            assistant(calls=[call("describe", {"dataset": "bad"}, "a"),
                             call("environment", {}, "b"), call("describe", {"dataset": "stale"}, "c")]),
            assistant("无法修正。"),
        ])
        planner = DeepSeekPlanner(client, [], "检查")
        action = planner.next_action([])
        self.assertEqual(action.tool, "describe")
        observation = {"tool": "describe", "arguments": {"dataset": "bad"}, "ok": False,
                       "error": {"type": "Timeout", "message": "超时", "hint": "缩小范围"}}
        self.assertIsNone(planner.next_action([observation]))
        self.assertEqual(len(client.requests), 2)
        feedback = [m for m in client.requests[1] if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in feedback], ["a", "b", "c"])
        self.assertEqual(json.loads(feedback[0]["content"]), observation)
        for message in feedback[1:]:
            content = json.loads(message["content"])
            self.assertFalse(content["ok"])
            self.assertEqual(content["error"]["type"], "NotExecuted")
        self.assert_closed_calls(client.requests[1])
        self.assertEqual(planner.pending, [])
        self.assertIsNone(planner.active_call)
        self.assertEqual(planner.seen_observations, 1)

    def test_final_text_after_failed_tool_does_not_claim_completion(self):
        failure = {"ok": False, "error": {"type": "Stalled", "message": "worker stalled", "hint": "检查运行时"}}
        client = FakeClient([assistant(calls=[call("environment", {}, "failed-id")]), assistant("没有完成。")])
        session = ChatSession(client, MockRuntime(), self.root)
        with patch.object(session.runtime, "call", return_value=failure) as dispatch:
            result = session.send("检查", approve=fixture_approval)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], failure["error"])
        self.assertIn("worker stalled", result["reply"])
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(session.history[-1]["content"], "没有完成。")
        self.assertEqual(json.loads(session.history[-2]["content"])["error"], failure["error"])
        self.assert_closed_calls(session.history)

    def test_write_blocked_turn_is_closed_and_kept(self):
        client = FakeClient([assistant(calls=[call("buffer", {"dataset": "x", "distance": "1 Meters", "output_name": "out"})])])
        session = ChatSession(client, MockRuntime(), self.root)
        result = session.send("缓冲", approve=fixture_approval)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("启用", result["reply"])
        # A blocked turn no longer rolls history back: the dangling write call is
        # closed with a synthesized "not executed" tool message so the API sequence
        # stays valid and the model can see why nothing happened next turn.
        self.assertEqual([m["role"] for m in session.history], ["user", "assistant", "tool"])
        content = json.loads(session.history[-1]["content"])
        self.assertFalse(content["ok"])
        self.assertIn("写操作", content["error"]["message"])

    def test_unknown_model_tool_is_rejected(self):
        client = FakeClient([assistant(calls=[call("execute_python", {"code": "x"})])])
        result = ChatSession(client, MockRuntime(), self.root).send("test", approve=fixture_approval)
        self.assertEqual(result["status"], "failed")
        self.assertIn("Unknown tool", result["reply"])

    def test_provider_http_errors_do_not_expose_body_or_key(self):
        key = "example-private-key"
        exc = urllib.error.HTTPError("https://example.com", 401, "Unauthorized", {}, io.BytesIO(key.encode()))
        with patch("urllib.request.urlopen", side_effect=exc):
            with self.assertRaises(DeepSeekError) as ctx:
                DeepSeekClient(key).complete([])
        exc.close()
        self.assertNotIn(key, str(ctx.exception))
        self.assertIn("401", str(ctx.exception))

    def test_api_request_has_model_and_tool_schemas(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({"choices": [{"message": assistant("ok")}]}).encode()
        with patch("urllib.request.urlopen", return_value=Response()) as mocked:
            self.assertEqual(DeepSeekClient("secret").complete([])["content"], "ok")
            request = mocked.call_args.args[0]
            payload = json.loads(request.data)
            self.assertEqual(payload["model"], "deepseek-flash")
            self.assertIn("buffer", [tool["function"]["name"] for tool in payload["tools"]])

    def test_key_precedence_and_no_leak(self):
        (self.root / ".env.local").write_text("DEEPSEEK_API_KEY=from_file\n", encoding="utf-8")
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "from_environment"}):
            self.assertEqual(read_api_key(self.root), "from_environment")
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(read_api_key(self.root), "from_file")

    def test_setup_key_preserves_other_env_entries_and_hides_key(self):
        target = self.root / ".env.local"
        target.write_text("OTHER=value\nDEEPSEEK_API_KEY=old\n", encoding="utf-8")
        out = io.StringIO()
        with patch("arcgis_agent.interfaces.ROOT", self.root), patch("getpass.getpass", return_value="new-secret"), patch("sys.stdout", out):
            self.assertEqual(setup_key(), 0)
        self.assertEqual(target.read_text(encoding="utf-8"), "OTHER=value\nDEEPSEEK_API_KEY=new-secret\n")
        self.assertNotIn("new-secret", out.getvalue())

    def test_rpc_streams_progress_then_result_and_reset(self):
        client = FakeClient([assistant(calls=[call("environment", {})]), assistant("完成")])
        session = ChatSession(client, MockRuntime(), self.root)
        incoming = RPCInput({"type": "send", "message": "test"})
        def handler(message, pipe):
            if message["type"] == "event" and message["event"]["event"] == "approval_requested":
                pipe.put({"type": "approval_response", "request_id": message["event"]["request_id"], "response": {"decision": "allow"}})
        outgoing = RPCOutput(incoming, handler)
        with patch("arcgis_agent.interfaces.create_session", return_value=session), patch("sys.stdin", incoming), patch("sys.stdout", outgoing):
            self.assertEqual(rpc(Settings(workspace=self.root)), 0)
        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual(messages[0]["type"], "ready")
        self.assertIn("tool_requested", [m["event"]["event"] for m in messages if m["type"] == "event"])
        self.assertEqual(messages[-1]["result"]["reply"], "完成")
        outgoing = io.StringIO()
        with patch("arcgis_agent.interfaces.create_session", return_value=session), patch("sys.stdin", io.StringIO('{"type":"reset"}\n')), patch("sys.stdout", outgoing):
            self.assertEqual(rpc(Settings(workspace=self.root)), 0)
        self.assertEqual(json.loads(outgoing.getvalue().splitlines()[-1])["type"], "reset")


if __name__ == "__main__":
    unittest.main()
