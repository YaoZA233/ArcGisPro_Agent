"""Contract tests for the bundled LLM system prompt (src/arcgis_agent/prompts/system.md)."""
from __future__ import annotations

import re
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
    "不要反问用户",                   # resolve technical uncertainty with tools
    "说明执行计划",                   # plan before multi-step execution
    "不同的纠正动作",                 # diagnose failures and change approach
    "绝不把技术错误",                 # no raw handoff of fixable errors
    "styles",                         # layout symbology palettes
    "色板",                           # palette selection guidance
    "labels",                         # layer labelling
    "重建工程",                       # palette change requires a rebuilt project
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

    # These are bounded instruction contracts, not proof of model compliance.
    # Check short markers in the relevant step, not entire prose paragraphs.
    def _workflow_step(self, number):
        match = re.search(rf"(?ms)^{number}\.\s.*?(?=^\d+\.\s|^## |\Z)", SYSTEM)
        self.assertIsNotNone(match, f"missing workflow step: {number}")
        return match.group()

    def _assert_markers(self, text, markers):
        for marker in markers:
            with self.subTest(marker=marker):
                self.assertTrue(marker in text, f"missing rule marker: {marker}")

    def test_technical_uncertainty_uses_approved_read_only_tools(self):
        # Catch unbounded autonomy or path/field questions before probing.
        self._assert_markers(self._workflow_step(1), (
            "可通过工具消解", "技术性不确定", "逐次授权", "只读",
            "describe", "find_datasets", "read_rows", "不要反问用户",
        ))

    def test_autonomy_exempts_license_access_approval_and_business_scope(self):
        # A blanket no-questions rule must not suppress essential approvals.
        self._assert_markers(self._workflow_step(1), (
            "不适用", "许可证", "访问权限", "安全审批", "必要业务范围",
            "分析范围", "方法选择",
        ))

    def test_discovery_disambiguates_with_evidence_before_business_question(self):
        step = self._workflow_step(2)
        self._assert_markers(step, (
            "map_context", "已有地图上下文", "名称", "类型", "范围", "元数据",
            "授权", "只读证据消歧", "实质业务歧义", "简短业务问题",
        ))
        self.assertRegex(step, re.compile(
            r"多个候选.*?先.*?只读证据消歧.*?仅当.*?实质业务歧义.*?才.*?简短业务问题",
            re.S,
        ))
        self.assertNotIn("多个候选列出名称、路径与类型让用户选择", step)

    def test_empty_discovery_tries_bounded_alternatives_before_reporting(self):
        step = self._workflow_step(2)
        self._assert_markers(step, (
            "配置的数据根目录", "合理的替代关键词", "已搜索范围", "阻碍",
            "不得无限制扫描", "不得编造路径", "绕过权限",
        ))
        self.assertRegex(step, re.compile(
            r"搜索为空.*?先.*?替代关键词.*?仍无结果.*?如实说明", re.S,
        ))

    def test_multistep_plan_is_short_actionable_and_precedes_tools(self):
        step = self._workflow_step(4)
        self._assert_markers(step, (
            "一两句话说明执行计划", "再开始调用工具", "真实输出路径",
            "独立授权", "简短可执行", "不展开详细推理链",
        ))
        self.assertRegex(step, re.compile(r"说明执行计划.*?再开始调用工具", re.S))

    def test_failure_diagnosis_changes_action_and_gathers_missing_facts(self):
        # Prevent returning to identical retries or raw technical-error handoff.
        self._assert_markers(SYSTEM, (
            "`error`", "`hint`", "诊断根因", "不同的纠正动作",
            "不要用相同参数盲目重试", "事实不足", "获批的只读工具",
            "补齐信息再重试", "绝不把技术错误", "可修复",
        ))

    def test_blockers_are_reported_without_false_success_or_permission_bypass(self):
        self._assert_markers(SYSTEM, (
            "无获授权且具备能力的替代方案", "许可证缺失",
            "尝试的动作", "结果", "阻碍", "实际输出路径", "未完成",
            "不得假称成功", "不保证所有技术问题都能解决",
            "授权过期", "写入未授权", "立即停止", "异常",
        ))

    def test_no_overwrite_remains_absolute_and_write_grant_is_single_use(self):
        self._assert_markers(SYSTEM, (
            "已有输出拒绝覆盖", "即使用户同意也不得覆盖",
            "单次写入许可仅对本次调用有效", "不得复用到后续调用", "唯一任务目录",
        ))
        self.assertNotIn("是否覆盖已有成果", SYSTEM)

    def test_either_write_capability_grant_still_requires_per_call_approval(self):
        # Catch AND-only capability wording or global enablement waiving approval.
        match = re.search(r"(?m)^- .*写操作只在唯一任务目录.*$", SYSTEM)
        self.assertIsNotNone(match, "missing write-safety rule")
        rule = match.group()
        self._assert_markers(rule, (
            "每次写操作仍须本次单独授权", "二者满足其一即可",
            "全局写入能力不代替逐次授权",
        ))
        self.assertRegex(rule, r"调用方显式启用或本次授权中的单次写入许可")
        self.assertNotIn("调用方显式启用写入且本次获批", rule)

    def test_existing_safety_and_extension_license_warning_remain(self):
        # Preserve the independent safety rules, including license hard-stop.
        self._assert_markers(SYSTEM, (
            "等待用户单独授权", "源数据不变", "不改动用户工程", "绝不保存",
            "空选择集不得退化为整层处理", "没有 eval/exec", "没有 shell",
            "拒绝或取消后不继续", "绕过被拒的操作",
            "离线模拟", "simulated: true", "工具成功不等于业务正确",
            "Spatial Analyst", "签出扩展失败时整次调用失败",
            "说明许可证状态", "不会退回部分结果", "不要换参数硬试",
        ))


if __name__ == "__main__":
    unittest.main()
