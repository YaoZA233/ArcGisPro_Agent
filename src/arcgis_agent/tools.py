from __future__ import annotations

import sys
import time
from pathlib import Path
from .catalog import TOOLS, Tool, validate_call, approval_details


def _progress(message: str) -> None:
    # Best-effort phase tracing for timeout diagnosis; must never break a tool run.
    try:
        print(message, file=sys.stderr, flush=True)
    except (OSError, UnicodeError):
        pass


def _layer_record(layer):
    record = {"name": layer.name}
    try:
        source = layer.dataSource
    except AttributeError:
        source = None
    if layer.isGroupLayer:
        record["is_broken"] = None
        record["broken_check"] = "skipped: group layer, children are listed separately"
    elif isinstance(source, str) and source.lower().startswith(("http:", "https:")):
        # isBroken on a service layer issues a live network request that can stall for minutes.
        record["is_broken"] = None
        record["broken_check"] = "skipped: online service, no network probe"
    else:
        record["is_broken"] = layer.isBroken
    return record


def _layout_record(layout):
    return {"name": layout.name, "page_width": layout.pageWidth, "page_height": layout.pageHeight,
            "page_units": layout.pageUnits, "element_count": len(layout.listElements())}


def execute(name: str, arguments: dict, output_dir: Path, *, allow_current: bool = False, selection_snapshot=None, skills_dir: Path | None = None) -> dict:
    """Allowlisted ArcPy calls only. No eval/exec, generated Python, or shell tools."""
    import sys

    validate_call(name, arguments)
    from .skills import SKILL_TOOLS, execute_skill_tool
    if name in SKILL_TOOLS:
        # Pure local reads; handled before the arcpy import so they work offline.
        return execute_skill_tool(name, arguments, skills_dir)
    if name == "export_selection":
        from .selections import validate_snapshot
        selection_snapshot = validate_snapshot(selection_snapshot, arguments["selection_token"])
    elif TOOLS[name].host:
        raise ValueError("This tool requires the ArcGIS Pro chat panel host bridge")
    elif selection_snapshot is not None:
        raise ValueError("Selection payload is allowed only for export_selection")
    if arguments.get("project", "").upper() == "CURRENT" and not allow_current:
        raise ValueError("CURRENT is available only inside the ArcGIS Pro Python toolbox; use an .aprx path from CLI")
    # Separate Python startup from ArcPy import when diagnosing an intermittent delay.
    _progress("[tools] importing arcpy")
    arcpy_start = time.monotonic()
    import arcpy
    _progress(f"[tools] arcpy ready in {time.monotonic() - arcpy_start:.1f}s")

    if name == "export_selection":
        from .gis_tools import export_selected_ids
        return export_selected_ids(arcpy, selection_snapshot, arguments["output_name"], output_dir)

    if name == "environment":
        info = arcpy.GetInstallInfo()
        return {"version": info.get("Version"), "build": info.get("BuildNumber"),
                "product": arcpy.ProductInfo(), "python": sys.executable,
                "python_version": sys.version.split()[0], "install_dir": info.get("InstallDir")}
    if name == "project_info":
        _progress("[project_info] opening project")
        project = arcpy.mp.ArcGISProject(arguments["project"])
        try:
            maps = project.listMaps()
            _progress(f"[project_info] project opened; maps={len(maps)}")
            map_records = []
            for m in maps:
                layers = m.listLayers()
                _progress(f"[project_info] map {m.name!r}: {len(layers)} top-level layers")
                records = []
                for layer in layers:
                    _progress(f"[project_info] layer {layer.name!r}")
                    records.append(_layer_record(layer))
                map_records.append({"name": m.name, "layers": records})
            return {"project": project.filePath, "maps": map_records,
                    "layouts": [_layout_record(layout) for layout in project.listLayouts()]}
        finally:
            del project
    if name not in ("describe", "buffer"):
        from .gis_tools import execute_extended
        return execute_extended(name, arguments, output_dir, arcpy)
    dataset = arguments["dataset"]
    if not arcpy.Exists(dataset):
        raise ValueError(f"GIS dataset does not exist: {dataset}")
    desc = arcpy.Describe(dataset)
    from .gis_tools import assert_real_gdb
    assert_real_gdb(dataset, desc)
    if name == "describe":
        sr = getattr(desc, "spatialReference", None)
        fields = list(getattr(desc, "fields", []))
        result = {"path": desc.catalogPath, "data_type": desc.dataType,
                  "shape_type": getattr(desc, "shapeType", None),
                  "spatial_reference": {"name": sr.name, "wkid": sr.factoryCode} if sr else None,
                  "fields": [{"name": f.name, "type": f.type, "length": f.length, "nullable": f.isNullable,
                              "required": f.required, "editable": f.editable} for f in fields[:200]], "fields_truncated": len(fields) > 200}
        extent = getattr(desc, "extent", None)
        if extent:
            result["extent"] = {"xmin": extent.XMin, "ymin": extent.YMin, "xmax": extent.XMax, "ymax": extent.YMax}
        if desc.dataType in ("FeatureClass", "FeatureLayer", "Table", "TableView", "ShapeFile"):
            result["count"] = int(arcpy.management.GetCount(dataset)[0])
        return result
    if name == "buffer":
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        gdb = output_dir / "results.gdb"
        if gdb.exists() and (not arcpy.Exists(str(gdb)) or arcpy.Describe(str(gdb)).dataType != "Workspace"):
            raise ValueError(f"Not an ArcGIS geodatabase: {gdb}")
        with arcpy.EnvManager(overwriteOutput=False, addOutputsToMap=False):
            if not arcpy.Exists(str(gdb)):
                arcpy.management.CreateFileGDB(str(output_dir), gdb.name)
            output = str(gdb / arguments["output_name"])
            if arcpy.Exists(output):
                raise ValueError(f"Output already exists; overwrite is disabled: {output}")
            arcpy.analysis.Buffer(dataset, output, arguments["distance"], method="GEODESIC")
            return {"output": output, "count": int(arcpy.management.GetCount(output)[0]),
                    "distance": arguments["distance"], "method": "GEODESIC"}
    raise ValueError(f"Unimplemented tool: {name}")
