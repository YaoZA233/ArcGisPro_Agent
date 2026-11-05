# -*- coding: utf-8 -*-
"""Add this Python toolbox to ArcGIS Pro 3.5.2's Catalog."""
import json
import sys
from pathlib import Path

import arcpy

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from arcgis_agent.agent import Agent, PlanPlanner, validate_plan
from arcgis_agent.config import Settings
from arcgis_agent.memory import MemoryStore
from arcgis_agent.runtime import InProcessRuntime
from arcgis_agent.approvals import toolbox_approval


def settings():
    local = REPO / "config/local.toml"
    return Settings.load(local) if local.is_file() else Settings(workspace=REPO / "workspace")


def runtime(config):
    return InProcessRuntime(config.skills_dir, config.data_roots, MemoryStore(config.workspace / "memory.json"))


class Toolbox:
    def __init__(self):
        self.label = "ArcGIS Pro Agent"
        self.alias = "arcgis_agent"
        self.tools = [InspectCurrentProject, ExecutePlan]


class InspectCurrentProject:
    def __init__(self):
        self.label = "Inspect current project / 查看当前工程"
        self.description = "Read maps and layers from the current Pro session. Does not save or modify the project."
        self.canRunInBackground = False

    def getParameterInfo(self):
        return []

    def execute(self, parameters, messages):
        config = settings()
        result = Agent(runtime(config), config.workspace, config.max_steps).run(
            PlanPlanner([{"tool": "project_info", "arguments": {"project": "CURRENT"}}]), approve=toolbox_approval)
        arcpy.AddMessage(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] != "completed":
            raise arcpy.ExecuteError("Inspect failed; see the task summary above.")


class ExecutePlan:
    def __init__(self):
        self.label = "Execute agent plan / 执行任务计划"
        self.description = "Execute a validated JSON plan. Write tools require Allow writes. Results go to a new task folder."
        self.canRunInBackground = False

    def getParameterInfo(self):
        plan = arcpy.Parameter(displayName="Plan JSON / 任务计划", name="plan", datatype="DEFile",
                               parameterType="Required", direction="Input")
        plan.filter.list = ["json"]
        writes = arcpy.Parameter(displayName="Allow writes / 允许创建分析成果", name="allow_writes", datatype="GPBoolean",
                                 parameterType="Optional", direction="Input")
        writes.value = False
        return [plan, writes]

    def execute(self, parameters, messages):
        config = settings()
        plan = json.loads(Path(parameters[0].valueAsText).read_text(encoding="utf-8-sig"))
        steps = validate_plan(plan, config.max_steps)
        result = Agent(runtime(config), config.workspace, config.max_steps).run(
            PlanPlanner(steps), allow_writes=bool(parameters[1].value), approve=toolbox_approval)
        arcpy.AddMessage(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] != "completed":
            raise arcpy.ExecuteError("Plan did not complete; see the task summary above.")
