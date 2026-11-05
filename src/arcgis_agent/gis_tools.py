"""Allowlisted geoprocessing. Imports ArcPy only through the licensed runtime."""
from __future__ import annotations

import itertools
import json
import math
import uuid
from pathlib import Path


def check_dataset(arcpy, dataset):
    if not arcpy.Exists(dataset):
        raise ValueError(f"GIS dataset does not exist: {dataset}")
    desc = arcpy.Describe(dataset)
    assert_real_gdb(dataset, desc)
    return desc


def require_extension(arcpy, name):
    """Check out an ArcGIS extension before any analysis runs; never degrade to a partial result."""
    status = str(arcpy.CheckExtension(name))
    if status == "Available":
        status = str(arcpy.CheckOutExtension(name))
    if status not in ("Available", "AlreadyCheckedOut", "CheckedOut"):
        raise ValueError(f"ArcGIS 扩展模块 {name} 当前不可用（状态 {status}），分析工具未执行；请确认已安装并许可 {name}。")


def assert_real_gdb(dataset, desc):
    if str(dataset).lower().endswith(".gdb") and desc.dataType != "Workspace":
        raise ValueError(f"Not a real geodatabase (plain directory or corrupt): {dataset}")


def _fields(arcpy, dataset, names):
    available = {f.name: f for f in arcpy.ListFields(dataset)}
    for name in names:
        if name not in available or available[name].type in ("Geometry", "Blob", "Raster"):
            raise ValueError(f"Field unavailable or unsupported: {name}")
    return available


def _field_value(field, value):
    if value is None:
        if not field.isNullable:
            raise ValueError("The selected field does not allow null values")
        return None
    if field.type == "String":
        if not isinstance(value, str) or len(value) > field.length:
            raise ValueError("Text value exceeds the field length or has the wrong type")
    elif field.type in ("SmallInteger", "Integer", "BigInteger", "OID"):
        if type(value) is not int:
            raise ValueError("Integer field requires an integer value")
        lower, upper = {"SmallInteger": (-32768, 32767), "Integer": (-2147483648, 2147483647),
                        "BigInteger": (-(2**53), 2**53), "OID": (0, 2**53)}[field.type]
        if not lower <= value <= upper:
            raise ValueError("Integer value is out of the field range")
    elif field.type in ("Single", "Double"):
        if type(value) not in (int, float):
            raise ValueError("Numeric field requires a number")
    else:
        raise ValueError("This tool supports text and numeric fields only")
    return value


def where_clause(arcpy, dataset, condition):
    if condition is None:
        return None
    if "all" in condition or "any" in condition:
        key = "all" if "all" in condition else "any"
        joiner = " AND " if key == "all" else " OR "
        return joiner.join("(" + _condition_clause(arcpy, dataset, leaf) + ")" for leaf in condition[key])
    return _condition_clause(arcpy, dataset, condition)


def _condition_clause(arcpy, dataset, condition):
    field = _fields(arcpy, dataset, [condition["field"]])[condition["field"]]
    name = arcpy.AddFieldDelimiters(dataset, field.name)
    operator, value = condition["operator"], condition.get("value")
    if operator in ("IS_NULL", "IS_NOT_NULL"):
        if condition.get("value") is not None:
            raise ValueError("IS_NULL/IS_NOT_NULL requires value=null")
        return name + (" IS NULL" if operator == "IS_NULL" else " IS NOT NULL")
    if operator in ("IN", "NOT_IN"):
        literals = []
        for item in condition["values"]:
            if item is None:
                raise ValueError("IN lists cannot contain null; combine with an IS_NULL condition instead")
            _field_value(field, item)
            literals.append("'" + item.replace("'", "''") + "'" if isinstance(item, str) else str(item))
        clause = name + " IN (" + ",".join(literals) + ")"
        return "NOT " + clause if operator == "NOT_IN" else clause
    if value is None:
        if operator not in ("EQ", "NE"):
            raise ValueError("Null comparison supports EQ/NE only")
        return name + (" IS NULL" if operator == "EQ" else " IS NOT NULL")
    if operator in ("CONTAINS", "NOT_CONTAINS", "STARTS_WITH"):
        if field.type != "String" or not isinstance(value, str):
            raise ValueError(f"{operator} requires a text field and a text value")
        literal = "'" + value.replace("'", "''") + "'"
        pattern = {"CONTAINS": "LIKE '%" + literal[1:-1] + "%'", "STARTS_WITH": "LIKE '" + literal[1:-1] + "%'",
                   "NOT_CONTAINS": "NOT LIKE '%" + literal[1:-1] + "%'"}[operator]
        return name + " " + pattern
    _field_value(field, value)
    literal = "'" + value.replace("'", "''") + "'" if isinstance(value, str) else str(value)
    return name + " " + {"EQ": "=", "NE": "<>", "GT": ">", "GE": ">=", "LT": "<", "LE": "<="}[operator] + " " + literal


def _output(arcpy, folder, name, raster=False):
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if raster:
        path = folder / (name + ".tif")
    else:
        gdb = folder / "results.gdb"
        if gdb.exists() and (not arcpy.Exists(str(gdb)) or arcpy.Describe(str(gdb)).dataType != "Workspace"):
            raise ValueError("Task output geodatabase is invalid")
        if not arcpy.Exists(str(gdb)):
            arcpy.management.CreateFileGDB(str(folder), gdb.name)
        if arcpy.ValidateTableName(name, str(gdb)) != name:
            raise ValueError("Output name is not valid for a geodatabase")
        path = gdb / name
    if arcpy.Exists(str(path)) or path.exists():
        raise ValueError(f"Output already exists; overwrite is disabled: {path}")
    return str(path)


def _copy(arcpy, source, output):
    if arcpy.Describe(source).dataType in ("Table", "TableView"):
        arcpy.management.CopyRows(source, output)
    else:
        arcpy.management.CopyFeatures(source, output)


def _file_output(folder, name, extension):
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + extension)
    if path.exists():
        raise ValueError(f"Output already exists; overwrite is disabled: {path}")
    return str(path)


def _referenced_fields(expression):
    import re
    return sorted(set(re.findall(r"!([^!]+)!", expression)))


def _expression_tree(expression):
    import ast
    return ast.parse("".join(expression.split("!")), mode="eval")


def _eval_expression(tree, values):
    import ast
    import operator
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
              ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
    unary = {ast.USub: operator.neg, ast.UAdd: operator.pos}

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            return binary[type(node.op)](visit(node.left), visit(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in unary:
            return unary[type(node.op)](visit(node.operand))
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.Name):
            value = values.get(node.id)
            if value is None:
                raise ValueError(f"字段 {node.id} 存在空值，无法参与算术计算；先用 read_rows 检查空值或筛选排除")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"字段 {node.id} 的值不是数值，无法参与算术计算")
            return value
        raise ValueError(f"不支持的表达式节点 {type(node).__name__}")
    return visit(tree)


NUMERIC_FIELD_TYPES = ("SmallInteger", "Integer", "BigInteger", "Single", "Double", "OID")


def _verify_dataset(arcpy, args, dataset, desc):
    checks, failures = [], []

    def record(check, passed, expected=None, actual=None):
        item = {"check": check, "passed": bool(passed)}
        if expected is not None:
            item["expected"] = expected
        if actual is not None:
            item["actual"] = actual
        checks.append(item)
        if not passed:
            failures.append(item)

    count = None
    if desc.dataType not in ("RasterDataset", "RasterBand", "MosaicDataset", "Workspace"):
        count = int(arcpy.management.GetCount(dataset)[0])
        record("count_readable", True, actual=count)
        if "min_count" in args or "max_count" in args:
            expected = {}
            if "min_count" in args and count < args["min_count"]:
                record("min_count", False, expected=args["min_count"], actual=count)
            elif "min_count" in args:
                record("min_count", True, expected=args["min_count"], actual=count)
            if "max_count" in args and count > args["max_count"]:
                record("max_count", False, expected=args["max_count"], actual=count)
            elif "max_count" in args:
                record("max_count", True, expected=args["max_count"], actual=count)
    if "fields" in args:
        present = {f.name for f in arcpy.ListFields(dataset)}
        missing = [name for name in args["fields"] if name not in present]
        record("fields_present", not missing, expected=args["fields"], actual=missing or None)
    if "non_null_fields" in args or "unique_fields" in args:
        columns = list(dict.fromkeys(args.get("non_null_fields", []) + args.get("unique_fields", [])))
        seen = {name: set() for name in args.get("unique_fields", [])}
        broken_null, broken_unique = [], []
        with arcpy.da.SearchCursor(dataset, columns) as cursor:
            for row in cursor:
                for name in args.get("non_null_fields", []):
                    if row[columns.index(name)] is None and name not in broken_null:
                        broken_null.append(name)
                for name in args.get("unique_fields", []):
                    value = row[columns.index(name)]
                    if value is not None:
                        if value in seen[name] and name not in broken_unique:
                            broken_unique.append(name)
                        seen[name].add(value)
        if "non_null_fields" in args:
            record("non_null", not broken_null, expected=args["non_null_fields"],
                   actual=broken_null or None)
        if "unique_fields" in args:
            record("unique", not broken_unique, expected=args["unique_fields"],
                   actual=broken_unique or None)
    if "wkid" in args:
        sr = getattr(desc, "spatialReference", None)
        actual = sr.factoryCode if sr else None
        record("wkid", actual == args["wkid"], expected=args["wkid"], actual=actual)
    if "extent_within" in args:
        extent = getattr(desc, "extent", None)
        if extent is None:
            record("extent_within", False, expected=args["extent_within"], actual="no extent")
        else:
            box = args["extent_within"]
            inside = (extent.XMin >= box["xmin"] and extent.YMin >= box["ymin"]
                      and extent.XMax <= box["xmax"] and extent.YMax <= box["ymax"])
            record("extent_within", inside, expected=box,
                   actual={"xmin": extent.XMin, "ymin": extent.YMin, "xmax": extent.XMax, "ymax": extent.YMax})
    return {"dataset": dataset, "checks": checks, "check_count": len(checks),
            "passed": not failures, "failures": failures, "source_modified": False,
            "messages": arcpy.GetMessages()[-4000:]}


LAT_COLUMNS = ("lat", "latitude", "y", "ycoord", "y_coord", "weidu", "纬度")
LON_COLUMNS = ("lon", "lng", "long", "longitude", "x", "xcoord", "x_coord", "jingdu", "经度")


def _pick_column(names, explicit, candidates, label):
    if explicit:
        if explicit not in names:
            raise ValueError(f"{label} column not in table; available: " + ", ".join(names[:20]))
        return explicit
    lowered = {name.lower(): name for name in names}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    raise ValueError(f"Cannot identify the {label} column; pass it explicitly. Available: " + ", ".join(names[:20]))


def _import_table_points(arcpy, args, folder, desc, dataset):
    fields = [f for f in desc.fields if f.type not in ("OID", "Geometry", "Blob", "Raster")]
    if not fields:
        raise ValueError("Table has no readable attribute fields")
    names = [f.name for f in fields]
    lat = _pick_column(names, args.get("lat_field"), LAT_COLUMNS, "latitude")
    lon = _pick_column(names, args.get("lon_field"), LON_COLUMNS, "longitude")
    sr = arcpy.SpatialReference(args.get("wkid", 4326))
    output = _output(arcpy, folder, args["output_name"])
    gdb, member = str(Path(output).parent), Path(output).name
    arcpy.management.CreateFeatureclass(gdb, member, "POINT", spatial_reference=sr)
    for field in fields:
        arcpy.management.AddField(output, field.name, field.type, field_length=field.length or None)
    inserted = skipped = 0
    lat_index, lon_index = names.index(lat), names.index(lon)
    with arcpy.da.InsertCursor(output, names + ["SHAPE@XY"]) as writer, \
            arcpy.da.SearchCursor(dataset, names) as reader:
        for row in reader:
            try:
                x, y = float(row[lon_index]), float(row[lat_index])
            except (TypeError, ValueError):
                skipped += 1
                continue
            if not (math.isfinite(x) and math.isfinite(y)):
                skipped += 1
                continue
            writer.insertRow(list(row) + [(x, y)])
            inserted += 1
    if inserted == 0:
        raise ValueError("No row had usable coordinates; nothing was imported")
    return {"output": output, "count": inserted, "skipped_rows": skipped, "lat_field": lat, "lon_field": lon,
            "wkid": sr.factoryCode, "source": dataset, "source_modified": False,
            "messages": arcpy.GetMessages()[-4000:]}


def _convert_dataset(arcpy, args, folder, dataset):
    if dataset.lower().endswith((".kml", ".kmz")):
        target = folder.resolve() / ("kml_" + args["output_name"])
        if target.exists():
            raise ValueError(f"Output already exists; overwrite is disabled: {target}")
        target.mkdir(parents=True)
        arcpy.conversion.KMLToLayer(dataset, str(target), args["output_name"])
        found = [path for path in target.rglob("*.gdb") if arcpy.Exists(str(path))]
        if len(found) != 1:
            raise RuntimeError(f"KML conversion did not produce exactly one geodatabase under {target}")
        gdb = found[0]
        members = [str(Path(directory) / name)
                   for directory, _, names in arcpy.da.Walk(str(gdb), datatype=["FeatureClass", "Table"])
                   for name in names]
        return {"output_workspace": str(gdb), "members": members, "member_count": len(members),
                "note": "Use copy_features with a member path to bring chosen members into results.gdb",
                "source": dataset, "source_modified": False, "messages": arcpy.GetMessages()[-4000:]}
    output = _output(arcpy, folder, args["output_name"])
    _copy(arcpy, dataset, output)
    return {"output": output, "count": int(arcpy.management.GetCount(output)[0]), "source": [dataset],
            "source_modified": False, "messages": arcpy.GetMessages()[-4000:]}


def _new_field(arcpy, dataset, name):
    if arcpy.ValidateFieldName(name) != name or name.lower() in {f.name.lower() for f in arcpy.ListFields(dataset)}:
        raise ValueError("The new field name is invalid or already exists")


def _json_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or type(value) in (bool, int, float):
        return value
    return (value.isoformat() if hasattr(value, "isoformat") else str(value))[:1024]


def export_selected_ids(arcpy, snapshot, output_name, folder):
    """Export a frozen nonempty OID set, never an ambient Pro selection."""
    dataset, ids = snapshot["dataset"], snapshot["object_ids"]
    desc = check_dataset(arcpy, dataset)
    if not getattr(desc, "hasOID", False) or not getattr(desc, "shapeType", None):
        raise ValueError("Selection export requires a feature class with object IDs")
    oid_field = arcpy.AddFieldDelimiters(dataset, desc.OIDFieldName)
    # Bound each IN group for providers with SQL expression size limits.
    clauses = [oid_field + " IN (" + ",".join(str(oid) for oid in ids[start:start + 900]) + ")"
               for start in range(0, len(ids), 900)]
    where = " OR ".join("(" + clause + ")" for clause in clauses)
    temporary = "agent_selection_" + uuid.uuid4().hex
    layer = None
    with arcpy.EnvManager(overwriteOutput=False, addOutputsToMap=False, extent=None, outputCoordinateSystem=None):
        try:
            layer = arcpy.management.MakeFeatureLayer(dataset, temporary, where)[0]
            with arcpy.da.SearchCursor(layer, ["OID@"]) as cursor:
                actual = {int(row[0]) for row in cursor}
            if actual != set(ids):
                raise ValueError("所选要素已删除、未保存或源数据发生变化；请保存需要的数据并重新获取选择集。未改为整层导出。")
            output = _output(arcpy, folder, output_name)
            arcpy.management.CopyFeatures(layer, output)
            count = int(arcpy.management.GetCount(output)[0])
            if count != len(ids):
                raise RuntimeError("导出记录数与授权快照不一致，任务失败；保留部分成果供检查。")
            return {"output": output, "source": dataset, "count": count, "selected_count": len(ids),
                    "selection_token": snapshot["selection_token"], "source_modified": False,
                    "selection_modified": False, "reads_saved_data": True,
                    "messages": arcpy.GetMessages()[-4000:]}
        finally:
            if layer is not None:
                arcpy.management.Delete(layer)


def execute_extended(name, args, folder: Path, arcpy):
    # Deliberately do not inherit Pro selections, extents, snap settings, cell size, mask or output SR.
    with arcpy.EnvManager(overwriteOutput=False, addOutputsToMap=False, extent=None, outputCoordinateSystem=None,
                          cellSize=None, mask=None, snapRaster=None):
        if name == "list_datasets":
            check_dataset(arcpy, args["workspace"])
            limit = args.get("limit", 100)
            rows = []
            for directory, _, files in arcpy.da.Walk(args["workspace"], datatype=["FeatureClass", "Table", "RasterDataset"]):
                for filename in files:
                    if len(rows) >= limit:
                        return {"datasets": rows, "truncated": True}
                    path = str(Path(directory) / filename)
                    desc = arcpy.Describe(path)
                    rows.append({"name": filename, "path": path, "data_type": desc.dataType})
            return {"datasets": rows, "truncated": False}
        from .analysis_tools import ANALYSIS_TOOLS, execute_analysis
        if name in ANALYSIS_TOOLS:
            return execute_analysis(name, args, folder, arcpy)
        from .cartography import CARTOGRAPHY_TOOLS, execute_cartography
        if name in CARTOGRAPHY_TOOLS:
            return execute_cartography(name, args, folder, arcpy)
        sources = args.get("datasets", [args.get("dataset")])
        for source in sources:
            check_dataset(arcpy, source)
        dataset = sources[0]
        desc = arcpy.Describe(dataset)
        for key in ("clip_dataset", "join_dataset", "select_dataset", "near_dataset"):
            if key in args:
                check_dataset(arcpy, args[key])
        if name == "import_table_points":
            return _import_table_points(arcpy, args, folder, desc, dataset)
        if name == "convert_dataset":
            return _convert_dataset(arcpy, args, folder, dataset)
        if name == "count":
            return {"count": int(arcpy.management.GetCount(dataset)[0]), "dataset": dataset}
        if name == "read_rows":
            names = args.get("fields") or [f.name for f in arcpy.ListFields(dataset) if f.type not in ("Geometry", "Blob", "Raster")][:12]
            if not names:
                raise ValueError("No supported fields found")
            _fields(arcpy, dataset, names)
            where = where_clause(arcpy, dataset, args.get("filter"))
            limit = args.get("limit", 100)
            with arcpy.da.SearchCursor(dataset, names, where_clause=where) as cursor:
                records, size, truncated = [], 0, False
                for row in itertools.islice(cursor, limit + 1):
                    record = [_json_value(value) for value in row]
                    size += len(json.dumps(record, ensure_ascii=False))
                    if len(records) >= limit or size > 60000:
                        truncated = True
                        break
                    records.append(record)
            return {"fields": names, "rows": records, "returned": len(records), "truncated": truncated,
                    "text_cell_limit": 1024, "text_may_be_truncated": True}
        if name == "verify_dataset":
            return _verify_dataset(arcpy, args, dataset, desc)
        if name == "export_table_csv":
            names = args.get("fields") or [f.name for f in arcpy.ListFields(dataset)
                                           if f.type not in ("Geometry", "Blob", "Raster")][:32]
            if not names:
                raise ValueError("No supported fields found")
            _fields(arcpy, dataset, names)
            import csv
            output = _file_output(folder, args["output_name"], ".csv")
            written = 0
            with open(output, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(names)
                with arcpy.da.SearchCursor(dataset, names, where_clause=where_clause(arcpy, dataset, args.get("filter"))) as cursor:
                    for row in cursor:
                        writer.writerow(["" if value is None else
                                         (value.isoformat() if hasattr(value, "isoformat") else value)
                                         for value in row])
                        written += 1
            return {"output": output, "count": written, "fields": names, "source": [dataset],
                    "source_modified": False, "messages": arcpy.GetMessages()[-4000:]}
        if name == "export_features":
            if not getattr(desc, "shapeType", None):
                raise ValueError(f"export_features 需要要素数据，{dataset} 是 {desc.dataType}")
            if args["format"] == "GEOJSON":
                output = _file_output(folder, args["output_name"], ".geojson")
                arcpy.conversion.FeaturesToJSON(dataset, output, geoJSON="GEOJSON")
            else:
                output = _file_output(folder, args["output_name"], ".shp")
                arcpy.management.CopyFeatures(dataset, output)
            return {"output": output, "format": args["format"],
                    "count": int(arcpy.management.GetCount(dataset)[0]), "source": [dataset],
                    "source_modified": False, "messages": arcpy.GetMessages()[-4000:]}

        # Check editable fields and conditions before creating any output.
        where = where_clause(arcpy, dataset, args.get("filter"))
        if name in ("dissolve", "delete_fields") and args.get("fields"):
            fields = _fields(arcpy, dataset, args["fields"])
            if name == "delete_fields" and any(fields[f].required or fields[f].type == "OID" for f in args["fields"]):
                raise ValueError("Required/OID fields cannot be deleted")
        if name in ("add_field", "calculate_geometry"):
            _new_field(arcpy, dataset, args["field_name"])
        if name == "set_field_value":
            field = _fields(arcpy, dataset, [args["field"]])[args["field"]]
            if field.required or not field.editable or field.type == "OID":
                raise ValueError("System or non-editable fields cannot be changed")
            _field_value(field, args["value"])
        if name == "calculate_field":
            target = _fields(arcpy, dataset, [args["field"]])[args["field"]]
            if target.required or not target.editable or target.type == "OID" or target.type not in NUMERIC_FIELD_TYPES:
                raise ValueError("calculate_field 的目标字段必须是可编辑的数值字段")
            for ref in _referenced_fields(args["expression"]):
                if _fields(arcpy, dataset, [ref])[ref].type not in NUMERIC_FIELD_TYPES:
                    raise ValueError(f"表达式引用的字段「{ref}」不是数值字段")
        if name == "select_by_location":
            if not getattr(desc, "shapeType", None) or not getattr(arcpy.Describe(args["select_dataset"]), "shapeType", None):
                raise ValueError("select_by_location 的输入与选择数据都必须是要素数据")
        if name == "create_thiessen_polygons" and desc.shapeType not in ("Point", "Multipoint"):
            raise ValueError(f"泰森多边形需要点要素，{dataset} 是 {desc.shapeType or desc.dataType}")
        target = None
        if name == "project":
            target = arcpy.SpatialReference(args["target_wkid"])
            source_sr = desc.spatialReference
            if target.name == "Unknown" or source_sr.name == "Unknown":
                raise ValueError("Source and target coordinate systems must be known")
            transformations = arcpy.ListTransformations(source_sr, target, desc.extent)
            if args.get("transformation") and args["transformation"] not in transformations:
                raise ValueError("Transformation unavailable; choices: " + ", ".join(transformations[:8]))
            if source_sr.GCS.name != target.GCS.name and not args.get("transformation"):
                raise ValueError("A geographic transformation must be chosen explicitly; choices: " + ", ".join(transformations[:8]))
        output = _output(arcpy, folder, args["output_name"], raster=name == "clip_raster")
        extra = {}
        if name == "copy_features":
            arcpy.management.CopyFeatures(dataset, output)
        elif name == "clip":
            arcpy.analysis.Clip(dataset, args["clip_dataset"], output)
        elif name == "intersect":
            arcpy.analysis.Intersect(sources, output)
        elif name == "merge":
            arcpy.management.Merge(sources, output)
        elif name == "dissolve":
            arcpy.management.Dissolve(dataset, output, args.get("fields"))
        elif name == "project":
            arcpy.management.Project(dataset, output, target, args.get("transformation", ""))
        elif name == "spatial_join":
            arcpy.analysis.SpatialJoin(dataset, args["join_dataset"], output, "JOIN_ONE_TO_ONE", "KEEP_ALL", match_option=args.get("match_option", "INTERSECT"))
        elif name == "select_features":
            arcpy.analysis.Select(dataset, output, where)
        elif name == "select_by_location":
            temporary = "agent_sbl_" + uuid.uuid4().hex
            layer = None
            try:
                layer = arcpy.management.MakeFeatureLayer(dataset, temporary)[0]
                options = {"overlap_type": args["overlap_type"], "select_features": args["select_dataset"]}
                if "search_distance" in args:
                    options["search_distance"] = args["search_distance"]
                arcpy.management.SelectLayerByLocation(layer, **options)
                arcpy.management.CopyFeatures(layer, output)
            finally:
                if layer is not None:
                    arcpy.management.Delete(layer)
        elif name == "create_thiessen_polygons":
            arcpy.analysis.CreateThiessenPolygons(dataset, output, args.get("fields_to_copy", "ONLY_FID"))
        elif name in ("add_field", "set_field_value", "delete_fields", "calculate_geometry", "repair_geometry",
                      "calculate_field", "define_projection", "near_analysis"):
            _copy(arcpy, dataset, output)
            if name == "add_field":
                arcpy.management.AddField(output, args["field_name"], args["field_type"], field_length=args.get("length", 255))
            elif name == "set_field_value":
                count = 0
                with arcpy.da.UpdateCursor(output, [args["field"]], where_clause=where_clause(arcpy, output, args.get("filter")), explicit=True) as cursor:
                    for row in cursor:
                        row[0] = args["value"]
                        cursor.updateRow(row)
                        count += 1
                extra["updated_count"] = count
            elif name == "delete_fields":
                arcpy.management.DeleteField(output, args["fields"])
            elif name == "calculate_geometry":
                arcpy.management.AddField(output, args["field_name"], "DOUBLE")
                option = "area_unit" if args["measurement"] == "AREA_GEODESIC" else "length_unit"
                arcpy.management.CalculateGeometryAttributes(output, [[args["field_name"], args["measurement"]]], **{option: args["unit"]})
            elif name == "calculate_field":
                tree = _expression_tree(args["expression"])
                refs = _referenced_fields(args["expression"])
                updated = 0
                with arcpy.da.UpdateCursor(output, [args["field"], *refs],
                                            where_clause=where_clause(arcpy, output, args.get("filter"))) as cursor:
                    for row in cursor:
                        row[0] = _eval_expression(tree, {name: row[index + 1] for index, name in enumerate(refs)})
                        cursor.updateRow(row)
                        updated += 1
                extra["updated_count"] = updated
            elif name == "define_projection":
                arcpy.management.DefineProjection(output, arcpy.SpatialReference(args["wkid"]))
            elif name == "near_analysis":
                options = {}
                if "search_radius" in args:
                    options["search_radius"] = args["search_radius"]
                if "method" in args:
                    options["method"] = args["method"]
                arcpy.analysis.Near(output, args["near_dataset"], **options)
                with arcpy.da.SearchCursor(output, ["NEAR_FID"]) as cursor:
                    extra["matched_count"] = sum(1 for row in cursor if row[0] != -1)
            else:
                arcpy.management.RepairGeometry(output, "KEEP_NULL")
        elif name == "clip_raster":
            arcpy.management.Clip(dataset, "#", output, args["clip_dataset"], "#", "ClippingGeometry", "NO_MAINTAIN_EXTENT")
        else:
            raise ValueError("Unimplemented GIS tool")
        if not arcpy.Exists(output):
            raise RuntimeError("ArcGIS did not create the expected output")
        result = {"output": output, "source": sources, "source_modified": False, **extra}
        if name == "clip_raster":
            info = arcpy.Describe(output)
            result.update(width=info.width, height=info.height, band_count=info.bandCount)
        else:
            result["count"] = int(arcpy.management.GetCount(output)[0])
            sr = getattr(arcpy.Describe(output), "spatialReference", None)
            if sr:
                result["spatial_reference"] = {"name": sr.name, "wkid": sr.factoryCode}
        result["messages"] = arcpy.GetMessages()[-4000:]
        return result
