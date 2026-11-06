"""Offline tests for long-term memory, the remember tool, history repair and session resume."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arcgis_agent.chat import ChatSession
from arcgis_agent.interfaces import resolve_session_id
from arcgis_agent.memory import MemoryStore, execute_memory_tool, memory_block
from arcgis_agent.runtime import ArcPyRuntime, InProcessRuntime, MockRuntime
from arcgis_agent.tools import validate_call
from approval_fixtures import fixture_approval
from test_chat import FakeClient, assistant, call


def deny_approval(request):
    return {"decision": "deny"}


class FailingRuntime:
    """Returns a structured tool failure so the repair path sees a real observation."""

    def __init__(self):
        self.calls = 0

    def call(self, name, arguments, output_dir, on_progress=None):
        self.calls += 1
        return {"ok": False, "error": {"type": "ValueError", "message": "GIS dataset does not exist: x"}}

    def export_selection(self, snapshot, arguments, output_dir, on_progress=None):
        return self.call("export_selection", arguments, output_dir)


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = MemoryStore(Path(self.temp.name) / "memory.json")

    def test_add_read_and_render_in_order(self):
        self.assertEqual(self.store.add("常用数据在 D:/gis")["status"], "added")
        self.assertEqual(self.store.add("成果坐标系偏好 CGCS2000")["status"], "added")
        self.assertEqual([item["text"] for item in self.store.entries()],
                         ["常用数据在 D:/gis", "成果坐标系偏好 CGCS2000"])
        self.assertEqual(self.store.render(), "- 常用数据在 D:/gis\n- 成果坐标系偏好 CGCS2000")

    def test_exact_duplicates_are_rejected_without_growth(self):
        self.store.add("事实 A")
        result = self.store.add("事实 A")
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(len(self.store.entries()), 1)

    def test_invalid_text_is_rejected(self):
        for text in ("", "   ", "x" * 501, None):
            with self.assertRaises(ValueError):
                self.store.add(text)

    def test_entry_cap_drops_oldest(self):
        with patch("arcgis_agent.memory.MAX_ENTRIES", 3):
            for index in range(5):
                self.store.add(f"事实 {index}")
        self.assertEqual([item["text"] for item in self.store.entries()], ["事实 2", "事实 3", "事实 4"])

    def test_remove_is_one_based_and_range_checked(self):
        self.store.add("事实 A")
        self.store.add("事实 B")
        self.assertEqual(self.store.remove(1)["removed"], "事实 A")
        self.assertEqual([item["text"] for item in self.store.entries()], ["事实 B"])
        for number in (0, 2, "1"):
            with self.assertRaises(ValueError):
                self.store.remove(number)

    def test_corrupt_or_missing_file_reads_as_empty(self):
        self.assertEqual(self.store.entries(), [])
        self.store.path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.store.entries(), [])
        self.assertEqual(self.store.render(), "")

    def test_render_keeps_newest_within_budget(self):
        for index in range(10):
            self.store.add(f"事实{index}：" + "x" * 40)
        rendered = self.store.render(limit=150)
        self.assertIn("更早的记忆已截断", rendered.splitlines()[0])
        self.assertIn("事实9", rendered)
        self.assertNotIn("事实0：", rendered)

    def test_execute_memory_tool_requires_a_store(self):
        with self.assertRaises(ValueError):
            execute_memory_tool("remember", {"text": "x"}, None)
        with self.assertRaises(ValueError):
            execute_memory_tool("forget", {}, self.store)

    def test_memory_block_is_empty_without_entries(self):
        self.assertEqual(memory_block(self.store), "")
        self.assertEqual(memory_block(None), "")
        self.store.add("事实 A")
        block = memory_block(self.store)
        self.assertIn("[长期记忆]", block)
        self.assertIn("- 事实 A", block)


class RememberToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MemoryStore(self.root / "memory.json")

    def test_schema_contract(self):
        validate_call("remember", {"text": "常用数据在 D:/gis"})
        for arguments in ({}, {"text": ""}, {"text": "x", "extra": 1}):
            with self.assertRaises(ValueError):
                validate_call("remember", arguments)

    def test_mock_runtime_writes_real_memory(self):
        result = MockRuntime(memory=self.store).call("remember", {"text": "事实 A"}, self.root)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["status"], "added")
        self.assertEqual([item["text"] for item in self.store.entries()], ["事实 A"])

    def test_missing_store_returns_structured_error(self):
        result = MockRuntime().call("remember", {"text": "事实 A"}, self.root)
        self.assertFalse(result["ok"])
        self.assertIn("记忆", result["error"]["message"])

    def test_in_process_runtime_handles_remember_offline(self):
        result = InProcessRuntime(memory=self.store).call("remember", {"text": "事实 A"}, self.root)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["status"], "added")

    def test_arcpy_runtime_never_spawns_a_worker_for_remember(self):
        runtime = ArcPyRuntime(Path("nonexistent-python.exe"), memory=self.store)
        result = runtime.call("remember", {"text": "事实 A"}, self.root)
        self.assertTrue(result["ok"])
        self.assertEqual([item["text"] for item in self.store.entries()], ["事实 A"])


class HistoryRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def send(self, client, runtime=None, approve=fixture_approval, **kwargs):
        return ChatSession(client, runtime or MockRuntime(), self.root).send("任务", approve=approve, **kwargs)

    def test_denied_turn_stays_in_history_with_reason(self):
        client = FakeClient([assistant(calls=[call("describe", {"dataset": "x"})]), assistant("第二轮。")])
        session = ChatSession(client, MockRuntime(), self.root)
        result = session.send("任务", approve=deny_approval)
        self.assertEqual(result["status"], "denied")
        self.assertEqual([m["role"] for m in session.history], ["user", "assistant", "tool"])
        content = json.loads(session.history[-1]["content"])
        self.assertFalse(content["ok"])
        self.assertIn("拒绝", content["error"]["message"])
        session.send("继续", approve=fixture_approval)
        roles = [m["role"] for m in client.requests[1]]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "user"])

    def test_blocked_write_turn_stays_in_history(self):
        client = FakeClient([assistant(calls=[call("buffer", {"dataset": "x", "distance": "1 Meters", "output_name": "out"})])])
        session = ChatSession(client, MockRuntime(), self.root)
        result = session.send("任务", approve=fixture_approval)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual([m["role"] for m in session.history], ["user", "assistant", "tool"])
        content = json.loads(session.history[-1]["content"])
        self.assertFalse(content["ok"])
        self.assertIn("写操作", content["error"]["message"])

    def test_failed_tool_turn_keeps_the_real_error_observation(self):
        client = FakeClient([assistant(calls=[call("describe", {"dataset": "x"}, ident)])
                             for ident in ("first", "retry", "last")])
        runtime = FailingRuntime()
        session = ChatSession(client, runtime, self.root)
        result = session.send("任务", approve=fixture_approval)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "ValueError")
        self.assertIn("连续 3 次", result["error"]["message"])
        self.assertEqual(runtime.calls, 3)
        self.assertEqual(len(client.requests), 3)
        self.assertEqual([m["tool_call_id"] for m in session.history if m["role"] == "tool"], ["first", "retry", "last"])
        content = json.loads(session.history[-1]["content"])
        self.assertFalse(content["ok"])
        self.assertEqual(content["error"]["message"], "GIS dataset does not exist: x")
        self.assertEqual(content, result["observations"][-1])

    def test_unknown_tool_failure_keeps_exchange(self):
        client = FakeClient([assistant(calls=[call("execute_python", {"code": "x"})])])
        session = ChatSession(client, MockRuntime(), self.root)
        result = session.send("任务", approve=fixture_approval)
        self.assertEqual(result["status"], "failed")
        self.assertEqual([m["role"] for m in session.history], ["user", "assistant", "tool"])
        self.assertIn("Unknown tool", session.history[-1]["content"])

    def test_rejected_multi_call_batch_closes_every_dangling_id(self):
        client = FakeClient([assistant(calls=[call("environment", {}, "a"), call("describe", {"dataset": "x"}, "b")])])
        session = ChatSession(client, MockRuntime(), self.root)
        result = session.send("任务", approve=deny_approval)
        self.assertEqual(result["status"], "denied")
        tool_messages = [m for m in session.history if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tool_messages], ["a", "b"])

    def test_completed_turn_history_is_unchanged_in_shape(self):
        client = FakeClient([assistant(calls=[call("environment", {})]), assistant("完成")])
        session = ChatSession(client, MockRuntime(), self.root)
        result = session.send("任务", approve=fixture_approval)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([m["role"] for m in session.history], ["user", "assistant", "tool", "assistant"])


class MemoryInjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MemoryStore(self.root / "memory.json")
        self.store.add("常用数据在 D:/gis")

    def user_text(self, request):
        # The newest user message; earlier turns stay in history (turn 1 keeps its
        # injected memory block), so injection must be judged on the latest message.
        message = [m for m in request if m["role"] == "user"][-1]
        content = message["content"]
        return content if isinstance(content, str) else content[0]["text"]

    def test_first_turn_injects_memory_and_later_turns_do_not(self):
        client = FakeClient([assistant("第一轮。"), assistant("第二轮。")])
        session = ChatSession(client, MockRuntime(memory=self.store), self.root, memory=self.store)
        session.send("第一句", approve=fixture_approval)
        session.send("第二句", approve=fixture_approval)
        self.assertIn("[长期记忆]", self.user_text(client.requests[0]))
        self.assertIn("常用数据在 D:/gis", self.user_text(client.requests[0]))
        self.assertNotIn("[长期记忆]", self.user_text(client.requests[1]))

    def test_reset_reinjects_memory(self):
        client = FakeClient([assistant("第一轮。"), assistant("第二轮。")])
        session = ChatSession(client, MockRuntime(memory=self.store), self.root, memory=self.store)
        session.send("第一句", approve=fixture_approval)
        session.reset()
        session.send("新对话", approve=fixture_approval)
        self.assertIn("[长期记忆]", self.user_text(client.requests[1]))

    def test_model_can_record_memory_through_the_tool(self):
        client = FakeClient([assistant(calls=[call("remember", {"text": "成果坐标系偏好 CGCS2000"})]), assistant("已记住。")])
        session = ChatSession(client, MockRuntime(memory=self.store), self.root, memory=self.store)
        result = session.send("记住我的偏好", approve=fixture_approval)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([item["text"] for item in self.store.entries()],
                         ["常用数据在 D:/gis", "成果坐标系偏好 CGCS2000"])


class ResolveSessionIdTests(unittest.TestCase):
    CHATS = [{"session_id": "a" * 32}, {"session_id": "ab" + "c" * 30}, {"session_id": "d" * 32}]

    def test_full_id_passes_through(self):
        self.assertEqual(resolve_session_id("a" * 32, []), "a" * 32)

    def test_unique_prefix_resolves(self):
        self.assertEqual(resolve_session_id("d", self.CHATS), "d" * 32)

    def test_ambiguous_prefix_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_session_id("a", self.CHATS)

    def test_unknown_or_empty_prefix_is_rejected(self):
        for prefix in ("zzz", ""):
            with self.assertRaises(ValueError):
                resolve_session_id(prefix, self.CHATS)


if __name__ == "__main__":
    unittest.main()
