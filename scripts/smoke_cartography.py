"""Real cartography check for batch 3. Run with ArcGIS Pro Python, not system Python.

Exercises build_layout and export_layout through the production subprocess worker and creates
synthetic data only under workspace/smoke/<uuid>; all outputs are retained for inspection.
"""
from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from arcgis_agent.runtime import ArcPyRuntime

TITLE = "湖南probe专题图"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def message(result):
    error = result.get("error")
    if isinstance(error, dict):
        return error.get("message", "")
    return error or ""


class Traced:
    """Records every refusal, so a red run says which message came back instead of only which check failed."""

    def __init__(self, runtime):
        self.runtime, self.refusals = runtime, []

    def call(self, name, arguments, run):
        result = self.runtime.call(name, arguments, run)
        if result.get("ok") is not True:
            self.refusals.append({"tool": name, "output_name": arguments.get("output_name"),
                                  "error": message(result)[:200]})
        return result


def ink(path):
    """Fraction of non-white pixels: proves the map frame actually rendered the data.

    The threshold is <250 because Pro picks a random default fill colour for a new layer, and a pale
    fill is nearly white in luminance; measured on 3.5.2 a rendered sheet gives 0.36-0.61 here while
    the sheet's own title/legend linework stays far below 0.05.
    """
    try:
        from PIL import Image
    except ImportError:
        return {"available": False, "bytes": Path(path).stat().st_size}
    image = Image.open(str(path)).convert("L")
    pixels = list(image.getdata())
    return {"available": True, "bytes": Path(path).stat().st_size,
            "ratio": round(sum(1 for value in pixels if value < 250) / len(pixels), 5)}


def main():
    import arcpy

    root = (REPO / "workspace/smoke" / ("cartography_" + uuid.uuid4().hex)).resolve()
    root.mkdir(parents=True)
    gdb = arcpy.management.CreateFileGDB(str(root), "input.gdb")[0]
    polygons = arcpy.management.CreateFeatureclass(gdb, "regions", "POLYGON", spatial_reference=arcpy.SpatialReference(4326))[0]
    arcpy.management.AddField(polygons, "zone_name", "TEXT", field_length=32)
    with arcpy.da.InsertCursor(polygons, ["SHAPE@", "zone_name"]) as rows:
        for name, x0, x1 in (("北部区", 112.0, 113.0), ("南部区", 113.0, 114.0)):
            array = arcpy.Array([arcpy.Point(x0, 25.0), arcpy.Point(x1, 25.0), arcpy.Point(x1, 26.0),
                                 arcpy.Point(x0, 26.0), arcpy.Point(x0, 25.0)])
            rows.insertRow([arcpy.Polygon(array), name])
    metric = arcpy.management.CreateFeatureclass(gdb, "metric_zones", "POLYGON", spatial_reference=arcpy.SpatialReference(3857))[0]
    with arcpy.da.InsertCursor(metric, ["SHAPE@"]) as rows:
        array = arcpy.Array([arcpy.Point(12400000, 2900000), arcpy.Point(12700000, 2900000),
                             arcpy.Point(12700000, 3100000), arcpy.Point(12400000, 3100000),
                             arcpy.Point(12400000, 2900000)])
        rows.insertRow([arcpy.Polygon(array)])
    notes = arcpy.management.CreateTable(gdb, "notes")[0]
    arcpy.management.AddField(polygons, "zone_code", "LONG")
    with arcpy.da.UpdateCursor(polygons, ["zone_code"]) as rows:
        for row in rows:
            row[0] = 1
            rows.updateRow(row)
    dem = root / "zones.tif"
    arcpy.conversion.PolygonToRaster(polygons, "zone_code", str(dem), "CELL_CENTER", "#", 0.02)
    source_count = int(arcpy.management.GetCount(polygons)[0])

    runtime = Traced(ArcPyRuntime(Path(sys.executable)))
    run = root / "out"
    aprx = run / "cartography" / "hunan_map.aprx"
    checks, measures = {}, {}

    build = runtime.call("build_layout", {"title": TITLE, "layers": [polygons], "output_name": "hunan_map"}, run)
    data = build.get("data") or {}
    checks["build_layout"] = (build.get("ok") is True and aprx.is_file() and data.get("layout") == TITLE
                              and data.get("layers") == ["regions"] and data.get("page_mm") == [297.0, 210.0]
                              and data.get("extent_source") == "data_extent" and data.get("project_modified") is False)
    checks["aprx_saved_in_task_dir"] = aprx.is_file() and aprx.stat().st_size > 1000
    checks["legend_north_scale_created"] = (data.get("elements") or []) == ["图例", "指北针", "比例尺"]
    checks["auto_extent_matches_data"] = abs((data.get("extent") or {}).get("xmin", 0) - 112.0) < 1e-6
    checks["map_adopts_geographic_reference"] = (data.get("spatial_reference") or {}).get("wkid") == 4326

    before = digest(aprx)
    png = runtime.call("export_layout", {"project": str(aprx), "output_name": "sheet", "format": "PNG", "dpi": 300}, run)
    png_path = run / "cartography" / "sheet.png"
    checks["export_png"] = (png.get("ok") is True and png_path.is_file() and png_path.stat().st_size > 15000
                            and (png.get("data") or {}).get("dpi") == 300
                            and (png.get("data") or {}).get("project_modified") is False)
    checks["export_is_read_only"] = digest(aprx) == before
    measures["sheet_png"] = ink(png_path) if png_path.is_file() else {"missing": True}
    checks["geographic_sheet_renders_data"] = measures["sheet_png"].get("ratio", 0) > 0.05

    pdf = runtime.call("export_layout", {"project": str(aprx), "output_name": "sheet", "format": "PDF"}, run)
    pdf_path = run / "cartography" / "sheet.pdf"
    checks["export_pdf_default_dpi"] = pdf.get("ok") is True and pdf_path.is_file() and pdf_path.stat().st_size > 10000

    info = runtime.call("project_info", {"project": str(aprx)}, run)
    layouts = ((info.get("data") or {}).get("layouts") or [])
    checks["project_info_lists_layouts"] = (info.get("ok") is True and len(layouts) == 1
                                           and layouts[0]["name"] == TITLE and layouts[0]["page_units"] == "MILLIMETER"
                                           and layouts[0]["page_width"] == 297.0 and layouts[0]["element_count"] >= 4)

    second = runtime.call("build_layout", {"title": "A3 竖排", "layers": [polygons], "output_name": "dual",
                                           "page_size": "A3", "orientation": "PORTRAIT",
                                           "layout_style": "POSTER",
                                           "elements": ["LEGEND", "DUAL_SCALE_BAR"],
                                           "extent": {"xmin": 112.2, "ymin": 25.2, "xmax": 113.8, "ymax": 25.9}}, run)
    second_data = second.get("data") or {}
    second_aprx = run / "cartography" / "dual.aprx"
    checks["dual_scale_bar_portrait_extent"] = (second.get("ok") is True and second_aprx.is_file()
                                              and second_data.get("page_mm") == [297.0, 420.0]
                                              and second_data.get("layout_style") == "POSTER"
                                              and second_data.get("elements") == ["图例", "双比例尺"]
                                              and second_data.get("extent_source") == "requested")
    tiff = runtime.call("export_layout", {"project": str(second_aprx), "layout": "A3 竖排", "output_name": "sheet",
                                         "format": "TIFF", "dpi": 150}, run)
    checks["export_tiff_named_layout"] = (tiff.get("ok") is True and (run / "cartography" / "sheet.tif").is_file()
                                          and (tiff.get("data") or {}).get("layout") == "A3 竖排")

    projected = runtime.call("build_layout", {"title": "墨卡托专题图", "layers": [metric], "output_name": "mercator"}, run)
    projected_data = projected.get("data") or {}
    projected_aprx = run / "cartography" / "mercator.aprx"
    checks["projected_layer_build"] = (projected.get("ok") is True and projected_aprx.is_file()
                                       and (projected_data.get("spatial_reference") or {}).get("wkid") == 3857
                                       and abs((projected_data.get("extent") or {}).get("xmin", 0) - 12400000) < 1)
    projected_png = runtime.call("export_layout", {"project": str(projected_aprx), "output_name": "mercator",
                                                   "format": "PNG", "dpi": 150}, run)
    measures["mercator_png"] = ink(run / "cartography" / "mercator.png") if projected_png.get("ok") else {"failed": message(projected_png)}
    checks["projected_sheet_renders_data"] = measures["mercator_png"].get("ratio", 0) > 0.05

    plain = runtime.call("build_layout", {"title": "无图廓", "layers": [polygons], "output_name": "plain",
                                          "elements": []}, run)
    plain_data = plain.get("data") or {}
    plain_png = runtime.call("export_layout", {"project": str(run / "cartography" / "plain.aprx"),
                                               "output_name": "plain", "format": "PNG", "dpi": 96}, run)
    checks["empty_elements_build_a_clean_sheet"] = (plain.get("ok") is True and plain_data.get("elements") == []
                                                    and plain_png.get("ok") is True
                                                    and not ({"图例", "指北针", "比例尺", "双比例尺"}
                                                             & set((plain_png.get("data") or {}).get("elements") or [])))

    jpeg = runtime.call("export_layout", {"project": str(aprx), "output_name": "sheet", "format": "JPEG", "dpi": 96}, run)
    svg = runtime.call("export_layout", {"project": str(aprx), "output_name": "sheet", "format": "SVG", "dpi": 96}, run)
    checks["jpeg_and_svg_export"] = (jpeg.get("ok") is True and (run / "cartography" / "sheet.jpg").stat().st_size > 1000
                                     and svg.get("ok") is True and (run / "cartography" / "sheet.svg").stat().st_size > 1000)

    raster = runtime.call("build_layout", {"title": "栅格专题图", "layers": [str(dem)], "output_name": "raster"}, run)
    raster_png = runtime.call("export_layout", {"project": str(run / "cartography" / "raster.aprx"),
                                                "output_name": "raster", "format": "PNG", "dpi": 96}, run)
    measures["raster_png"] = ink(run / "cartography" / "raster.png") if raster_png.get("ok") else {"failed": message(raster_png)}
    checks["raster_layer_sheet"] = (raster.get("ok") is True and raster_png.get("ok") is True
                                    and measures["raster_png"].get("ratio", 0) > 0.05)

    again = runtime.call("build_layout", {"title": TITLE, "layers": [polygons], "output_name": "hunan_map"}, run)
    checks["no_overwrite_guard"] = again.get("ok") is False and "禁止覆盖" in message(again)

    table_only = runtime.call("build_layout", {"title": "表", "layers": [notes], "output_name": "tables"}, run)
    checks["table_layer_refused"] = table_only.get("ok") is False and "没有几何" in message(table_only)
    checks["no_project_for_refused_build"] = not (run / "cartography" / "tables.aprx").exists()

    mixed = runtime.call("build_layout", {"title": "混", "layers": [polygons, metric], "output_name": "mixed"}, run)
    checks["mixed_spatial_reference_refused"] = mixed.get("ok") is False and "坐标系不一致" in message(mixed)

    # SpatialReference(4326).type is 'Geographic', so metre boxes must be refused before the camera is set.
    degree_box = runtime.call("build_layout", {"title": "单位错", "layers": [polygons], "output_name": "wrongunit",
                                               "extent": {"xmin": 12400000.0, "ymin": 2900000.0,
                                                          "xmax": 12700000.0, "ymax": 3100000.0}}, run)
    checks["metre_box_refused_for_geographic_layer"] = (degree_box.get("ok") is False
                                                        and "地理坐标系" in message(degree_box))
    checks["no_project_for_refused_extent"] = not (run / "cartography" / "wrongunit.aprx").exists()
    metre_build = runtime.call("build_layout", {"title": "投影带范围", "layers": [metric], "output_name": "boxed",
                                                "extent": {"xmin": 12450000.0, "ymin": 2950000.0,
                                                           "xmax": 12650000.0, "ymax": 3050000.0}}, run)
    boxed_data = metre_build.get("data") or {}
    checks["metre_box_accepted_for_projected_layer"] = (metre_build.get("ok") is True
                                                        and boxed_data.get("extent_source") == "requested"
                                                        and abs(boxed_data.get("extent", {}).get("xmin", 0)
                                                                - 12450000.0) < 1e-6)

    unknown = runtime.call("export_layout", {"project": str(aprx), "layout": "不存在的布局",
                                            "output_name": "none", "format": "PNG"}, run)
    checks["unknown_layout_refused"] = unknown.get("ok") is False and "没有名为" in message(unknown)

    missing = runtime.call("export_layout", {"project": str(root / "nope.aprx"), "output_name": "none2",
                                            "format": "PNG"}, run)
    checks["missing_project_refused"] = missing.get("ok") is False and "工程文件不存在" in message(missing)
    checks["no_partial_export_file"] = not (run / "cartography" / "none.png").exists()

    # Only files that were actually published may remain: no staged .<uuid>. files, no empty leftovers.
    leftover = [path.name for path in (run / "cartography").iterdir()
                if len(path.stem.split(".")) > 1 or path.name.endswith((".aprx", ".png", ".pdf", ".tif", ".jpg", ".svg")) is False]
    checks["only_published_files_remain"] = leftover == []
    measures["cartography_dir"] = sorted(path.name for path in (run / "cartography").iterdir())

    checks["source_data_unchanged"] = int(arcpy.management.GetCount(polygons)[0]) == source_count

    print(json.dumps({"ok": all(checks.values()), "checks": checks, "measures": measures,
                      "refusals": runtime.refusals,
                      "version": arcpy.GetInstallInfo()["Version"], "run_dir": str(root)},
                     ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
