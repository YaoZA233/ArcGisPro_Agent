"""Discover the toolbox; --interactive executes with per-tool native confirmations."""
import json
import sys
import uuid
import argparse
from pathlib import Path

import arcpy

repo = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description="Toolbox discovery; opt in to per-tool interactive execution")
parser.add_argument("--interactive", action="store_true")
arguments = parser.parse_args()
folder = repo / "workspace/smoke" / uuid.uuid4().hex
folder.mkdir(parents=True, exist_ok=False)
plan = folder / "environment.json"
plan.write_text(json.dumps({"version": 1, "steps": [{"tool": "environment", "arguments": {}}]}), encoding="utf-8")
arcpy.ImportToolbox(str(repo / "arcgis/ArcGISAgent.pyt"))
tools = arcpy.ListTools("*_arcgis_agent")
expected = {"ExecutePlan_arcgis_agent", "InspectCurrentProject_arcgis_agent"}
if not expected.issubset(set(tools)):
    print(json.dumps({"ok": False, "tools": tools}))
    sys.exit(1)
if not arguments.interactive:
    print(json.dumps({"ok": True, "tools": tools, "executed": False,
                      "message": "Discovery only. Use --interactive to execute and confirm each tool."}, ensure_ascii=False))
    sys.exit(0)
result = arcpy.ExecutePlan_arcgis_agent(str(plan), False)
print(json.dumps({"ok": result.status == 4, "tools": tools,
                  "messages": result.getMessages()}, ensure_ascii=False, indent=2))
sys.exit(0 if result.status == 4 else 1)
