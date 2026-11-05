"""Batch 4 real ArcPy smoke: query/selection expansion, verification, field calculation,
exports and raster/hydrology tools. Synthetic fixtures only, under workspace/smoke.

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


def main():
    print("Loading licensed ArcPy for batch 4 synthetic tool verification…", flush=True)
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
    boundary = arcpy.management.CreateFeatureclass(gdb, "boundary", "POLYGON", spatial_reference=sr)[0]
    polygon = arcpy.Polygon(arcpy.Array([arcpy.Point(x, y) for x, y in
                                         ((-10, -10), (500, -10), (500, 100), (-10, 100), (-10, -10))]), sr)
    with arcpy.da.InsertCursor(boundary, ["SHAPE@"]) as cursor:
        cursor.insertRow([polygon])
    raster = str(root / "source.tif")
    arcpy.NumPyArrayToRaster(np.arange(10000, dtype="int16").reshape(100, 100),
                              arcpy.Point(-100, -100), 10, 10).save(raster)
    arcpy.management.DefineProjection(raster, sr)
    original = [(bytes(row[0]), row[1], row[2]) for row in arcpy.da.SearchCursor(points, ["SHAPE@WKB", "group_name", "score"])]
    checks = []

    def approve_fixture(request):
        for key in ("dataset", "select_dataset", "near_dataset"):
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
        print(json.dumps(checks[-1], ensure_ascii=False), flush=True)
        if not passed:
            raise AssertionError(json.dumps(result, ensure_ascii=False))
        return data

    try:
        # --- batch A: spatial selection, proximity, Thiessen ---
        check("select_by_location", {"dataset": points, "select_dataset": boundary, "overlap_type": "INTERSECT",
                                     "output_name": "inside"}, lambda data: int(arcpy.management.GetCount(data["output"])[0]) == 2)
        check("select_by_location", {"dataset": points, "select_dataset": points, "overlap_type": "WITHIN_A_DISTANCE",
                                     "search_distance": "950 Meters", "output_name": "near_each_other"},
              lambda data: int(arcpy.management.GetCount(data["output"])[0]) == 3)
        near = check("near_analysis", {"dataset": points, "near_dataset": boundary, "output_name": "nearby"},
                     lambda data: data["matched_count"] == 3 and
                     all(row[0] != -1 for row in arcpy.da.SearchCursor(data["output"], ["NEAR_FID"])))
        check("create_thiessen_polygons", {"dataset": points, "fields_to_copy": "ONLY_FID", "output_name": "thiessen"},
              lambda data: int(arcpy.management.GetCount(data["output"])[0]) == 3)

        # --- batch A: composite attribute filters ---
        compound = {"all": [{"field": "score", "operator": "GE", "value": 2}, {"field": "group_name", "operator": "EQ", "value": "B"}]}
        check("select_features", {"dataset": points, "filter": compound, "output_name": "compound"},
              lambda data: int(arcpy.management.GetCount(data["output"])[0]) == 1)
        in_list = {"any": [{"field": "group_name", "operator": "IN", "values": ["A", "B"]},
                           {"field": "score", "operator": "GT", "value": 10}]}
        check("select_features", {"dataset": points, "filter": in_list, "output_name": "in_list"},
              lambda data: int(arcpy.management.GetCount(data["output"])[0]) == 3)
        check("read_rows", {"dataset": points, "filter": {"field": "group_name", "operator": "STARTS_WITH", "value": "B"}},
              lambda data: data["returned"] == 1 and data["rows"][0][1] == "B")

        # --- batch C: replica field calculation and exports ---
        with_calc = check("add_field", {"dataset": points, "field_name": "calc", "field_type": "DOUBLE",
                                         "output_name": "with_calc"}, lambda data: True)["output"]
        check("calculate_field", {"dataset": with_calc, "field": "calc", "expression": "!score! * 2 + 1",
                                  "output_name": "calc_done"},
              lambda data: data["updated_count"] == 3 and sorted(row[0] for row in arcpy.da.SearchCursor(data["output"], ["calc"])) == [3, 5, 7])
        check("calculate_field", {"dataset": with_calc, "field": "calc", "expression": "!score! * 2",
                                  "filter": {"field": "group_name", "operator": "EQ", "value": "A"}, "output_name": "calc_filtered"},
              lambda data: data["updated_count"] == 2)
        check("define_projection", {"dataset": points, "wkid": 3857, "output_name": "redefined"},
              lambda data: arcpy.Describe(data["output"]).spatialReference.factoryCode == 3857)
        csv_data = check("export_table_csv", {"dataset": points, "output_name": "points_csv"},
                         lambda data: Path(data["output"]).is_file())
        csv_lines = Path(csv_data["output"]).read_text(encoding="utf-8-sig").splitlines()
        checks.append({"check": "csv_has_header_and_rows", "passed": len(csv_lines) == 4 and "group_name" in csv_lines[0]})
        check("export_table_csv", {"dataset": points, "fields": ["group_name", "score"],
                                   "filter": {"field": "group_name", "operator": "EQ", "value": "B"},
                                   "output_name": "points_filtered_csv"},
              lambda data: len(Path(data["output"]).read_text(encoding="utf-8-sig").splitlines()) == 2)
        geojson = check("export_features", {"dataset": points, "format": "GEOJSON", "output_name": "points_geojson"},
                        lambda data: Path(data["output"]).is_file())
        payload = json.loads(Path(geojson["output"]).read_text(encoding="utf-8"))
        checks.append({"check": "geojson_feature_count", "passed": len(payload.get("features", [])) == 3})
        check("export_features", {"dataset": points, "format": "SHAPEFILE", "output_name": "points_shp"},
              lambda data: Path(data["output"]).is_file() and Path(str(data["output"])[:-4] + ".dbf").is_file())

        # --- batch B: structured verification ---
        verified = check("verify_dataset", {"dataset": points, "min_count": 3, "max_count": 3, "fields": ["group_name"],
                                           "non_null_fields": ["group_name"], "unique_fields": ["score"], "wkid": 3857,
                                           "extent_within": {"xmin": -2000, "ymin": -2000, "xmax": 2000, "ymax": 2000}},
                         lambda data: data["passed"] and data["check_count"] >= 6)
        failing = check("verify_dataset", {"dataset": points, "min_count": 999},
                        lambda data: data["passed"] is False and
                        [item["check"] for item in data["failures"]] == ["min_count"])
        checks.append({"check": "verify_returns_structured_failures", "passed": len(failing["failures"]) == 1})

        # --- batch D: raster and hydrology ---
        reclassified = check("reclassify", {"dataset": raster, "remap": [{"start": 0, "end": 4999, "new_value": 1},
                                                                         {"start": 5000, "end": 9999, "new_value": 2}],
                                            "map_nodata_to": 0, "output_name": "reclass"},
                             lambda data: Path(data["output"]).is_file())
        values = arcpy.RasterToNumPyArray(reclassified["output"])
        checks.append({"check": "reclassify_values_remap", "passed": set(np.unique(values)).issubset({0, 1, 2})})
        shaded = check("hillshade", {"dataset": raster, "azimuth": 315, "altitude": 45, "output_name": "shade"},
                       lambda data: Path(data["output"]).is_file())
        checks.append({"check": "hillshade_range", "passed": bool((arcpy.RasterToNumPyArray(shaded["output"]) >= 0).all())})
        check("resample", {"dataset": raster, "cell_size": 20, "resampling": "BILINEAR", "output_name": "resampled"},
              lambda data: arcpy.Describe(data["output"]).width == 50 and arcpy.Describe(data["output"]).meanCellWidth == 20)
        check("project_raster", {"dataset": raster, "target_wkid": 4326, "resampling": "NEAREST", "output_name": "wgs84"},
              lambda data: arcpy.Describe(data["output"]).spatialReference.factoryCode == 4326)
        check("fill", {"dataset": raster, "output_name": "filled"}, lambda data: Path(data["output"]).is_file())
        flow = check("flow_direction", {"dataset": raster, "output_name": "flow"}, lambda data: Path(data["output"]).is_file())["output"]
        accumulated = check("flow_accumulation", {"dataset": flow, "data_type": "INTEGER", "output_name": "accum"},
                            lambda data: Path(data["output"]).is_file())
        checks.append({"check": "flow_accumulation_max", "passed": int(arcpy.RasterToNumPyArray(accumulated["output"]).max()) >= 99})

        # --- safety invariants ---
        checks.append({"check": "source_geometry_and_attributes_unchanged",
                       "passed": original == [(bytes(row[0]), row[1], row[2]) for row in
                                              arcpy.da.SearchCursor(points, ["SHAPE@WKB", "group_name", "score"])]})
        checks.append({"check": "verify_dataset_is_readonly", "passed": verified["source_modified"] is False})
    except Exception as exc:
        checks.append({"check": "suite_error", "passed": False, "message": str(exc)})
    passed = all(item["passed"] for item in checks)
    summary = {"ok": passed, "fixture_root": str(root), "checks": checks,
               "scope": "Synthetic fixtures; tool-level execution check, not business-level GIS acceptance"}
    (root / "batch4-validation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
