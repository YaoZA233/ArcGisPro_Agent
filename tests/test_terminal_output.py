import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from arcgis_agent.interfaces import TerminalTurn
from arcgis_agent.approvals import terminal_approval


class TerminalOutputTests(unittest.TestCase):
    def test_interleaved_reasoning_tool_and_final_are_separated(self):
        output = io.StringIO()
        with redirect_stdout(output):
            view = TerminalTurn(color=False, width=40)
            view.start()
            view.event({"event": "reasoning_delta", "text": "先检查"})
            view.event({"event": "content_delta", "text": "**准备**检查"})
            view.event({"event": "tool_requested", "tool": "describe", "step": 1})
            view.event({"event": "tool_result", "ok": True, "data": {"count": 2}})
            view.event({"event": "reasoning_delta", "text": "检查完成"})
            view.event({"event": "content_delta", "text": "# 结果\n\n| 指标 | 值 |\n| --- | --- |\n| 数量 | 2 |"})
            view.finish({"reply": "# 结果\n\n| 指标 | 值 |\n| --- | --- |\n| 数量 | 2 |", "usage": {"input": 1234, "output": 12}, "usage_available": True, "status": "completed", "run_dir": "run", "elapsed_seconds": 1})
        text = output.getvalue()
        self.assertLess(text.index("模型思考"), text.index("模型说明"))
        self.assertLess(text.index("模型说明"), text.index("工具 ·"))
        self.assertLess(text.index("工具结果"), text.index("Agent 答复"))
        self.assertEqual(text.count("| 数量"), 1)
        self.assertIn("1,234 tokens", text)
        self.assertNotIn("\x1b", text)

    def test_failure_keeps_partial_content_and_unknown_usage(self):
        output = io.StringIO()
        with redirect_stdout(output):
            view = TerminalTurn(color=False, width=40)
            view.event({"event": "content_delta", "text": "部分内容"})
            view.fail("连接中断")
            view.finish({"reply": "未完成", "usage_available": False})
        self.assertIn("部分内容", output.getvalue())
        self.assertIn("接口未返回", output.getvalue())

    def test_approval_preserves_default_deny_and_details(self):
        request = {"title": "读取数据", "effect": "只读", "arguments": {"dataset": "input"}, "writes": False, "expected_outputs": []}
        output = io.StringIO()
        with redirect_stdout(output), patch("builtins.input", return_value=""):
            result = terminal_approval(request)
        self.assertEqual(result["decision"], "deny")
        self.assertIn("读取数据", output.getvalue())
        self.assertIn("input", output.getvalue())


if __name__ == "__main__":
    unittest.main()
