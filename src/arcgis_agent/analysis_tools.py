"""Batch-2 analysis tools: Spatial Analyst, overlay, reshape and table joins.

Every ArcPy call shape here was verified against the local ArcGIS Pro 3.5.2 runtime;
several documented forms (arcpy.sa.IDW, percent tolerances, JoinField join types) do not exist there.
"""
from __future__ import annotations

from .gis_tools import _copy, _fields, _output, check_dataset, require_extension

ANALYSIS_TOOLS = frozenset({"kernel_density", "interpolate_surface", "zonal_statistics", "extract_by_mask",
                            "sample_raster_at_points", "polygon_to_raster", "terrain_derivatives", "contour_lines",
                            "summarize_statistics", "join_table", "union", "erase", "reshape_geometry",
                            "reclassify", "hillshade", "resample", "project_raster",
                            "fill", "flow_direction", "flow_accumulation"})
SPATIAL_ANALYST_TOOLS = {"kernel_density", "interpolate_surface", "zonal_statistics", "extract_by_mask",
                         "sample_raster_at_points", "terrain_derivatives", "contour_lines",
                         "reclassify", "hillshade", "fill", "flow_direction", "flow_accumulation"}
NUMERIC_TYPES = ("SmallInteger", "Integer", "BigInteger", "Single", "Double", "OID")
INTEGER_TYPES = ("SmallInteger", "Integer", "BigInteger", "OID")
RASTER_TYPES = ("RasterDataset", "RasterBand", "MosaicDataset", "MosaicLayer", "ImageServerLayerDataset", "RasterLayer")


def _num(value):
    """Plain decimal text: ArcPy refuses exponent notation and unit suffixes for numeric analysis parameters."""
    text = format(float(value), ".10f").rstrip("0").rstrip(".")
    if text in ("", "-0"):
        raise ValueError(f"Value is too small to use as an analysis parameter: {value}")
    return text


def _require_shape(desc, allowed, label):
    if getattr(desc, "shapeType", None) not in allowed:
        raise ValueError(f"{label}需要 {'/'.join(allowed)} 要素，实际为 {getattr(desc, 'shapeType', None) or desc.dataType}。")
    return desc


def _require_raster(arcpy, dataset, label):
    desc = check_dataset(arcpy, dataset)
    if desc.dataType not in RASTER_TYPES:
        raise ValueError(f"{label}需要栅格数据，实际为 {desc.dataType}：{dataset}")
    return desc


def _numeric_field(arcpy, dataset, name, label, *, integer=False):
    field = _fields(arcpy, dataset, [name])[name]
    types = INTEGER_TYPES if integer else NUMERIC_TYPES
    if field.type not in types:
        raise ValueError(f"{label}「{name}」的类型是 {field.type}，需要 {'整数' if integer else '数值'} 字段。")
    return field


def _sr_key(desc):
    sr = desc.spatialReference
    return sr.factoryCode or sr.name


def _finish(arcpy, output, sources, raster=False, **extra):
    result = {"output": output, "source": sources, "source_modified": False, **extra}
    if raster:
        info = arcpy.Describe(output)
        sr = getattr(info, "spatialReference", None)
        extent = getattr(info, "extent", None)
        result.update(width=info.width, height=info.height, band_count=info.bandCount, pixel_type=info.pixelType,
                      cell_size=info.meanCellWidth,
                      spatial_reference={"name": sr.name, "wkid": sr.factoryCode} if sr else None,
                      extent={"xmin": extent.XMin, "ymin": extent.YMin, "xmax": extent.XMax, "ymax": extent.YMax} if extent else None)
    else:
        result["count"] = int(arcpy.management.GetCount(output)[0])
        result["output_fields"] = [f.name for f in arcpy.ListFields(output)][:40]
        if getattr(arcpy.Describe(output), "shapeType", None):
            result["shape_type"] = arcpy.Describe(output).shapeType
    result["messages"] = arcpy.GetMessages()[-4000:]
    return result


def execute_analysis(name, args, folder, arcpy):
    """Run one allowlisted analysis tool. Returns the observation payload for the model."""
    if name in SPATIAL_ANALYST_TOOLS:
        require_extension(arcpy, "Spatial")
    dataset = args.get("dataset") or args.get("zone_dataset")
    sources = list(args.get("datasets") or args.get("rasters") or ([dataset] if dataset else []))
    for key in ("mask_dataset", "join_dataset", "erase_dataset", "value_raster", "zone_dataset"):
        if args.get(key) and args[key] not in sources:
            sources.append(args[key])
    for source in sources:
        check_dataset(arcpy, source)

    if name == "kernel_density":
        _require_shape(check_dataset(arcpy, dataset), ("Point", "Multipoint", "Polyline"), "核密度")
        if "value_field" in args:
            _numeric_field(arcpy, dataset, args["value_field"], "密度权重字段")
        options = {"cell_size": _num(args["cell_size"])}
        for key, param in (("search_radius", "search_radius"), ("area_unit", "area_unit_scale_factor"),
                           ("output_values", "out_cell_values"), ("method", "method")):
            if key in args:
                options[param] = _num(args[key]) if key == "search_radius" else args[key]
        output = _output(arcpy, folder, args["output_name"], raster=True)
        arcpy.sa.KernelDensity(dataset, args.get("value_field", "NONE"), **options).save(output)
        return _finish(arcpy, output, sources, raster=True, density_field=args.get("value_field", "NONE"))

    if name == "interpolate_surface":
        _require_shape(check_dataset(arcpy, dataset), ("Point", "Multipoint"), "插值")
        _numeric_field(arcpy, dataset, args["value_field"], "插值字段")
        options = {"cell_size": _num(args["cell_size"])}
        if "search_radius" in args:
            options["search_radius"] = _num(args["search_radius"])
        if args["method"] == "IDW":
            if "power" in args:
                options["power"] = float(args["power"])
            surface = arcpy.sa.Idw(dataset, args["value_field"], **options)
        else:
            model = arcpy.sa.KrigingModelOrdinary(args["variogram"]) if "variogram" in args else arcpy.sa.KrigingModelOrdinary()
            surface = arcpy.sa.Kriging(dataset, args["value_field"], model, **options)
        output = _output(arcpy, folder, args["output_name"], raster=True)
        surface.save(output)
        return _finish(arcpy, output, sources, raster=True, method=args["method"], value_field=args["value_field"])

    if name == "zonal_statistics":
        zone, zone_field = args["zone_dataset"], args["zone_field"]
        _require_raster(arcpy, args["value_raster"], "分区统计的值栅格")
        if getattr(check_dataset(arcpy, zone), "shapeType", None):
            _numeric_field(arcpy, zone, zone_field, "分区字段", integer=True)
        output = _output(arcpy, folder, args["output_name"])
        arcpy.sa.ZonalStatisticsAsTable(zone, zone_field, args["value_raster"], output, "DATA", args["statistic"])
        return _finish(arcpy, output, sources, statistic=args["statistic"], zone_field=zone_field)

    if name == "extract_by_mask":
        _require_raster(arcpy, dataset, "按掩膜提取")
        output = _output(arcpy, folder, args["output_name"], raster=True)
        arcpy.sa.ExtractByMask(dataset, args["mask_dataset"], args.get("extraction_area", "INSIDE")).save(output)
        return _finish(arcpy, output, sources, raster=True, extraction_area=args.get("extraction_area", "INSIDE"))

    if name == "sample_raster_at_points":
        for raster in args["rasters"]:
            _require_raster(arcpy, raster, "采样栅格")
        _require_shape(check_dataset(arcpy, dataset), ("Point", "Multipoint", "Polyline", "Polygon"), "栅格取值")
        output = _output(arcpy, folder, args["output_name"])
        if "resampling" in args:
            arcpy.sa.Sample(args["rasters"], dataset, output, args["resampling"])
        else:
            arcpy.sa.Sample(args["rasters"], dataset, output)
        return _finish(arcpy, output, sources, location_dataset=dataset, raster_count=len(args["rasters"]))

    if name == "polygon_to_raster":
        _require_shape(check_dataset(arcpy, dataset), ("Polygon",), "面转栅格")
        _fields(arcpy, dataset, [args["value_field"]])
        output = _output(arcpy, folder, args["output_name"], raster=True)
        arcpy.conversion.FeatureToRaster(dataset, args["value_field"], output, _num(args["cell_size"]))
        return _finish(arcpy, output, sources, raster=True, value_field=args["value_field"])

    if name == "terrain_derivatives":
        _require_raster(arcpy, dataset, "地形计算")
        method = args.get("method", "PLANAR")
        if args["derivative"] == "SLOPE":
            options = {"output_measurement": args.get("slope_measurement", "DEGREE"), "method": method}
            if "z_factor" in args:
                options["z_factor"] = float(args["z_factor"])
            if "z_unit" in args:
                options["z_unit"] = args["z_unit"]
            surface = arcpy.sa.Slope(dataset, **options)
        else:
            options = {"method": method}
            if "z_unit" in args:
                options["z_unit"] = args["z_unit"]
            surface = arcpy.sa.Aspect(dataset, **options)
        output = _output(arcpy, folder, args["output_name"], raster=True)
        surface.save(output)
        return _finish(arcpy, output, sources, raster=True, derivative=args["derivative"], method=method)

    if name == "contour_lines":
        _require_raster(arcpy, dataset, "等高线")
        output = _output(arcpy, folder, args["output_name"])
        if "base_value" in args:
            arcpy.sa.Contour(dataset, output, _num(args["interval"]), _num(args["base_value"]))
        else:
            arcpy.sa.Contour(dataset, output, _num(args["interval"]))
        return _finish(arcpy, output, sources, interval=float(args["interval"]), base_value=args.get("base_value"))

    if name == "reclassify":
        desc = _require_raster(arcpy, dataset, "重分类")
        entries = [[item["start"], item["end"], item["new_value"]] for item in args["remap"]]
        if "map_nodata_to" in args:
            entries = [["NODATA", args["map_nodata_to"]]] + entries
        remap = arcpy.sa.RemapRange(entries)
        output = _output(arcpy, folder, args["output_name"], raster=True)
        arcpy.sa.Reclassify(dataset, "Value", remap, "NODATA").save(output)
        return _finish(arcpy, output, sources, raster=True, remap_count=len(args["remap"]),
                       source_pixel_type=desc.pixelType)

    if name == "hillshade":
        _require_raster(arcpy, dataset, "山体阴影")
        options = {}
        for key in ("azimuth", "altitude", "z_factor"):
            if key in args:
                options[key] = float(args[key])
        options["model_shadows"] = args.get("shadows", "NO_SHADOWS")
        output = _output(arcpy, folder, args["output_name"], raster=True)
        arcpy.sa.Hillshade(dataset, **options).save(output)
        return _finish(arcpy, output, sources, raster=True, shadows=args.get("shadows", "NO_SHADOWS"))

    if name == "resample":
        _require_raster(arcpy, dataset, "重采样")
        output = _output(arcpy, folder, args["output_name"], raster=True)
        if "resampling" in args:
            arcpy.management.Resample(dataset, output, _num(args["cell_size"]), args["resampling"])
        else:
            arcpy.management.Resample(dataset, output, _num(args["cell_size"]))
        return _finish(arcpy, output, sources, raster=True, cell_size=float(args["cell_size"]),
                       resampling=args.get("resampling", "NEAREST"))

    if name == "project_raster":
        desc = _require_raster(arcpy, dataset, "栅格投影")
        target = arcpy.SpatialReference(args["target_wkid"])
        source_sr = desc.spatialReference
        if target.name == "Unknown" or source_sr.name == "Unknown":
            raise ValueError("Source and target coordinate systems must be known")
        transformations = arcpy.ListTransformations(source_sr, target, desc.extent)
        if args.get("transformation") and args["transformation"] not in transformations:
            raise ValueError("Transformation unavailable; choices: " + ", ".join(transformations[:8]))
        if source_sr.GCS.name != target.GCS.name and not args.get("transformation"):
            raise ValueError("A geographic transformation must be chosen explicitly; choices: " + ", ".join(transformations[:8]))
        output = _output(arcpy, folder, args["output_name"], raster=True)
        options = {"resampling_type": args.get("resampling", "NEAREST")}
        if "cell_size" in args:
            options["cell_size"] = _num(args["cell_size"])
        if args.get("transformation"):
            options["geographic_transform"] = args["transformation"]
        arcpy.management.ProjectRaster(dataset, output, target, **options)
        return _finish(arcpy, output, sources, raster=True, target_wkid=args["target_wkid"])

    if name == "fill":
        _require_raster(arcpy, dataset, "填洼")
        output = _output(arcpy, folder, args["output_name"], raster=True)
        if "z_limit" in args:
            arcpy.sa.Fill(dataset, float(args["z_limit"])).save(output)
        else:
            arcpy.sa.Fill(dataset).save(output)
        return _finish(arcpy, output, sources, raster=True, z_limit=args.get("z_limit"))

    if name == "flow_direction":
        _require_raster(arcpy, dataset, "流向计算")
        output = _output(arcpy, folder, args["output_name"], raster=True)
        if "flow_direction_type" in args:
            arcpy.sa.FlowDirection(dataset, flow_direction_type=args["flow_direction_type"]).save(output)
        else:
            arcpy.sa.FlowDirection(dataset).save(output)
        return _finish(arcpy, output, sources, raster=True, flow_direction_type=args.get("flow_direction_type", "D8"))

    if name == "flow_accumulation":
        _require_raster(arcpy, dataset, "汇流累积")
        output = _output(arcpy, folder, args["output_name"], raster=True)
        options = {}
        if "data_type" in args:
            options["data_type"] = args["data_type"]
        if "flow_direction_type" in args:
            options["flow_direction_type"] = args["flow_direction_type"]
        arcpy.sa.FlowAccumulation(dataset, **options).save(output)
        return _finish(arcpy, output, sources, raster=True,
                       data_type=args.get("data_type", "FLOAT"), flow_direction_type=args.get("flow_direction_type", "D8"))

    if name == "summarize_statistics":
        available = _fields(arcpy, dataset, [s["field"] for s in args["stats"]] + ([args["case_field"]] if "case_field" in args else []))
        for item in args["stats"]:
            field, statistic = available[item["field"]], item["statistic"]
            if statistic == "CONCATENATE" and field.type != "String":
                raise ValueError(f"CONCATENATE 只适用于文本字段，「{item['field']}」是 {field.type}。")
            if statistic not in ("CONCATENATE", "COUNT", "UNIQUE", "FIRST", "LAST") and field.type not in NUMERIC_TYPES:
                raise ValueError(f"{statistic} 需要数值字段，「{item['field']}」是 {field.type}。")
        pairs = [[item["field"], item["statistic"]] for item in args["stats"]]
        output = _output(arcpy, folder, args["output_name"])
        if "case_field" in args:
            arcpy.analysis.Statistics(dataset, output, pairs, [args["case_field"]])
        else:
            arcpy.analysis.Statistics(dataset, output, pairs)
        return _finish(arcpy, output, sources, statistics=pairs, case_field=args.get("case_field"))

    if name == "join_table":
        target = _fields(arcpy, dataset, [args["target_field"]])[args["target_field"]]
        remote = _fields(arcpy, args["join_dataset"], [args["join_field"]])[args["join_field"]]
        if (target.type == "String") != (remote.type == "String"):
            raise ValueError(f"连接字段类型不一致：{args['target_field']} 为 {target.type}，{args['join_field']} 为 {remote.type}。")
        # JoinField silently skips unknown columns, so the requested names are verified against the join table here.
        _fields(arcpy, args["join_dataset"], args["fields"])
        output = _output(arcpy, folder, args["output_name"])
        _copy(arcpy, dataset, output)
        arcpy.management.JoinField(output, target.name, args["join_dataset"], remote.name, args["fields"])
        present = {f.name for f in arcpy.ListFields(output)}
        joined = [name for name in args["fields"] if any(p == name or p.endswith("_" + name) for p in present)]
        if len(joined) != len(args["fields"]):
            missing = [n for n in args["fields"] if n not in joined]
            raise ValueError(f"连接后副本缺少列 {missing}；任务失败，副本已保留供检查。")
        return _finish(arcpy, output, sources, joined_fields=joined, target_field=target.name, join_field=remote.name)

    if name == "union":
        keys = {}
        for source in args["datasets"]:
            desc = check_dataset(arcpy, source)
            _require_shape(desc, ("Polygon",), "叠合分析")
            keys[_sr_key(desc)] = source
        if len(keys) > 1:
            raise ValueError("叠合分析的输入必须使用同一坐标系，请先用 project 工具统一：" + ", ".join(str(k) for k in keys))
        output = _output(arcpy, folder, args["output_name"])
        arcpy.analysis.Union(args["datasets"], output, args.get("keep_attributes", "ALL"))
        return _finish(arcpy, output, sources, keep_attributes=args.get("keep_attributes", "ALL"))

    if name == "erase":
        _require_shape(check_dataset(arcpy, dataset), ("Polygon", "Polyline", "Point", "Multipoint"), "擦除分析")
        _require_shape(check_dataset(arcpy, args["erase_dataset"]), ("Polygon", "Polyline"), "擦除要素")
        output = _output(arcpy, folder, args["output_name"])
        arcpy.analysis.Erase(dataset, args["erase_dataset"], output)
        return _finish(arcpy, output, sources)

    if name == "reshape_geometry":
        desc = check_dataset(arcpy, dataset)
        shape, operation = desc.shapeType, args["operation"]
        output = _output(arcpy, folder, args["output_name"])
        if operation == "CENTROID":
            _require_shape(desc, ("Polygon", "Polyline", "Multipoint"), "生成中心点")
            arcpy.management.FeatureToPoint(dataset, output, "CENTROID")
        elif operation == "SIMPLIFY":
            if shape == "Polygon":
                arcpy.cartography.SimplifyPolygon(dataset, output, "POINT_REMOVE", args["tolerance"])
            elif shape == "Polyline":
                arcpy.cartography.SimplifyLine(dataset, output, "POINT_REMOVE", args["tolerance"])
            else:
                raise ValueError(f"简化只支持面或线要素，{dataset} 是 {shape}。")
        else:
            _require_shape(desc, ("Point", "Multipoint", "Polyline", "Polygon"), "最小外包几何")
            arcpy.management.MinimumBoundingGeometry(dataset, output, args["geometry_type"])
        return _finish(arcpy, output, sources, operation=operation, tolerance=args.get("tolerance"),
                       geometry_type=args.get("geometry_type"))

    raise ValueError(f"Unimplemented analysis tool: {name}")
