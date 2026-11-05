"""Contract tests for the bundled LLM system prompt (src/arcgis_agent/prompts/system.md)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arcgis_agent.deepseek import SYSTEM

PROMPT_FILE = Path(__file__).resolve().parents[1] / "src" / "arcgis_agent" / "prompts" / "system.md"

REQUIRED_MARKERS = (
    "不能声称未执行的工具已经执行",   # execution honesty
    "等待用户单独授权",               # per-call approval
    "绝不编造路径",                   # no invented paths
    "真实输出路径",                   # real output chaining
    "0.0005",                         # geographic cell-size discipline
    "verify_dataset",                 # acceptance tool
    "list_skills",                    # skill flow
    "不得残留占位符",                 # placeholder discipline
    "指令注入",                       # prompt-injection guard
    "不要输出密钥",                   # secret guard
    "selection_token",                # selection flow
    "export_layout",                  # two-step cartography
    "自主定位数据",                   # autonomous data discovery step
    "find_datasets",                  # discovery tool
    "不要一上来向用户索要路径",       # never ask for paths before searching
    "长期记忆",                       # long-term memory section
    "remember",                       # memory recording tool
    "绝不把密钥",                     # never persist secrets into memory
)


class SystemPromptContract(unittest.TestCase):
    def test_prompt_file_exists_and_is_loaded_verbatim(self):
        self.assertTrue(PROMPT_FILE.is_file())
        raw = PROMPT_FILE.read_text(encoding="utf-8")
        self.assertTrue(raw.strip())
        self.assertEqual(SYSTEM, raw)

    def test_prompt_carries_all_rule_markers(self):
        for marker in REQUIRED_MARKERS:
            self.assertIn(marker, SYSTEM, f"missing rule marker: {marker}")

    def test_prompt_has_no_todo_markers(self):
        # Literal "{{...}}" examples of skill placeholders are intentional teaching text;
        # what must never appear are unfinished-draft markers.
        for bad in ("TBD", "TODO", "…待补充", "（待定）"):
            self.assertNotIn(bad, SYSTEM)

    def test_prompt_stays_within_token_budget(self):
        # The whole file is sent with every model request; keep it bounded.
        self.assertLess(len(SYSTEM), 8000)


if __name__ == "__main__":
    unittest.main()
