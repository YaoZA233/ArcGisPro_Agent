"""Real ArcPy suite using only synthetic fixtures under workspace/smoke.

The explicit fixture approval callback is test-only and refuses all external inputs.
Run with licensed Pro Python. No model API or user datasets are used.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from arcgis_agent.agent import Agent, PlanPlanner
from arcgis_agent.runtime import InProcessRuntime
from arcgis_agent.tools import execute


def main():
    print("Loading licensed ArcPy for isolated synthetic tool verification…", flush=True)
    import arcpy
    import numpy as np

    root = (REPO / "workspace/smoke" / uuid.uuid4().hex).resolve()
    root.mkdir(parents=True, exist_ok=False)
    print(json.dumps({"fixture_root": str(root), "version": arcpy.GetInstallInfo()["Version"]}), flush=True)
    gdb = arcpy.management.CreateFileGDB(str(root), "input.gdb")[0]
    sr = arcpy.SpatialReference(3857)
    points = arcpy.management.CreateFeatureclass(gdb, "points", "POINT", spatial_reference=sr)[0]
    arcpy.management.AddField(points, "group_name", "TEXT", field_length=20)
    arcpy.management.AddField(points, "score", "DOUBLE")
    with arcpy.da.InsertCursor(points, ["SHAPE@XY", "group_name", "score"]) as cursor:
        for values in (((0, 0), "A", 1), ((100, 0), "A", 2), ((1000, 0), "B", 3)):
            cursor.insertRow(values)
    clipping = arcpy.management.CreateFeatureclass(gdb, "boundary", "POLYGON", spatial_reference=sr)[0]
    polygon = arcpy.Polygon(arcpy.Array([arcpy.Point(x, y) for x, y in ((-10, -10), (500, -10), (500, 100), (-10, 100), (-10, -10))]), sr)
    with arcpy.da.InsertCursor(clipping, ["SHAPE@"]) as cursor:
        cursor.insertRow([polygon])
    raster = str(root / "source.tif")
    arcpy.NumPyArrayToRaster(np.arange(10000, dtype="int16").reshape(100, 100), arcpy.Point(-100, -100), 10, 10).save(raster)
    arcpy.management.DefineProjection(raster, sr)
    original = [(bytes(row[0]), row[1], row[2]) for row in arcpy.da.SearchCursor(points, ["SHAPE@WKB", "group_name", "score"])]
    checks, results = [], {}

    def approve_fixture(request):
        for key in ("dataset", "workspace", "clip_dataset", "join_dataset"):
            if key in request["arguments"] and not Path(request["arguments"][key]).resolve().is_relative_to(root):
                return {"decision": "deny"}
        for item in request["arguments"].get("datasets", []):
            if not Path(item).resolve().is_relative_to(root):
                return {"decision": "deny"}
        if any(not Path(path).resolve().is_relative_to(root) for path in request["expected_outputs"]):
            return {"decision": "deny"}
        print(json.dumps({"fixture_approval": request["tool"], "writes": request["writes"]}), flush=True)
        return {"decision": "allow", "allow_writes": request["writes"]}

    def check(name, arguments, verify):
        result = Agent(InProcessRuntime(), root).run(PlanPlanner([{"tool": name, "arguments": arguments}]), approve=approve_fixture)
        data = result["observations"][-1].get("data", {}) if result["observations"] else {}
        passed = result["status"] == "completed" and verify(data)
        checks.append({"tool": name, "passed": bool(passed), "run_dir": result["run_dir"], "output": data.get("output")})
        results[name] = result
        print(json.dumps(checks[-1], ensure_ascii=False), flush=True)
        if not passed:
            raise AssertionError(json.dumps(result, ensure_ascii=False))
        return data

    try:
        check("environment", {}, lambda data: data["version"] == "3.5.2")
        check("list_datasets", {"workspace": gdb}, lambda data: {"points", "boundary"}.issubset({d["name"] for d in data["datasets"]}))
        check("describe", {"dataset": points}, lambda data: data["count"] == 3 and data["spatial_reference"]["wkid"] == 3857)
        check("count", {"dataset": points}, lambda data: data["count"] == 3)
        check("read_rows", {"dataset": points, "fields": ["group_name", "score"], "limit": 2}, lambda data: data["returned"] == 2 and data["truncated"])
        copied = check("copy_features", {"dataset": points, "output_name": "copied"}, lambda data: int(arcpy.management.GetCount(data["output"])[0]) == 3)["output"]
        check("clip", {"dataset": points, "clip_dataset": clipping, "output_name": "clipped"}, lambda data: int(arcpy.management.GetCount(data["output"])[0]) == 2)
        check("intersect", {"datasets": [points, clipping], "output_name": "intersection"}, lambda data: int(arcpy.management.GetCount(data["output"])[0]) == 2)
        check("merge", {"datasets": [points, copied], "output_name": "merged"}, lambda data: int(arcpy.management.GetCount(data["output"])[0]) == 6)
        check("dissolve", {"dataset": points, "fields": ["group_name"], "output_name": "dissolved"}, lambda data: int(arcpy.management.GetCount(data["output"])[0]) == 2)
        check("project", {"dataset": points, "target_wkid": 4326, "output_name": "geographic"}, lambda data: arcpy.Describe(data["output"]).spatialReference.factoryCode == 4326)
        check("spatial_join", {"dataset": points, "join_dataset": clipping, "output_name": "joined"}, lambda data: sorted(row[0] for row in arcpy.da.SearchCursor(data["output"], ["Join_Count"])) == [0, 1, 1])
        condition = {"field": "group_name", "operator": "EQ", "value": "A"}
        check("select_features", {"dataset": points, "filter": condition, "output_name": "selected"}, lambda data: int(arcpy.management.GetCount(data["output"])[0]) == 2)
        check("add_field", {"dataset": points, "field_name": "reviewed", "field_type": "LONG", "output_name": "newfield"}, lambda data: "reviewed" in {field.name for field in arcpy.ListFields(data["output"])})
        check("set_field_value", {"dataset": points, "field": "score", "value": 10, "filter": condition, "output_name": "updated"}, lambda data: data["updated_count"] == 2 and sorted(row[0] for row in arcpy.da.SearchCursor(data["output"], ["score"])) == [3, 10, 10])
        check("delete_fields", {"dataset": points, "fields": ["group_name"], "output_name": "dropped"}, lambda data: "group_name" not in {field.name for field in arcpy.ListFields(data["output"])})
        buffered = check("buffer", {"dataset": points, "distance": "50 Meters", "output_name": "buffers"}, lambda data: int(arcpy.management.GetCount(data["output"])[0]) == 3)["output"]
        check("calculate_geometry", {"dataset": buffered, "field_name": "area_m2", "measurement": "AREA_GEODESIC", "unit": "SQUARE_METERS", "output_name": "areas"}, lambda data: all(7000 < row[0] < 8500 for row in arcpy.da.SearchCursor(data["output"], ["area_m2"])))
        check("repair_geometry", {"dataset": buffered, "output_name": "repaired"}, lambda data: int(arcpy.management.GetCount(data["output"])[0]) == 3)
        check("clip_raster", {"dataset": raster, "clip_dataset": clipping, "output_name": "raster_clipped"}, lambda data: 0 < arcpy.Describe(data["output"]).width < 100 and 0 < arcpy.Describe(data["output"]).height < 100)
        unchanged = original == [(bytes(row[0]), row[1], row[2]) for row in arcpy.da.SearchCursor(points, ["SHAPE@WKB", "group_name", "score"])]
        checks.append({"check": "source_geometry_and_attributes_unchanged", "passed": unchanged})
        copy_dir = Path(results["copy_features"]["run_dir"]) / "outputs"
        try:
            execute("copy_features", {"dataset": points, "output_name": "copied"}, copy_dir)
            no_overwrite = False
        except ValueError:
            no_overwrite = int(arcpy.management.GetCount(copied)[0]) == 3
        checks.append({"check": "existing_output_rejected", "passed": no_overwrite})
    except Exception as exc:
        checks.append({"check": "suite_error", "passed": False, "message": str(exc)})
    passed = all(check["passed"] for check in checks)
    summary = {"ok": passed, "fixture_root": str(root), "checks": checks, "scope": "Synthetic fixtures only; not business-level GIS acceptance"}
    (root / "tool-validation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
