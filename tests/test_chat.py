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
from arcgis_agent.interfaces import rpc, setup_key
from arcgis_agent.config import Settings
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
        result = ChatSession(client, MockRuntime(), self.root).send("检查", approve=fixture_approval)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([m["tool_call_id"] for m in client.requests[1] if m["role"] == "tool"], ["a", "b"])

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
