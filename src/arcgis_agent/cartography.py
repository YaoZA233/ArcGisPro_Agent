"""Batch-3 cartography: build a layout project and export it through allowlisted arcpy.mp calls.

Every call shape here was verified against the local ArcGIS Pro 3.5.2 runtime (docs/validation.md):
the Blank.aprx shipped inside the install, createLayout/createMapFrame/createMapSurroundElement,
the CIM title element and Layout.export all work without any Pro UI, while georeferenceMapFrame,
worldFile and per-format colour modes were refused by the runtime and are therefore not offered.
The user's own project is only ever opened read-only: this module saves nothing back to it.
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from .analysis_tools import RASTER_TYPES
from .catalog import EXPORT_EXTENSIONS, PAGE_SIZES
from .gis_tools import check_dataset
from .symbology import apply_label, apply_renderer, plan_style, validate_label_spec

CARTOGRAPHY_TOOLS = frozenset({"build_layout", "export_layout"})
BLANK_TEMPLATE = Path("Resources") / "ArcToolBox" / "Services" / "routingservices" / "data" / "Blank.aprx"
SURROUND_TYPES = {"LEGEND": "LEGEND", "NORTH_ARROW": "NORTH_ARROW", "SCALE_BAR": "SCALE_BAR",
                  "DUAL_SCALE_BAR": "DUAL_SCALE_BAR"}
# Element names are what the user sees in Pro and in export_layout's element list, so they are kept Chinese.
ELEMENT_LABELS = {"LEGEND": "图例", "NORTH_ARROW": "指北针", "SCALE_BAR": "比例尺", "DUAL_SCALE_BAR": "双比例尺"}
DEFAULT_ELEMENTS = ["LEGEND", "NORTH_ARROW", "SCALE_BAR"]
MARGIN, TITLE_BAND, SIDE_BAND = 12.0, 16.0, 62.0


def _page(arguments):
    short, long_side = PAGE_SIZES[arguments.get("page_size", "A4")]
    return (long_side, short) if arguments.get("orientation", "LANDSCAPE") == "LANDSCAPE" else (short, long_side)


def _target(folder, name, suffix):
    path = folder.resolve() / "cartography" / f"{name}.{suffix}"
    if path.exists():
        raise ValueError(f"输出已存在，禁止覆盖：{path}")
    return path


def _reserve(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _staged(path, token):
    """Writes land on a throwaway sibling and are published with one atomic rename.

    A worker killed mid-call then leaves no file at the declared path, so the same output_name can
    be retried instead of being blocked forever by the "never overwrite" rule.
    """
    return path.with_name(f"{path.stem}.{token}{path.suffix}")


def _blank_template(arcpy):
    template = Path(arcpy.GetInstallInfo()["InstallDir"]) / BLANK_TEMPLATE
    if not template.is_file():
        raise ValueError(f"本机 ArcGIS Pro 安装中找不到空白工程模板，出图工具未执行：{template}")
    return template


def _rect(arcpy, left, bottom, width, height):
    return arcpy.Polygon(arcpy.Array([arcpy.Point(left, bottom), arcpy.Point(left + width, bottom),
                                      arcpy.Point(left + width, bottom + height), arcpy.Point(left, bottom + height),
                                      arcpy.Point(left, bottom)]))


def _map_layer(arcpy, dataset):
    """Reject anything without geometry: a table would render an empty map frame."""
    desc = check_dataset(arcpy, dataset)
    if getattr(desc, "shapeType", None):
        return desc
    if desc.dataType in RASTER_TYPES:
        return desc
    raise ValueError(f"{dataset} 是 {desc.dataType}，没有几何，无法作为出图图层；请先转成要素类或栅格。")


def _require_degree_box(reference, box):
    """Refuse metre numbers on a geographic layer before the camera is set.

    A geographic map frame takes its extent in degrees, so a box of projected coordinates would land
    outside the world and export an empty sheet with no error at all.
    """
    if reference.type != "Geographic":
        return
    bounds = {"x": (-180.0, 180.0), "y": (-90.0, 90.0)}
    for axis, (low, high) in bounds.items():
        for key in (f"{axis}min", f"{axis}max"):
            if not low <= box[key] <= high:
                raise ValueError(f"图层坐标系「{reference.name}」是地理坐标系，extent 要用经纬度给出（经度 ±180、"
                                 f"纬度 ±90），而 {key}={box[key]:g} 超出了该范围，疑似投影坐标（米）。"
                                 f"请改给经纬度，或先用 project 工具把数据转成投影坐标系再出图。")


def _sr_advice(entries, verb):
    """project wraps management.Project, which accepts feature classes only, so a raster layer needs a
    different pointer than a vector one."""
    if any(desc.dataType in RASTER_TYPES for _, desc in entries):
        return f"栅格请先在 ArcGIS Pro 中用 Project Raster {verb}（本项目的 project 工具只支持要素类）"
    return f"请先用 project 工具{verb}"


def _camera_extent(arcpy, arguments, prepared):
    """Union the layer extents and return the spatial reference the camera must use.

    camera.setExtent() reads its numbers in the *map's* coordinate system, and Pro gives an empty map
    the coordinate system of the first layer added (verified on 3.5.2), so the extent and the data
    reference are handed to the caller together and re-checked against the map after the layers land.
    """
    codes, edges = {}, [None, None, None, None]
    for dataset, desc in prepared:
        reference = desc.spatialReference
        if (reference.name or "").lower() == "unknown" or (not reference.factoryCode and (reference.name or "") == ""):
            raise ValueError(f"{dataset} 的坐标系未知，无法确定出图范围单位；{_sr_advice([(dataset, desc)], '赋予合法坐标系')}。")
        codes[reference.factoryCode or reference.name] = (dataset, reference)
        box = desc.extent
        edges[0] = box.XMin if edges[0] is None else min(edges[0], box.XMin)
        edges[1] = box.YMin if edges[1] is None else min(edges[1], box.YMin)
        edges[2] = box.XMax if edges[2] is None else max(edges[2], box.XMax)
        edges[3] = box.YMax if edges[3] is None else max(edges[3], box.YMax)
    if len(codes) > 1:
        raise ValueError(f"各图层坐标系不一致，无法自动确定出图范围；{_sr_advice(prepared, '统一坐标系')}，或显式提供 extent。")
    reference = next(iter(code[1] for code in codes.values()))
    if "extent" in arguments:
        box = arguments["extent"]
        _require_degree_box(reference, box)
        return arcpy.Extent(box["xmin"], box["ymin"], box["xmax"], box["ymax"]), reference, "requested"
    return arcpy.Extent(*edges), reference, "data_extent"


def _add_title(arcpy, layout, text, width, height, band_width):
    """arcpy.mp has no createTextElement; the verified 3.5.2 path is the CIM definition."""
    definition = layout.getDefinition("V3")
    graphic = arcpy.cim.CIMParagraphTextGraphic()
    graphic.text = text
    symbol = arcpy.cim.CIMTextSymbol()
    symbol.fontFamilyName = "微软雅黑"
    symbol.fontSize = 20
    symbol.bold = True
    symbol.horizontalAlignment = "Center"
    # 颜色必须写进 values：red=/green=/blue= 关键字会被静默忽略，
    # 现在的黑色只是 values 未设置时的默认值碰巧撞对的。
    title_color = arcpy.cim.CIMRGBColor()
    title_color.values = [0, 0, 0, 100]
    symbol.color = title_color
    graphic.symbol = symbol
    graphic.shape = _rect(arcpy, MARGIN, height - MARGIN - TITLE_BAND, band_width, TITLE_BAND - 4.0)
    element = arcpy.cim.CIMGraphicElement()
    element.name = "图名"
    element.graphic = graphic
    definition.elements.append(element)
    layout.setDefinition(definition)


def _fill_layout(arcpy, project_path, arguments, prepared, extent, reference, width, height,
                 elements, plans, label_plans):
    project = arcpy.mp.ArcGISProject(str(project_path))
    map_object = layout = frame = camera = None
    try:
        map_object = project.listMaps()[0]
        layers = []
        by_dataset = {dataset: plan for dataset, plan in plans}
        labels_by_dataset = {dataset: label for dataset, label in label_plans}
        for dataset, _ in prepared:
            layer = map_object.addDataFromPath(dataset)
            if layer is None:
                raise ValueError(f"ArcGIS 无法把 {dataset} 加入地图，出图未执行；请用 describe 检查该数据。")
            # 只对有样式或有标注的图层做 CIM 往返；其余图层保持改动前的行为与开销。
            if dataset in by_dataset or dataset in labels_by_dataset:
                definition = layer.getDefinition("V3")
                if dataset in by_dataset:
                    apply_renderer(arcpy, definition, by_dataset[dataset])
                if dataset in labels_by_dataset:
                    apply_label(arcpy, definition, labels_by_dataset[dataset])
                layer.setDefinition(definition)
            layers.append(layer.name)
        map_code = map_object.spatialReference.factoryCode or map_object.spatialReference.name
        if map_code != (reference.factoryCode or reference.name):
            raise ValueError(f"地图坐标系（{map_object.spatialReference.name}）与图层坐标系"
                             f"（{reference.name}）不一致，出图范围无法定位；{_sr_advice(prepared, '统一坐标系')}。")
        layout = project.createLayout(width, height, "MILLIMETER", name=arguments["title"])
        side_band = SIDE_BAND if "LEGEND" in elements else 0.0
        frame = layout.createMapFrame(_rect(arcpy, MARGIN, MARGIN, width - 2 * MARGIN - side_band,
                                            height - 2 * MARGIN - TITLE_BAND), map_object, name="图幅")
        camera = frame.camera
        camera.setExtent(extent)
        frame.camera = camera
        camera = None
        placed = []
        for name in elements:
            if name == "LEGEND":
                geometry = _rect(arcpy, width - side_band + 8.0, MARGIN + 6.0, side_band - 16.0, 84.0)
            elif name == "NORTH_ARROW":
                geometry = arcpy.Point(width - MARGIN - 20.0, height - MARGIN - 25.0)
            else:
                geometry = arcpy.Point(width - MARGIN - 20.0, height - MARGIN - 70.0)
            placed.append(layout.createMapSurroundElement(geometry, SURROUND_TYPES[name], frame,
                                                          name=ELEMENT_LABELS[name]).name)
        _add_title(arcpy, layout, arguments["title"], width, height, width - 2 * MARGIN - side_band)
        project.save()
        return {"project": str(project_path), "layout": layout.name, "map": map_object.name, "layers": layers,
                "page_mm": [width, height], "page_size": arguments.get("page_size", "A4"),
                "orientation": arguments.get("orientation", "LANDSCAPE"), "elements": placed,
                "spatial_reference": {"name": reference.name, "wkid": reference.factoryCode},
                "extent": {"xmin": extent.XMin, "ymin": extent.YMin, "xmax": extent.XMax, "ymax": extent.YMax},
                "extent_source": "requested" if "extent" in arguments else "data_extent",
                "symbology": [plan for _, plan in plans],
                "labels": list(labels_by_dataset.values()),
                "project_modified": False, "next_step": "用 export_layout 传入本 project 路径与 layout 名称导出图片"}
    finally:
        del camera, frame, layout, map_object, project


def _build_layout(arcpy, arguments, folder):
    project_path = _target(folder, arguments["output_name"], "aprx")
    template = _blank_template(arcpy)
    prepared = [(dataset, _map_layer(arcpy, dataset)) for dataset in arguments["layers"]]
    extent, reference, _ = _camera_extent(arcpy, arguments, prepared)
    described = dict(prepared)

    def _resolve(entries, what):
        """把 styles/labels 里的 layer 翻成 (路径, 条目, desc)。

        未知图层必须抛 ValueError 而不是 KeyError：validate_call 已挡过一次，但
        _build_layout 也会被直接调用（离线测试与确定性计划执行器），而 KeyError 会被
        上层当成内部错误，用户就看不到「哪个图层写错了」这条可操作信息。
        """
        resolved = []
        for entry in entries:
            target = entry["layer"]
            if target not in described:
                raise ValueError(f"{what} 里的 layer「{target}」不在 layers 中；"
                                 f"本次 layers 是：{', '.join(described)}。")
            resolved.append((target, entry, described[target]))
        return resolved

    # 规划阶段会读源数据（只读），但必须全部排在复制模板之前：
    # 任何拒绝都不该留下半成品 .aprx，也不该创建 cartography 目录。
    plans = [(target, plan_style(arcpy, entry, target, desc))
             for target, entry, desc in _resolve(arguments.get("styles") or [], "styles")]
    label_plans = []
    for target, entry, desc in _resolve(arguments.get("labels") or [], "labels"):
        validate_label_spec(entry, desc)
        label_plans.append((target, dict(entry)))
    elements = arguments.get("elements")
    if elements is None:
        elements = list(DEFAULT_ELEMENTS)
    width, height = _page(arguments)
    # Everything that can be refused happens before the template is copied, so a rejected call leaves no stray project.
    staged = _staged(_reserve(project_path), uuid.uuid4().hex[:8])
    shutil.copy2(template, staged)
    result, published = None, False
    try:
        result = _fill_layout(arcpy, staged, arguments, prepared, extent, reference, width, height,
                              elements, plans, label_plans)
        os.replace(staged, project_path)
        published = True
        result["project"] = str(project_path)
    finally:
        if not published:
            staged.unlink(missing_ok=True)
            project_path.unlink(missing_ok=True)
    return result


def _export_layout(arcpy, arguments, folder):
    source = arguments["project"]
    if source.upper() != "CURRENT" and not Path(source).is_file():
        raise ValueError(f"工程文件不存在：{source}；请提供 .aprx 的绝对路径，或使用任务目录中 build_layout 的输出。")
    output = _target(folder, arguments["output_name"], EXPORT_EXTENSIONS[arguments["format"]])
    staged = _staged(output, uuid.uuid4().hex[:8])
    project = arcpy.mp.ArcGISProject(source)
    published = False
    try:
        layouts = project.listLayouts()
        names = ", ".join(item.name for item in layouts) or "（无布局，请先用 build_layout 创建）"
        wanted = arguments.get("layout")
        if wanted:
            layout = next((item for item in layouts if item.name == wanted), None)
            if layout is None:
                raise ValueError(f"工程中没有名为「{wanted}」的布局；现有布局：{names}。")
        elif len(layouts) == 1:
            layout = layouts[0]
        else:
            raise ValueError(f"工程里有 {len(layouts)} 个布局，必须指定 layout 名称：{names}。")
        export_format = arcpy.mp.CreateExportFormat(arguments["format"], str(_reserve(staged)))
        export_format.resolution = arguments.get("dpi", 300)
        produced = layout.export(export_format)
        path = Path(produced if isinstance(produced, str) and produced else staged)
        if path.resolve() != staged.resolve():
            raise RuntimeError(f"ArcGIS 未按声明路径生成导出文件（实际写到 {path}）；本次未交付。")
        if not staged.is_file() or staged.stat().st_size == 0:
            raise RuntimeError("ArcGIS 未生成导出文件；请检查布局是否包含图幅与可见图层。")
        os.replace(staged, output)
        published = True
        return {"output": str(output), "bytes": output.stat().st_size, "format": arguments["format"],
                "dpi": export_format.resolution, "layout": layout.name,
                "elements": [element.name for element in layout.listElements()],
                "project": source, "project_modified": False}
    finally:
        del project
        if not published:
            staged.unlink(missing_ok=True)
            output.unlink(missing_ok=True)


def execute_cartography(name, arguments, folder, arcpy):
    if name == "build_layout":
        return _build_layout(arcpy, arguments, folder)
    if name == "export_layout":
        return _export_layout(arcpy, arguments, folder)
    raise ValueError(f"Unimplemented cartography tool: {name}")
