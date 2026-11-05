from approval_fixtures import fixture_approval
import base64
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from arcgis_agent.attachments import prepare_attachments
from arcgis_agent.chat import ChatSession
from arcgis_agent.deepseek import Cancelled, DeepSeekClient, DeepSeekError
from arcgis_agent.runtime import MockRuntime


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def stream(*chunks):
    lines = [("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode("utf-8") for chunk in chunks]
    return Response(b"".join(lines) + b"data: [DONE]\n\n")


def chunk(delta, finish=None):
    return {"model": "deepseek-flash", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_stream_assembles_unicode_reasoning_tools_and_usage(self):
        client = DeepSeekClient("private")
        events = []
        client.on_event = events.append
        response = stream(chunk({"reasoning_content": "先检查数据"}),
            chunk({"tool_calls": [{"index": 0, "id": "abc", "function": {"name": "describe", "arguments": '{"data'}}]}),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'set":"湖南"}'}}]}, "tool_calls"),
            {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 12}})
        with patch("urllib.request.urlopen", return_value=response) as request:
            message = client.complete([])
        self.assertEqual(message["reasoning_content"], "先检查数据")
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"]), {"dataset": "湖南"})
        self.assertEqual(client.last_usage["prompt_tokens"], 120)
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertEqual(payload["stream_options"], {"include_usage": True})
        self.assertIn("reasoning_delta", [e["event"] for e in events])

    def test_incomplete_tool_stream_is_never_executed(self):
        client = DeepSeekClient("secret")
        client.on_event = lambda _: None
        with patch("urllib.request.urlopen", return_value=stream(chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{"}}]}))):
            with self.assertRaisesRegex(DeepSeekError, "中断"):
                client.complete([])

    def test_truncated_output_is_failed(self):
        client = DeepSeekClient("secret")
        client.on_event = lambda _: None
        with patch("urllib.request.urlopen", return_value=stream(chunk({"content": "partial"}, "length"))):
            with self.assertRaisesRegex(DeepSeekError, "上限"):
                client.complete([])

    def test_disabled_thinking_request_and_cancel(self):
        client = DeepSeekClient("secret")
        client.configure({"thinking": "off", "max_tokens": 256})
        with patch("urllib.request.urlopen", return_value=Response(b'{"choices":[{"message":{"role":"assistant","content":"ok"}}]}')) as request:
            client.complete([])
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", payload)
        client.cancel_event.set()
        with self.assertRaises(Cancelled):
            client.complete([])

    def test_attachment_types_and_vision_guard(self):
        text = self.root / "data.csv"
        text.write_text("name,value\n湖南,1", encoding="utf-8")
        image = self.root / "map.png"
        image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jC7sAAAAASUVORK5CYII="))
        parts, meta = prepare_attachments([str(text), str(image)], "deepseek-flash", "low")
        self.assertEqual(meta[0]["kind"], "text")
        self.assertEqual(parts[1]["image_url"]["detail"], "low")
        with self.assertRaisesRegex(ValueError, "图片"):
            prepare_attachments([str(image)], "deepseek-v4-pro")
        env = self.root / ".env.local"
        env.write_text("secret", encoding="utf-8")
        with self.assertRaises(ValueError):
            prepare_attachments([str(env)], "deepseek-flash")

    def test_attachment_size_and_file_signature_checks(self):
        image = self.root / "fake.png"
        image.write_text("not an image", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不一致"):
            prepare_attachments([str(image)], "deepseek-flash")
        with self.assertRaises(ValueError):
            prepare_attachments(["x"] * 7, "deepseek-flash")
        large = self.root / "large.txt"
        large.write_text("x" * (257 * 1024), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "超过"):
            prepare_attachments([str(large)], "deepseek-flash")

    def test_persistence_uses_real_usage_and_can_resume(self):
        client = DeepSeekClient("not-written-to-disk")
        session = ChatSession(client, MockRuntime(), self.root)
        first = stream(chunk({"reasoning_content": "检验数据"}), chunk({"content": "完成"}, "stop"), {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 5, "prompt_cache_hit_tokens": 20}})
        with patch("urllib.request.urlopen", return_value=first):
            result = session.send("检查", approve=fixture_approval)
        self.assertEqual(result["usage"]["input"], 50)
        self.assertTrue(result["usage_available"])
        saved = self.root / "chats" / (session.session_id + ".json")
        self.assertNotIn("not-written-to-disk", saved.read_text(encoding="utf-8"))
        other = ChatSession(DeepSeekClient("other"), MockRuntime(), self.root)
        loaded = other.load(session.session_id)
        self.assertEqual(loaded["turns"][0]["reply"], "完成")
        self.assertEqual(loaded["turns"][0]["reasoning"], "检验数据")
        self.assertEqual(other.total_usage["cache_hit"], 20)
        self.assertEqual(len(other.list_chats()), 1)
        with self.assertRaises(ValueError):
            other.load("../outside")

    def test_cancel_does_not_commit_partial_history(self):
        client = DeepSeekClient("secret")
        client.cancel_event.set()
        session = ChatSession(client, MockRuntime(), self.root)
        result = session.send("cancel", approve=fixture_approval)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(session.history, [])

    def test_invalid_options_rejected(self):
        client = DeepSeekClient("secret")
        for options in ({"thinking": "unknown"}, {"max_tokens": True}, {"model": "bad model"}, {"image_detail": "x"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                client.configure(options)

    def test_old_images_are_bounded_before_request(self):
        client = DeepSeekClient("secret")
        session = ChatSession(client, MockRuntime(), self.root)
        image = {"type": "image_url", "image_url": {"url": "0123456789"}}
        session.history = [{"role": "user", "content": [image]}, {"role": "assistant", "content": "first"},
                           {"role": "user", "content": [image]}, {"role": "assistant", "content": "second"}]
        response = stream(chunk({"content": "ok"}, "stop"), {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
        with patch("arcgis_agent.chat.PRIOR_IMAGE_BUDGET", 10), patch("urllib.request.urlopen", return_value=response) as mocked:
            session.send("继续", approve=fixture_approval)
        messages = json.loads(mocked.call_args.args[0].data)["messages"]
        self.assertEqual(messages[1]["content"][0]["type"], "text")
        self.assertEqual(messages[3]["content"][0]["type"], "image_url")


if __name__ == "__main__":
    unittest.main()
