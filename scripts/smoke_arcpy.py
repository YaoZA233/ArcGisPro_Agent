"""Real local integration check. Run with ArcGIS Pro Python, not system Python.

Creates isolated synthetic data under workspace/smoke/<uuid>; retains all outputs.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from arcgis_agent.agent import Agent, PlanPlanner
from arcgis_agent.runtime import ArcPyRuntime


def main():
    import arcpy

    root = REPO / "workspace/smoke" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    gdb = arcpy.management.CreateFileGDB(str(root), "input.gdb")[0]
    points = arcpy.management.CreateFeatureclass(gdb, "points", "POINT", spatial_reference=arcpy.SpatialReference(4326))[0]
    with arcpy.da.InsertCursor(points, ["SHAPE@XY"]) as cursor:
        cursor.insertRow(((112.98, 28.20),))
        cursor.insertRow(((113.00, 28.22),))
    steps = [{"tool": "describe", "arguments": {"dataset": points}},
             {"tool": "buffer", "arguments": {"dataset": points, "distance": "500 Meters", "output_name": "buffer_500m"}}]
    def approve_fixture(request):
        if not Path(request["arguments"]["dataset"]).resolve().is_relative_to(root.resolve()):
            return {"decision": "deny"}
        print(json.dumps({"fixture_approval": request["tool"], "writes": request["writes"]}), flush=True)
        return {"decision": "allow", "allow_writes": request["writes"]}
    result = Agent(ArcPyRuntime(Path(sys.executable)), root).run(PlanPlanner(steps), approve=approve_fixture)
    if result["status"] != "completed":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    output = result["observations"][-1]["data"]["output"]
    count = int(arcpy.management.GetCount(output)[0])
    areas = [row[0].getArea("GEODESIC", "SQUAREMETERS") for row in arcpy.da.SearchCursor(output, ["SHAPE@"])]
    checks = {"buffer_areas": count == 2 and len(areas) == 2 and all(700000 < area < 850000 for area in areas)}

    runtime = ArcPyRuntime(Path(sys.executable))
    manual = root / "manual"
    table = root / "stations.csv"
    table.write_text("name,latitude,longitude,note\na,28.20,112.98,ok\nb,28.22,113.00,ok\nc,bad,113.00,bad\n",
                     encoding="utf-8")
    imported = runtime.call("import_table_points", {"dataset": str(table), "output_name": "stations"}, manual)
    data = imported.get("data") or {}
    checks["import_table_points"] = (imported.get("ok") is True and data.get("count") == 2
                                     and data.get("skipped_rows") == 1 and data.get("lat_field") == "latitude"
                                     and data.get("wkid") == 4326)

    fake = root / "fake.gdb"
    fake.mkdir()
    guarded = runtime.call("describe", {"dataset": str(fake)}, manual)
    checks["fake_gdb_guard"] = (guarded.get("ok") is False
                                and "real geodatabase" in guarded["error"]["message"].lower())

    arcpy.management.CreateSQLiteDatabase(str(root / "conv.gpkg"), "GEOPACKAGE")
    gpkg_member = str(root / "conv.gpkg" / "pts_src")
    arcpy.management.CopyFeatures(points, gpkg_member)
    converted = runtime.call("convert_dataset", {"dataset": gpkg_member, "output_name": "from_gpkg"}, manual)
    checks["convert_dataset_gpkg"] = (converted.get("ok") is True
                                      and (converted.get("data") or {}).get("count") == 2)

    # --- batch 2: spatial analysis, overlay, reshape and joins through the real subprocess worker ---
    grid = arcpy.management.CreateFeatureclass(gdb, "grid", "POINT", spatial_reference=arcpy.SpatialReference(4326))[0]
    arcpy.management.AddField(grid, "zval", "DOUBLE")
    with arcpy.da.InsertCursor(grid, ["SHAPE@XY", "zval"]) as cursor:
        value = 10.0
        for row in range(3):
            for column in range(3):
                cursor.insertRow(((112.90 + column * 0.02, 28.10 + row * 0.02), value))
                value += 5
    zones = arcpy.management.CreateFeatureclass(gdb, "zones", "POLYGON", spatial_reference=arcpy.SpatialReference(4326))[0]
    arcpy.management.AddField(zones, "zone_code", "LONG")
    arcpy.management.AddField(zones, "zone_name", "TEXT", field_length=20)
    # Zone boundary sits inside the interpolated extent (112.90–112.94) so zonal statistics sees both zones.
    for code, name, x0, x1 in ((1, "alpha", 112.85, 112.92), (2, "beta", 112.92, 113.15)):
        ring = [(x0, 28.05), (x1, 28.05), (x1, 28.30), (x0, 28.30), (x0, 28.05)]
        with arcpy.da.InsertCursor(zones, ["SHAPE@", "zone_code", "zone_name"]) as cursor:
            cursor.insertRow((arcpy.Polygon(arcpy.Array([arcpy.Point(*p) for p in ring])), code, name))
    mask = arcpy.management.CreateFeatureclass(gdb, "mask", "POLYGON", spatial_reference=arcpy.SpatialReference(4326))[0]
    with arcpy.da.InsertCursor(mask, ["SHAPE@"]) as cursor:
        cursor.insertRow((arcpy.Polygon(arcpy.Array([arcpy.Point(*p) for p in
                      [(112.94, 28.14), (113.06, 28.14), (113.06, 28.26), (112.94, 28.26), (112.94, 28.14)]])),))
    labels = arcpy.management.CreateTable(gdb, "labels")[0]
    arcpy.management.AddField(labels, "zone_key", "LONG")
    arcpy.management.AddField(labels, "label_text", "TEXT", field_length=30)
    with arcpy.da.InsertCursor(labels, ["zone_key", "label_text"]) as cursor:
        cursor.insertRow((1, "north"))
        cursor.insertRow((2, "south"))

    def call(tool, arguments):
        response = runtime.call(tool, arguments, manual)
        print(json.dumps({"tool": tool, "ok": response.get("ok"),
                          "error": (response.get("error") or {}).get("message"),
                          "hint": (response.get("error") or {}).get("hint"),
                          "data": {k: v for k, v in (response.get("data") or {}).items()
                                   if k in ("output", "count", "width", "height", "pixel_type", "output_fields",
                                            "joined_fields", "statistics", "method")}}, ensure_ascii=False), flush=True)
        return response

    surface = call("interpolate_surface", {"dataset": grid, "value_field": "zval", "method": "IDW", "cell_size": 0.005,
                                          "power": 2, "output_name": "surface"})
    checks["interpolate_idw"] = bool((surface.get("data") or {}).get("width", 0) > 0
                                     and surface["data"]["spatial_reference"]["wkid"] == 4326)
    kriging = call("interpolate_surface", {"dataset": grid, "value_field": "zval", "method": "KRIGING", "cell_size": 0.005,
                                          "variogram": "LINEAR", "output_name": "kriging"})
    checks["interpolate_kriging"] = bool((kriging.get("data") or {}).get("width", 0) > 0)
    raster = (surface.get("data") or {}).get("output") or str(root / "manual" / "surface.tif")
    zone_raster = call("polygon_to_raster", {"dataset": zones, "value_field": "zone_code", "cell_size": 0.005, "output_name": "zone_grid"})
    checks["polygon_to_raster"] = bool((zone_raster.get("data") or {}).get("width", 0) > 0)
    density = call("kernel_density", {"dataset": grid, "value_field": "zval", "cell_size": 0.005, "search_radius": 0.03,
                                      "area_unit": "SQUARE_KILOMETERS", "output_name": "density"})
    checks["kernel_density"] = bool((density.get("data") or {}).get("width", 0) > 0)
    zonal = call("zonal_statistics", {"zone_dataset": zones, "zone_field": "zone_code", "value_raster": raster,
                                      "statistic": "ALL", "output_name": "zone_summary"})
    checks["zonal_statistics"] = ((zonal.get("data") or {}).get("count") == 2
                                  and "MEAN" in (zonal["data"].get("output_fields") or [])
                                  and {row[0] for row in arcpy.da.SearchCursor(zonal["data"]["output"], ["zone_code"])} == {1, 2}
                                  and all(row[0] is not None for row in arcpy.da.SearchCursor(zonal["data"]["output"], ["MEAN"])))
    bad_zone = call("zonal_statistics", {"zone_dataset": zones, "zone_field": "zone_name", "value_raster": raster,
                                         "statistic": "MEAN", "output_name": "zone_text"})
    checks["zonal_rejects_text_zone"] = (bad_zone.get("ok") is False and "分区字段" in (bad_zone["error"]["message"] or "")
                                         and not arcpy.Exists(str(manual / "results.gdb" / "zone_text")))
    extracted = call("extract_by_mask", {"dataset": raster, "mask_dataset": mask, "output_name": "masked"})
    checks["extract_by_mask"] = bool((extracted.get("data") or {}).get("width", 0) > 0
                                     and extracted["data"]["extent"]["xmax"] <= 113.07)
    sampled = call("sample_raster_at_points", {"dataset": grid, "rasters": [raster, (zone_raster.get("data") or {}).get("output", raster)],
                                               "output_name": "raster_values"})
    checks["sample_raster"] = ((sampled.get("data") or {}).get("count") == 9
                               and len(sampled["data"].get("output_fields") or []) >= 4)
    slope = call("terrain_derivatives", {"dataset": raster, "derivative": "SLOPE", "slope_measurement": "DEGREE",
                                         "z_factor": 1, "output_name": "slope"})
    aspect = call("terrain_derivatives", {"dataset": raster, "derivative": "ASPECT", "z_unit": "METER",
                                          "output_name": "aspect"})
    checks["terrain_derivatives"] = (bool((slope.get("data") or {}).get("width", 0) > 0)
                                     and bool((aspect.get("data") or {}).get("width", 0) > 0))
    contours = call("contour_lines", {"dataset": raster, "interval": 10, "output_name": "contours"})
    checks["contour_lines"] = ((contours.get("data") or {}).get("count", 0) > 0
                               and contours["data"].get("shape_type") == "Polyline")
    summary = call("summarize_statistics", {"dataset": zones, "stats": [{"field": "zone_code", "statistic": "SUM"},
                                                                        {"field": "zone_code", "statistic": "MEAN"}],
                                            "case_field": "zone_name", "output_name": "zone_stats"})
    checks["summarize_statistics"] = ((summary.get("data") or {}).get("count") == 2
                                      and "SUM_zone_code" in (summary["data"].get("output_fields") or []))
    joined = call("join_table", {"dataset": zones, "join_dataset": labels, "target_field": "zone_code",
                                 "join_field": "zone_key", "fields": ["label_text"], "output_name": "zones_labelled"})
    checks["join_table"] = ((joined.get("data") or {}).get("count") == 2
                            and joined["data"].get("joined_fields") == ["label_text"]
                            and sorted(row[0] for row in arcpy.da.SearchCursor(joined["data"]["output"], ["label_text"])) == ["north", "south"])
    bad_join = call("join_table", {"dataset": zones, "join_dataset": labels, "target_field": "zone_code",
                                   "join_field": "zone_key", "fields": ["nope"], "output_name": "zones_bad"})
    checks["join_rejects_missing_column"] = (bad_join.get("ok") is False and "unavailable" in (bad_join["error"]["message"] or ""))
    overlaid = call("union", {"datasets": [zones, mask], "keep_attributes": "ALL", "output_name": "unioned"})
    checks["union"] = ((overlaid.get("data") or {}).get("count", 0) > 2
                       and overlaid["data"].get("shape_type") == "Polygon")
    bad_union = call("union", {"datasets": [zones, grid], "output_name": "union_points"})
    checks["union_rejects_non_polygon"] = (bad_union.get("ok") is False and "叠合分析" in (bad_union["error"]["message"] or ""))
    erased = call("erase", {"dataset": zones, "erase_dataset": mask, "output_name": "erased"})
    checks["erase"] = (erased.get("ok") is True and (erased.get("data") or {}).get("count", 0) >= 1)
    reshaped = {}
    for operation, extra in (("CENTROID", {}), ("SIMPLIFY", {"tolerance": "0.001 Meters"}), ("MIN_BOUNDING", {"geometry_type": "CIRCLE"})):
        arguments = {"dataset": zones if operation != "MIN_BOUNDING" else grid, "operation": operation,
                     "output_name": "reshaped_" + operation.lower(), **extra}
        reshaped[operation] = call("reshape_geometry", arguments)
    checks["reshape_centroid"] = (reshaped["CENTROID"].get("data") or {}).get("count") == 2
    checks["reshape_simplify"] = (reshaped["SIMPLIFY"].get("data") or {}).get("count") == 2
    # group_option defaults to NONE, so MIN_BOUNDING returns a single enclosing geometry for all inputs.
    checks["reshape_min_bounding"] = (reshaped["MIN_BOUNDING"].get("data") or {}).get("count") == 1

    untouched = int(arcpy.management.GetCount(zones)[0]) == 2 and int(arcpy.management.GetCount(grid)[0]) == 9
    checks["source_data_unchanged"] = untouched

    ok = all(checks.values())
    print(json.dumps({"ok": ok, "checks": checks, "version": arcpy.GetInstallInfo()["Version"],
                      "run_dir": result["run_dir"], "output": output}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
