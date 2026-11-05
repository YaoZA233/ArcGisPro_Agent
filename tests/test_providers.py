from approval_fixtures import fixture_approval
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch, Mock

from arcgis_agent.chat import ChatSession, usage_numbers
from arcgis_agent.cli import main
from arcgis_agent.config import Settings
from arcgis_agent.deepseek import Cancelled, DeepSeekError
from arcgis_agent.interfaces import provider_state, rpc, select_provider
from arcgis_agent.providers import (PRESETS, AnthropicClient, CompatibleClient, NoRedirect,
    ProviderStore, get_key, key_name, preset_profile, save_key, validate_profile, validate_url)
from arcgis_agent.runtime import MockRuntime


def sse(*chunks):
    return io.BytesIO("".join("data: " + json.dumps(c, ensure_ascii=False) + "\n\n" for c in chunks).encode("utf-8"))


def cc_stream(content="完成", calls=None, usage=None):
    delta = {"content": content}
    if calls:
        delta["tool_calls"] = calls
    return sse({"model": "actual-model", "choices": [{"index": 0, "delta": delta, "finish_reason": "tool_calls" if calls else "stop"}]},
               {"choices": [], "usage": usage or {"prompt_tokens": 12, "completion_tokens": 3}})


def claude_stream(blocks, stop="end_turn"):
    chunks = [{"type": "message_start", "message": {"model": "claude-actual", "usage": {"input_tokens": 5, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2, "output_tokens": 1}}}]
    for index, block in enumerate(blocks):
        chunks.append({"type": "content_block_start", "index": index, "content_block": block})
        chunks.append({"type": "content_block_stop", "index": index})
    chunks.extend([{"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 6}}, {"type": "message_stop"}])
    return sse(*chunks)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.settings = Settings(workspace=self.root / "workspace")
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def store(self):
        return ProviderStore(self.root, self.settings.workspace, self.settings)

    def test_all_requested_presets_exist_and_protocols_differ(self):
        self.assertEqual(set(PRESETS), {"deepseek", "qwen", "kimi", "glm", "openai", "claude", "grok", "custom"})
        for name in PRESETS:
            validate_profile(preset_profile(name))
        self.assertEqual(preset_profile("claude")["protocol"], "anthropic")

    def test_custom_profile_roundtrip_multiple_accounts_and_no_secret_in_metadata(self):
        store = self.store()
        for ident in ("relay-one", "relay-two"):
            store.save({**preset_profile("custom", ident), "base_url": "https://relay.example/v1", "model": "org/model"}, "example-private-value")
        store.select("relay-two")
        restored = self.store()
        self.assertEqual(restored.active, "relay-two")
        self.assertEqual(restored.client().api_key, "example-private-value")
        self.assertNotIn("example-private-value", restored.path.read_text(encoding="utf-8"))
        self.assertNotIn("example-private-value", json.dumps(restored.public()))
        self.assertNotEqual(key_name("relay-one"), key_name("relay_one"))

    def test_provider_keys_are_isolated_and_env_has_precedence(self):
        save_key(self.root, "deepseek", "deepseek-private")
        save_key(self.root, "qwen", "qwen-private")
        self.assertEqual(get_key(self.root, "qwen"), "qwen-private")
        self.assertEqual(get_key(self.root, "kimi"), "")
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "environment-value"}):
            self.assertEqual(get_key(self.root, "qwen"), "environment-value")
        self.assertEqual(get_key(self.root, "deepseek"), "deepseek-private")

    def test_invalid_profile_does_not_write_secret_or_profile(self):
        store = self.store()
        for change in ({"id": "../bad"}, {"base_url": "http://remote.example/v1"}, {"vision": "yes"},
                       {"api_key": "forbidden"}, {"model": "bad model"}, {"reasoning": "anthropic"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                store.save({**preset_profile("custom"), **change}, "private")
        self.assertFalse((self.root / ".env.local").exists())
        self.assertFalse(store.path.exists())

    def test_url_rules_and_no_redirect_credential_forwarding(self):
        for url in ("http://example.com/v1", "https://u:p@example.com/v1", "https://example.com?v=key",
                    "https://example.com/#secret", "https://example.com:bad", "https://example.com/\r\n"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
        self.assertEqual(validate_url("http://127.0.0.1:1234/v1/"), "http://127.0.0.1:1234/v1")
        with self.assertRaises(DeepSeekError):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example")

    def test_http_errors_are_sanitized_and_missing_key_is_local_error(self):
        client = CompatibleClient("private", preset_profile("qwen"))
        response = urllib.error.HTTPError(client.base_url, 401, "private", {}, io.BytesIO(b"private"))
        opener = Mock()
        opener.open.side_effect = response
        with patch("urllib.request.build_opener", return_value=opener), self.assertRaises(DeepSeekError) as error:
            client.list_models()
        self.assertIn("401", str(error.exception))
        self.assertNotIn("private", str(error.exception))
        client.api_key = ""
        with patch("urllib.request.build_opener") as opened, self.assertRaises(DeepSeekError):
            client.list_models()
        opened.assert_not_called()

    def test_profile_deletion_survives_restart_and_cannot_delete_active(self):
        store = self.store()
        with self.assertRaises(ValueError):
            store.delete("deepseek")
        store.delete("qwen")
        self.assertNotIn("qwen", self.store().profiles)

    def test_each_connection_retains_its_model_and_parameters(self):
        store = self.store()
        store.save_options({"model": "deepseek-v4-pro", "thinking": "low", "max_tokens": 1024})
        store.select("qwen")
        store.save_options({"model": "qwen-vl-plus", "thinking": "off", "max_tokens": 4096})
        store.select("deepseek")
        restored = self.store().client()
        self.assertEqual((restored.model, restored.thinking, restored.max_tokens), ("deepseek-v4-pro", "low", 1024))
        store.save({**store.profiles["deepseek"], "model": "deepseek-flash"})
        self.assertEqual(store.client().model, "deepseek-flash")

    def test_cli_provider_contract_save_use_list_delete(self):
        profile = self.root / "relay.json"
        profile.write_text(json.dumps(preset_profile("custom", "my-relay")), encoding="utf-8")
        with patch("arcgis_agent.config.Settings.load", return_value=self.settings), patch("arcgis_agent.interfaces.ROOT", self.root):
            for args in (["providers", "presets"], ["providers", "save", str(profile)], ["providers", "use", "my-relay"], ["providers", "list"]):
                output = io.StringIO()
                with patch("sys.stdout", output):
                    self.assertEqual(main(args), 0)
                json.loads(output.getvalue())
            self.assertEqual(self.store().active, "my-relay")
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(main(["providers", "delete", "my-relay"]), 2)

    def test_rpc_can_configure_and_switch_without_any_api_key(self):
        store = self.store()
        session = ChatSession(store.client(), MockRuntime(), self.settings.workspace)
        session.providers = store
        requests = [{"type": "provider_save", "profile": preset_profile("custom", "test-relay"), "api_key": "test-private", "activate": True},
                    {"type": "configure", "options": {"model": "custom-model", "thinking": "off"}},
                    {"type": "provider_select", "provider": "claude"}, {"type": "providers"}]
        output = io.StringIO()
        with patch("arcgis_agent.interfaces.create_session", return_value=session), patch("sys.stdin", io.StringIO("\n".join(json.dumps(r) for r in requests))), patch("sys.stdout", output):
            self.assertEqual(rpc(self.settings), 0)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([r["type"] for r in records], ["ready", "providers", "configured", "providers", "providers"])
        self.assertEqual(records[-1]["provider"], "claude")
        self.assertFalse(records[-1]["key_configured"])
        self.assertNotIn("test-private", output.getvalue())
        select_provider(session, "test-relay", self.settings)
        self.assertEqual(session.client.model, "custom-model")


class ProtocolTests(unittest.TestCase):
    def client(self, preset="qwen", **changes):
        profile = {**preset_profile(preset), **changes}
        cls = AnthropicClient if profile["protocol"] == "anthropic" else CompatibleClient
        return cls("private", profile)

    def test_slow_stream_stops_at_the_wall_clock_limit(self):
        for preset, stream, method in (("qwen", cc_stream("慢"), "_stream"),
                                       ("claude", claude_stream([{"type": "text", "text": "慢"}]), "_stream_anthropic")):
            client = self.client(preset)
            client.stream_limit = 0
            with self.subTest(preset=preset), self.assertRaises(DeepSeekError) as refused:
                getattr(client, method)(stream)
            self.assertIn("总时长", str(refused.exception))

    def test_provider_specific_reasoning_payloads(self):
        expected = {"deepseek": ("thinking", {"type": "enabled"}), "qwen": ("enable_thinking", True),
                    "kimi": ("thinking", {"type": "enabled"}), "glm": ("thinking", {"type": "enabled"}),
                    "openai": ("reasoning_effort", "high")}
        for name, (field, value) in expected.items():
            with self.subTest(provider=name):
                client = self.client(name, **({"reasoning": "openai"} if name == "openai" else {}))
                client.configure({"thinking": "high"})
                payload = client._payload([{"role": "user", "content": "hello"}], True)
                self.assertEqual(payload[field], value)
                self.assertIn("tools", payload)
                if name == "openai":
                    self.assertIn("max_completion_tokens", payload)
                    self.assertNotIn("max_tokens", payload)
        generic = self.client("custom", stream_usage=False, tools=False)
        payload = generic._payload([], True)
        for key in ("thinking", "enable_thinking", "reasoning_effort", "tools", "stream_options"):
            self.assertNotIn(key, payload)

    def test_compatible_request_and_model_discovery(self):
        client = self.client("grok")
        with patch.object(client, "_open", return_value=io.BytesIO(b'{"data":[{"id":"b"},{"id":"a"}]}')) as opened:
            self.assertEqual(client.list_models(), ["a", "b"])
            req = opened.call_args.args[0]
            self.assertEqual(req.full_url, "https://api.x.ai/v1/models")
            self.assertEqual(req.get_header("Authorization"), "Bearer private")
        client.on_event = lambda e: None
        with patch.object(client, "_open", return_value=cc_stream()) as opened:
            self.assertEqual(client.complete([])["content"], "完成")
            self.assertEqual(opened.call_args.args[0].full_url, "https://api.x.ai/v1/chat/completions")
            self.assertEqual(client.actual_model, "actual-model")

    def test_all_compatible_providers_complete_tool_loop(self):
        for name in ("deepseek", "qwen", "kimi", "glm", "openai", "grok", "custom"):
            with self.subTest(provider=name), tempfile.TemporaryDirectory() as temp:
                client = self.client(name)
                call = {"index": 0, "id": "t1", "type": "function", "function": {"name": "environment", "arguments": "{}"}}
                with patch.object(client, "_open", side_effect=[cc_stream("", [call]), cc_stream()]) as opened:
                    result = ChatSession(client, MockRuntime(), Path(temp)).send("检查环境", approve=fixture_approval)
                    self.assertEqual(result["status"], "completed")
                    self.assertTrue(result["simulated"])
                    self.assertEqual(result["provider"], name)
                    self.assertEqual(result["usage"]["input"], 24)
                    self.assertEqual(json.loads(opened.call_args.args[0].data)["messages"][-1]["role"], "tool")

    def test_tool_disabled_and_truncated_output_never_execute(self):
        call = {"index": 0, "id": "t1", "type": "function", "function": {"name": "environment", "arguments": "{}"}}
        client = self.client("custom", tools=False)
        client.on_event = lambda e: None
        with patch.object(client, "_open", return_value=cc_stream("", [call])), self.assertRaises(DeepSeekError):
            client.complete([])
        client = self.client("qwen")
        client.on_event = lambda e: None
        with patch.object(client, "_open", return_value=sse({"choices": [{"delta": {"tool_calls": [call]}}]})), self.assertRaises(DeepSeekError):
            client.complete([])

    def test_anthropic_system_images_tools_and_reasoning_budget(self):
        client = self.client("claude")
        client.configure({"thinking": "high", "max_tokens": 2048})
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": [{"type": "text", "text": "describe"},
                     {"type": "image_url", "image_url": {"url": "data:image/png;base64,YWJj", "detail": "high"}}]}]
        payload = client._payload(messages, True)
        self.assertEqual(payload["system"], "rules")
        self.assertEqual(payload["messages"][0]["content"][1]["source"]["media_type"], "image/png")
        self.assertEqual(payload["thinking"]["budget_tokens"], 2047)
        self.assertIn("input_schema", payload["tools"][0])
        self.assertNotIn("stream_options", payload)
        client.max_tokens = 1024
        with self.assertRaises(ValueError):
            client._payload(messages, True)

    def test_anthropic_fragmented_json_reasoning_signature_and_usage(self):
        client = self.client("claude")
        events = []
        client.on_event = events.append
        response = sse(
            {"type": "message_start", "message": {"model": "claude-actual", "usage": {"input_tokens": 5, "cache_read_input_tokens": 3, "output_tokens": 1}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "检查坐标系"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "signed-block"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "t", "name": "describe", "input": {}}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"dataset":'}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '"C:/a.gdb/roads"}'}},
            {"type": "content_block_stop", "index": 1},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9}}, {"type": "message_stop"})
        with patch.object(client, "_open", return_value=response) as opened:
            message = client.complete([])
        self.assertEqual(opened.call_args.args[0].get_header("X-api-key"), "private")
        self.assertEqual(opened.call_args.args[0].get_header("Anthropic-version"), "2023-06-01")
        self.assertIsNone(opened.call_args.args[0].get_header("Authorization"))
        self.assertEqual(message["_anthropic_content"][0]["signature"], "signed-block")
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"]), {"dataset": "C:/a.gdb/roads"})
        self.assertEqual(client.last_usage, {"prompt_tokens": 8, "prompt_cache_hit_tokens": 3, "completion_tokens": 9})
        self.assertEqual([e["text"] for e in events if e["event"] == "reasoning_delta"], ["检查坐标系"])
        payload = client._payload([message, {"role": "tool", "tool_call_id": "t", "content": "result"}], True)
        self.assertEqual(payload["messages"][0]["content"][0]["signature"], "signed-block")
        self.assertEqual(payload["messages"][1]["content"][0]["tool_use_id"], "t")

    def test_anthropic_multiple_tool_results_are_grouped_and_can_continue(self):
        client = self.client("claude")
        blocks = [{"type": "tool_use", "id": "a", "name": "environment", "input": {}},
                  {"type": "tool_use", "id": "b", "name": "describe", "input": {"dataset": "x"}}]
        with tempfile.TemporaryDirectory() as temp, patch.object(client, "_open", side_effect=[claude_stream(blocks, "tool_use"), claude_stream([{"type": "text", "text": "已检查"}])]) as opened:
            result = ChatSession(client, MockRuntime(), Path(temp)).send("inspect", approve=fixture_approval)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["reply"], "已检查")
            self.assertEqual(result["usage"], {"input": 20, "output": 12, "cache_hit": 6, "reasoning": 0})
            final = json.loads(opened.call_args.args[0].data)["messages"][-1]
            self.assertEqual(final["role"], "user")
            self.assertEqual([b["tool_use_id"] for b in final["content"]], ["a", "b"])

    def test_anthropic_cancel_truncate_and_error_are_failures(self):
        client = self.client("claude")
        client.on_event = lambda e: None
        for response in (sse({"type": "message_start", "message": {}}),
                         claude_stream([{"type": "text", "text": "partial"}], "max_tokens"),
                         sse({"type": "error", "error": {"message": "private"}})):
            with patch.object(client, "_open", return_value=response), self.assertRaises(DeepSeekError) as error:
                client.complete([])
            self.assertNotIn("private", str(error.exception))
        client.cancel_event.set()
        with patch.object(client, "_open") as opened, self.assertRaises(Cancelled):
            client.complete([])
        opened.assert_not_called()

    def test_anthropic_nonstream_and_paginated_models(self):
        client = self.client("claude")
        body = {"model": "claude-actual", "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {"input_tokens": 3, "output_tokens": 2}}
        with patch.object(client, "_open", return_value=io.BytesIO(json.dumps(body).encode())):
            self.assertEqual(client.complete([])["content"], "ok")
        pages = [{"data": [{"id": "a"}], "has_more": True, "last_id": "a"}, {"data": [{"id": "b"}], "has_more": False}]
        with patch.object(client, "_open", side_effect=[io.BytesIO(json.dumps(p).encode()) for p in pages]) as opened:
            self.assertEqual(client.list_models(), ["a", "b"])
            self.assertIn("after_id=a", opened.call_args.args[0].full_url)

    def test_provider_switch_strips_native_metadata_preserves_text_and_rejects_old_images(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.client("claude")
            session = ChatSession(client, MockRuntime(), Path(temp))
            session.history = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,YWJj"}}]},
                {"role": "assistant", "content": "old answer", "reasoning_content": "old reasoning", "_anthropic_content": [{"type": "thinking", "signature": "old-signature"}]}]
            other = self.client("qwen")
            session.switch_client(other)
            with patch.object(other, "_open", return_value=cc_stream()) as opened:
                result = session.send("continue", approve=fixture_approval)
            self.assertEqual(result["status"], "completed")
            messages = json.loads(opened.call_args.args[0].data)["messages"]
            self.assertNotIn("image_url", json.dumps(messages))
            self.assertNotIn("old-signature", json.dumps(messages))
            self.assertEqual(messages[2]["content"], "old answer")

    def test_cache_usage_for_openai_and_deepseek_formats(self):
        self.assertEqual(usage_numbers({"prompt_tokens": 10, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 4}})["cache_hit"], 4)
        self.assertEqual(usage_numbers({"prompt_cache_hit_tokens": 3})["cache_hit"], 3)

    def test_reselect_same_claude_connection_preserves_signed_history(self):
        with tempfile.TemporaryDirectory() as temp:
            session = ChatSession(self.client("claude"), MockRuntime(), Path(temp))
            block = {"type": "thinking", "thinking": "reason", "signature": "valid-signature"}
            session.history = [{"role": "assistant", "content": "answer", "_anthropic_content": [block]}]
            session.switch_client(self.client("claude"))
            self.assertEqual(session.history[0]["_anthropic_content"][0]["signature"], "valid-signature")

    def test_model_change_drops_old_signature_even_after_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.client("claude")
            session = ChatSession(client, MockRuntime(), Path(temp))
            session.history = [{"role": "user", "content": "old question"}, {"role": "assistant", "content": "old answer",
                "_anthropic_content": [{"type": "thinking", "signature": "old-signature"}]}]
            session._save()
            restored = ChatSession(self.client("claude"), MockRuntime(), Path(temp))
            restored.load(session.session_id)
            with patch.object(restored.client, "_open", return_value=claude_stream([{"type": "text", "text": "continued"}])) as opened:
                result = restored.send("continue", approve=fixture_approval, options={"model": "another-claude-model"})
            self.assertEqual(result["status"], "completed")
            self.assertNotIn("old-signature", opened.call_args.args[0].data.decode())

    def test_compatible_nonstream_cannot_bypass_disabled_tools(self):
        client = self.client("custom", tools=False)
        body = {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "environment", "arguments": "{}"}}]}}]}
        with patch.object(client, "_open", return_value=io.BytesIO(json.dumps(body).encode())), self.assertRaises(DeepSeekError):
            client.complete([])


if __name__ == "__main__":
    unittest.main()
