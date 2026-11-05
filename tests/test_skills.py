"""Contract tests for the declarative skill system (offline, no ArcPy)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arcgis_agent.catalog import TOOLS
from arcgis_agent.runtime import InProcessRuntime, MockRuntime
from arcgis_agent.skills import BUILTIN_DIR, SKILL_TOOLS, execute_skill_tool, load_skills, validate_skill


def minimal_skill(**overrides):
    payload = {
        "name": "demo_skill",
        "title": "演示技能",
        "summary": "用于测试的最小技能包。",
        "when_to_use": "测试时。",
        "params": [{"name": "dataset", "description": "数据路径"}],
        "steps": [
            {"tool": "describe", "arguments": {"dataset": "{{params.dataset}}"}},
            {"tool": "copy_features", "arguments": {"dataset": "{{params.dataset}}", "output_name": "copied"}},
            {"tool": "verify_dataset", "arguments": {"dataset": "{{steps.copied.output}}", "min_count": 1}},
        ],
    }
    payload.update(overrides)
    return payload


class BuiltinPacks(unittest.TestCase):
    def test_builtin_packs_load_and_validate(self):
        skills = load_skills()
        self.assertGreaterEqual(len(skills), 7)
        for name, record in skills.items():
            self.assertEqual(record["name"], name)
            self.assertEqual(record["source"], "builtin")
            self.assertTrue(record["steps"])

    def test_expected_pack_names_present(self):
        names = set(load_skills())
        for expected in ("hydrology_watershed", "suitability_site_selection", "data_quality_check",
                         "interpolation_surface_map", "coordinate_unify_overlay",
                         "csv_points_to_density_map", "attribute_edit_delivery"):
            self.assertIn(expected, names)

    def test_every_step_tool_exists_and_is_not_host(self):
        for record in load_skills().values():
            for step in record["steps"]:
                self.assertIn(step["tool"], TOOLS)
                self.assertFalse(TOOLS[step["tool"]].host)

    def test_list_and_read_tools_registered(self):
        for name in SKILL_TOOLS:
            self.assertIn(name, TOOLS)
            self.assertFalse(TOOLS[name].writes)
            self.assertFalse(TOOLS[name].host)
        self.assertEqual(TOOLS["read_skill"].required, ("name",))
        self.assertEqual(TOOLS["list_skills"].required, ())


class SkillValidation(unittest.TestCase):
    def assert_rejected(self, payload, fragment):
        with self.assertRaises(ValueError) as caught:
            validate_skill(payload)
        self.assertIn(fragment, str(caught.exception))

    def test_minimal_skill_accepted(self):
        self.assertEqual(validate_skill(minimal_skill())["name"], "demo_skill")

    def test_bad_name_rejected(self):
        self.assert_rejected(minimal_skill(name="Demo Skill"), "skill.name")
        self.assert_rejected(minimal_skill(name="_x"), "skill.name")

    def test_unknown_top_level_key_rejected(self):
        self.assert_rejected(minimal_skill(extra_key=1), "unknown keys")

    def test_unknown_tool_rejected(self):
        skill = minimal_skill()
        skill["steps"][0]["tool"] = "run_python"
        self.assert_rejected(skill, "unknown tool")

    def test_host_tool_rejected(self):
        skill = minimal_skill()
        skill["steps"][0] = {"tool": "map_context", "arguments": {}}
        self.assert_rejected(skill, "host-only")

    def test_unknown_argument_key_rejected(self):
        skill = minimal_skill()
        skill["steps"][0]["arguments"]["datasets"] = "x"
        self.assert_rejected(skill, "has no arguments")

    def test_missing_required_argument_rejected(self):
        skill = minimal_skill()
        skill["steps"][1]["arguments"].pop("output_name")
        self.assert_rejected(skill, "requires arguments")

    def test_bad_enum_literal_rejected(self):
        skill = minimal_skill(steps=[
            {"tool": "export_layout", "arguments": {"project": "E:/x.aprx", "format": "BMP", "output_name": "out"}}])
        self.assert_rejected(skill, "is not one of")

    def test_bad_output_name_pattern_rejected(self):
        skill = minimal_skill()
        skill["steps"][1]["arguments"]["output_name"] = "9bad name"
        self.assert_rejected(skill, "pattern")

    def test_undeclared_param_placeholder_rejected(self):
        skill = minimal_skill()
        skill["steps"][0]["arguments"]["dataset"] = "{{params.other}}"
        self.assert_rejected(skill, "undeclared parameter")

    def test_forward_step_reference_rejected(self):
        skill = minimal_skill()
        skill["steps"][0]["arguments"]["dataset"] = "{{steps.copied.output}}"
        self.assert_rejected(skill, "does not exist")

    def test_malformed_placeholder_rejected(self):
        skill = minimal_skill()
        skill["steps"][0]["arguments"]["dataset"] = "{{import os}}"
        self.assert_rejected(skill, "unsupported placeholder")

    def test_broken_json_file_fails_fast(self):
        with tempfile.TemporaryDirectory() as user_dir:
            (Path(user_dir) / "broken.json").write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                load_skills(Path(user_dir))
            self.assertIn("unreadable", str(caught.exception))


class UserSkills(unittest.TestCase):
    def test_user_dir_extends_and_overrides(self):
        with tempfile.TemporaryDirectory() as user_dir:
            custom = minimal_skill(name="custom_flow")
            (Path(user_dir) / "custom.json").write_text(json.dumps(custom, ensure_ascii=False), encoding="utf-8")
            override = minimal_skill(name="hydrology_watershed", title="用户覆盖版")
            (Path(user_dir) / "override.json").write_text(json.dumps(override, ensure_ascii=False), encoding="utf-8")
            skills = load_skills(Path(user_dir))
            self.assertEqual(skills["custom_flow"]["source"], "user")
            self.assertEqual(skills["hydrology_watershed"]["source"], "user")
            self.assertEqual(skills["hydrology_watershed"]["title"], "用户覆盖版")
            self.assertEqual(skills["data_quality_check"]["source"], "builtin")

    def test_duplicate_user_name_rejected(self):
        with tempfile.TemporaryDirectory() as user_dir:
            for file in ("a.json", "b.json"):
                (Path(user_dir) / file).write_text(json.dumps(minimal_skill(), ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                load_skills(Path(user_dir))
            self.assertIn("duplicate user skill", str(caught.exception))


class SkillToolExecution(unittest.TestCase):
    def test_list_skills_payload(self):
        data = execute_skill_tool("list_skills", {})
        self.assertEqual(data["count"], len(data["skills"]))
        self.assertGreaterEqual(data["count"], 7)
        for record in data["skills"]:
            self.assertNotIn("steps", record)
            self.assertIn("usage", data)

    def test_read_skill_returns_full_pack(self):
        data = execute_skill_tool("read_skill", {"name": "hydrology_watershed"})
        self.assertEqual([step["tool"] for step in data["steps"]],
                         ["describe", "fill", "flow_direction", "flow_accumulation"])
        self.assertIn("Spatial Analyst", data["requires"])

    def test_read_unknown_skill_rejected(self):
        with self.assertRaises(ValueError) as caught:
            execute_skill_tool("read_skill", {"name": "no_such_skill"})
        self.assertIn("Unknown skill", str(caught.exception))

    def test_arcpy_runtime_returns_structured_error_without_worker(self):
        from arcgis_agent.runtime import ArcPyRuntime
        runtime = ArcPyRuntime(Path("nonexistent-python.exe"))
        good = runtime.call("read_skill", {"name": "hydrology_watershed"}, Path("."))
        self.assertTrue(good["ok"])
        bad = runtime.call("read_skill", {"name": "no_such_skill"}, Path("."))
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"]["type"], "ValueError")
        self.assertIn("Unknown skill", bad["error"]["message"])

    def test_mock_runtime_serves_real_skill_data(self):
        result = MockRuntime().call("list_skills", {}, Path(tempfile.gettempdir()))
        self.assertTrue(result["ok"])
        self.assertNotIn("simulated", result)
        self.assertGreaterEqual(result["data"]["count"], 7)

    def test_in_process_runtime_reads_skills_without_arcpy(self):
        result = InProcessRuntime().call("read_skill", {"name": "data_quality_check"}, Path(tempfile.gettempdir()))
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["name"], "data_quality_check")

    def test_builtin_dir_inside_package(self):
        self.assertTrue(BUILTIN_DIR.is_dir())
        self.assertEqual(BUILTIN_DIR.parent.name, "arcgis_agent")


if __name__ == "__main__":
    unittest.main()
