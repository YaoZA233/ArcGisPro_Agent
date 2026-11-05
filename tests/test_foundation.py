from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from functools import partial
from approval_fixtures import fixture_approval
from pathlib import Path
from unittest.mock import patch

from arcgis_agent.agent import Action, Agent, PlanPlanner, validate_plan
from arcgis_agent.cli import main
from arcgis_agent.config import Settings, discover_python
from arcgis_agent.runtime import ArcPyRuntime, MockRuntime
from arcgis_agent.tools import validate_call


def step(tool="environment", **arguments):
    return {"tool": tool, "arguments": arguments}


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def agent(self, runtime=None, max_steps=8):
        instance = Agent(runtime or MockRuntime(), self.root, max_steps)
        instance.run = partial(instance.run, approve=fixture_approval)
        return instance

    def test_offline_run_has_durable_trace_and_simulation_label(self):
        result = self.agent().run(PlanPlanner([step()]))
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["simulated"])
        run = Path(result["run_dir"])
        self.assertEqual(json.loads((run / "summary.json").read_text(encoding="utf-8")), result)
        events = [json.loads(line)["event"] for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events, ["started", "tool_requested", "approval_requested", "approval_decided", "tool_executing", "tool_result", "finished"])

    def test_tool_events_carry_step_numbers(self):
        events = []
        result = self.agent().run(PlanPlanner([step(), step()]), on_event=events.append)
        self.assertEqual(result["status"], "completed")
        steps = [record["step"] for record in events
                 if record["event"] in ("tool_requested", "tool_executing", "tool_result")]
        self.assertEqual(steps, [1, 1, 1, 2, 2, 2])

    def test_worker_progress_becomes_durable_tool_progress_events(self):
        class ProgressRuntime:
            def call(self, name, arguments, output_dir, on_progress=None):
                if on_progress is not None:
                    on_progress("[worker] tool=environment starting")
                return {"ok": True, "data": {"version": "3.5.2"}}

            def export_selection(self, snapshot, arguments, output_dir, on_progress=None):
                return self.call("export_selection", arguments, output_dir, on_progress)

        events = []
        result = self.agent(ProgressRuntime()).run(PlanPlanner([step()]), on_event=events.append)
        self.assertEqual(result["status"], "completed")
        progress = [record for record in events if record["event"] == "tool_progress"]
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0]["step"], 1)
        self.assertEqual(progress[0]["tool"], "environment")
        self.assertIn("starting", progress[0]["text"])

    def test_write_is_blocked_before_runtime_call(self):
        with patch.object(MockRuntime, "call", side_effect=AssertionError("Must not dispatch")):
            result = self.agent().run(PlanPlanner([step("buffer", dataset="x", distance="1 Meters", output_name="out")]))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["observations"], [])

    def test_explicit_write_permission_dispatches(self):
        result = self.agent().run(PlanPlanner([step("buffer", dataset="x", distance="1 Meters", output_name="out")]), allow_writes=True)
        self.assertEqual(result["status"], "completed")
        self.assertFalse((Path(result["run_dir"]) / "outputs").exists())

    def test_failed_tool_stops_following_steps(self):
        class Failure:
            calls = 0

            def call(self, *args, **kwargs):
                self.calls += 1
                return {"ok": False, "error": {"message": "failure"}}

        runtime = Failure()
        result = self.agent(runtime).run(PlanPlanner([step(), step()]))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(runtime.calls, 1)

    def test_planner_cannot_loop_forever(self):
        class Forever:
            def next_action(self, observations):
                return Action("environment", {})

        result = self.agent(max_steps=2).run(Forever())
        self.assertEqual(result["status"], "step_limit")
        self.assertEqual(len(result["observations"]), 2)

    def test_exact_limit_can_complete(self):
        self.assertEqual(self.agent(max_steps=1).run(PlanPlanner([step()]))["status"], "completed")

    def test_runtime_exception_is_persisted(self):
        with patch.object(MockRuntime, "call", side_effect=RuntimeError("broken runtime")):
            result = self.agent().run(PlanPlanner([step()]))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "RuntimeError")

    def test_task_directories_are_unique(self):
        a, b = [self.agent().run(PlanPlanner([step()])) for _ in range(2)]
        self.assertNotEqual(a["run_dir"], b["run_dir"])

    def test_invalid_plans_rejected_before_dispatch(self):
        plans = [{}, {"version": True, "steps": [step()]}, {"version": 1, "steps": []},
                 {"version": 1, "steps": [step(), step()]},
                 {"version": 1, "steps": [{"tool": "exec", "arguments": {}}]},
                 {"version": 1, "steps": [{"tool": "describe", "arguments": {"dataset": ""}}]}]
        for plan in plans:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                validate_plan(plan, 1)

    def test_no_path_traversal_or_code_arguments(self):
        for name in ("../output", "C:/x", "x/y", "a;delete", "_x"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_call("buffer", {"dataset": "x", "distance": "500 Meters", "output_name": name})
        with self.assertRaises(ValueError):
            validate_call("environment", {"code": "print('unexpected')"})

    def test_invalid_distances(self):
        for distance in ("0 Meters", "-1 Meters", "nan Meters", "500", "500 Degrees"):
            with self.subTest(distance=distance), self.assertRaises(ValueError):
                validate_call("buffer", {"dataset": "x", "distance": distance, "output_name": "out"})

    def test_current_rejected_before_importing_arcpy(self):
        # The subprocess-facing executor must reject CURRENT before importing ArcPy.
        from arcgis_agent.tools import execute
        with self.assertRaisesRegex(ValueError, "CURRENT"):
            execute("project_info", {"project": "CURRENT"}, self.root)

    def test_project_info_layer_records_skip_network_probes(self):
        from arcgis_agent.tools import _layer_record

        class Layer:
            def __init__(self, name, source, group=False, broken=False):
                self.name, self.source, self.isGroupLayer, self.broken = name, source, group, broken
                self.probed = False

            @property
            def dataSource(self):
                if self.source is None:
                    raise AttributeError("dataSource")
                return self.source

            @property
            def isBroken(self):
                self.probed = True
                return self.broken

        service = Layer("World Topographic Map", "https:\\cdn.arcgis.com/sharing/rest/x/root.json")
        self.assertEqual(_layer_record(service), {"name": "World Topographic Map", "is_broken": None,
                                                  "broken_check": "skipped: online service, no network probe"})
        self.assertFalse(service.probed)
        group = Layer("daolu", None, group=True)
        self.assertEqual(_layer_record(group), {"name": "daolu", "is_broken": None,
                                                "broken_check": "skipped: group layer, children are listed separately"})
        self.assertFalse(group.probed)
        local = Layer("gonglu", "E:\\data\\gonglu.shp", broken=True)
        self.assertEqual(_layer_record(local), {"name": "gonglu", "is_broken": True})
        self.assertTrue(local.probed)

    def test_project_info_layout_records_report_page_and_elements(self):
        from arcgis_agent.tools import _layout_record

        class Layout:
            name, pageWidth, pageHeight, pageUnits = "湖南县市地图", 420.0, 297.0, "MILLIMETER"

            def listElements(self):
                return ["图幅", "图名", "图例", "指北针", "比例尺"]

        self.assertEqual(_layout_record(Layout()), {"name": "湖南县市地图", "page_width": 420.0,
                                                    "page_height": 297.0, "page_units": "MILLIMETER",
                                                    "element_count": 5})

    def test_config_paths_relative_to_config_file(self):
        config = self.root / "agent.toml"
        config.write_text('[agent]\nworkspace = "outputs"\nmax_steps = 3\nskills_dir = "packs"\n', encoding="utf-8")
        settings = Settings.load(config)
        self.assertEqual(settings.workspace, self.root / "outputs")
        self.assertEqual(settings.max_steps, 3)
        self.assertEqual(settings.skills_dir, self.root / "packs")

    def test_config_skills_dir_defaults_none(self):
        config = self.root / "agent-default.toml"
        config.write_text('[agent]\nworkspace = "outputs"\n', encoding="utf-8")
        self.assertIsNone(Settings.load(config).skills_dir)

    def test_config_limits_validated(self):
        for content in ('[agent]\nmax_steps = 0', '[arcgis]\ntimeout_seconds = -1'):
            config = self.root / "invalid.toml"
            config.write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError):
                Settings.load(config)

    def test_explicit_python_path_does_not_fall_back(self):
        with self.assertRaises(ValueError):
            discover_python(self.root / "missing/python.exe")

    def test_subprocess_result_is_independent_of_stdout_noise(self):
        def fake_run(command, **kwargs):
            request = json.loads(Path(command[-2]).read_text(encoding="utf-8"))
            self.assertEqual(request["tool"], "environment")
            self.assertFalse(kwargs["shell"])
            Path(command[-1]).write_text(json.dumps({"ok": True, "data": {"name": "湖南"}}, ensure_ascii=False), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "ArcPy native noise", "")

        with patch("arcgis_agent.runtime.subprocess.run", side_effect=fake_run):
            result = ArcPyRuntime(Path("python.exe")).call("environment", {}, self.root)
        self.assertEqual(result["data"]["name"], "湖南")

    def test_native_crash_is_failure(self):
        with patch("arcgis_agent.runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 3221225477, "", "")):
            result = ArcPyRuntime(Path("python.exe")).call("environment", {}, self.root)
        self.assertFalse(result["ok"])
        self.assertIn("3221225477", result["error"]["message"])

    def test_timeout_is_failure_with_partial_output_notice(self):
        with patch("arcgis_agent.runtime.subprocess.run", side_effect=subprocess.TimeoutExpired([], 1)):
            result = ArcPyRuntime(Path("python.exe"), 1).call("environment", {}, self.root)
        self.assertEqual(result["error"]["type"], "Timeout")
        self.assertIn("Partial outputs", result["error"]["message"])

    def test_corrupt_worker_protocol_is_failure(self):
        def fake_run(command, **kwargs):
            Path(command[-1]).write_text('{"ok": "yes"}', encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("arcgis_agent.runtime.subprocess.run", side_effect=fake_run):
            result = ArcPyRuntime(Path("python.exe")).call("environment", {}, self.root)
        self.assertEqual(result["error"]["type"], "ProtocolError")

    def test_cli_invalid_plan_returns_nonzero(self):
        plan = self.root / "invalid.json"
        plan.write_text('{"version": 1, "steps": []}', encoding="utf-8")
        with patch("arcgis_agent.cli.print_json"):
            self.assertEqual(main(["run", str(plan), "--mock"]), 2)


if __name__ == "__main__":
    unittest.main()
