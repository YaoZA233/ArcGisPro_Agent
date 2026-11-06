"""预设色板驱动的符号化与标注：把 CIM renderer 与 labelClass 挂到图层定义上。

所有 CIM 构造形态都在本机 ArcGIS Pro 3.5.2 上实测过（见设计规格 §8.1 的偏差表）。
三条实测约束决定了本模块的写法，违反任一条都会「定义落盘成功但渲染为空或为灰」且不报错：
  * CIMRGBColor 的颜色存在 values=[r,g,b,alpha]，red/green/blue 关键字被静默忽略；
  * 从零构造的符号会落盘但完全不渲染，必须深拷贝运行时生成的默认符号再改色；
  * 栅格符号化走 definition.colorizer，CIMRasterLayer.renderer 恒为 None。

本模块自身不做 IO、不碰工程文件、不决定输出路径：只接受 CIM definition 并原地改好它；
字段值的读取（collect_values / collect_categories）通过调用方注入的 arcpy 游标句柄委托进行。
工程文件的打开与保存全部归 cartography.py。
"""
from __future__ import annotations

import copy
import math

from .analysis_tools import RASTER_TYPES

KINDS = ("SEQUENTIAL", "QUALITATIVE", "TERRAIN", "BASE")
CLASSIFY_METHODS = ("EQUAL_INTERVAL", "QUANTILE")
# 可用分级档数在 SEQUENTIAL 定义后由色板实际档数派生（见 CLASS_COUNTS），绝不写死字面量。
# 标注字号范围（pt）：catalog.py 的 schema 上下限直接取自这里，单一事实来源。
LABEL_SIZES = (6, 24)
LABEL_SIZE_DEFAULT = 9
MAX_UNIQUE_VALUES = 10
VALUE_CAP = 200000
NUMERIC_FIELD_TYPES = frozenset({"Integer", "Integer64", "SmallInteger", "Float", "Double"})
TEXT_FIELD_TYPES = frozenset({"Text", "String"})

# 分级色彩与唯一值的轮廓是固定的，不开放参数：分级用细白线让色块干净分离，
# 唯一值用深灰线强调类别边界。
OUTLINES = {"CLASS_BREAKS": ("#FFFFFF", 0.2), "UNIQUE_VALUE": ("#666666", 0.4)}

# SEQUENTIAL 按 3/5/7 三档分别存色值，不做等距抽样：ColorBrewer 的分档色是专门调过的，
# naive 抽样会出现相邻两级几乎同色。
SEQUENTIAL = {
    "BLUES": {3: ("#deebf7", "#9ecae1", "#3182bd"),
              5: ("#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"),
              7: ("#eff3ff", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#08306b")},
    "GREENS": {3: ("#e5f5e0", "#a1d99b", "#31a354"),
               5: ("#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"),
               7: ("#edf8e9", "#c7e9c0", "#a1d99b", "#74c476", "#41ab5d", "#238b45", "#00441b")},
    "REDS": {3: ("#fee0d2", "#fc9272", "#de2d26"),
             5: ("#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"),
             7: ("#fee5d9", "#fcbba1", "#fc9272", "#fb6a4a", "#ef3b2c", "#cb181d", "#67000d")},
    # 低值蓝、高值红、中间中性白，用于距均值偏差与增减变化。
    "RD_BU": {3: ("#67a9cf", "#f7f7f7", "#ef8a62"),
              5: ("#0571b0", "#92c5de", "#f7f7f7", "#f4a582", "#ca0020"),
              7: ("#2166ac", "#67a9cf", "#d1e5f0", "#f7f7f7", "#fddbc7", "#ef8a62", "#b2182b")},
}

# 分级矢量只能落在预设色板真正备有色值的档数上。这里对所有 SEQUENTIAL 色板的档数求交集，
# 得到唯一的合法档数集合：schema、校验器与色板表从此同源，绝不会出现 schema 放行 classes=4/6
# 而色板无对应色值、要到复制工程模板之后的 plan_style 才被拒的脱节（终审发现的 Important）。
# COLORBREWER 的分档色是 3/5/7 专门调过的，不做等距补档。
CLASS_COUNTS = tuple(sorted(set.intersection(*(set(steps) for steps in SEQUENTIAL.values()))))
MIN_CLASSES, MAX_CLASSES = CLASS_COUNTS[0], CLASS_COUNTS[-1]

# TERRAIN 只有 7 档：栅格色带恒为 6 段线性色带拼成的多段色带。
TERRAIN = {
    "ELEVATION": {7: ("#1a6b3c", "#5a9c4f", "#a3b18a", "#e3d49a", "#d19a63", "#a4674a", "#d9d6cf")},
    "HILLSHADE_GRAY": {7: ("#ffffff", "#e0e0e0", "#c2c2c2", "#a6a6a6", "#8a8a8a", "#6e6e6e", "#4d4d4d")},
}

# ColorBrewer Paired 重排为饱和色优先，保证类别少时也高度可辨。
CATEGORY10 = ("#1f78b4", "#e31a1c", "#33a02c", "#ff7f00", "#6a3d9a",
              "#a6cee3", "#fb9a99", "#b2df8a", "#fdbf6f", "#cab2d6")

# 语义色的关键词组顺序是有效载荷，不是装饰：匹配按组顺序取首个命中，
# 所以「水浇地」「湿地」这类含「水」的地类必须排在水体之前，否则会静默错配。
LAND_USE_SEMANTIC = (
    (("耕", "农田", "旱地", "水田", "水浇地", "crop", "farmland"), "#E3C777"),
    (("湿", "沼泽", "wetland", "marsh"), "#6E9BA8"),
    (("水体", "水域", "水", "河", "湖", "库", "water", "river", "lake", "reservoir"), "#3B7DBF"),
    (("林", "乔", "forest", "wood"), "#2E7D32"),
    (("草", "grass", "meadow"), "#9CC47A"),
    (("园", "果园", "orchard", "garden"), "#7FA85C"),
    (("建成", "城镇", "城市", "居民点", "工矿", "urban", "built"), "#B85C4E"),
    (("裸", "bare"), "#A6927B"),
)

BASE = {"fill": "#DCE6F1", "outline": "#5B7A99", "outline_width": 0.7,
        "line": "#4D4D4D", "line_width": 1.2,
        "point": "#3B6E8F", "point_size": 6.0, "point_outline": "#FFFFFF"}

PALETTES = {name: {"kind": "SEQUENTIAL", "steps": steps} for name, steps in SEQUENTIAL.items()}
PALETTES.update({name: {"kind": "TERRAIN", "steps": steps} for name, steps in TERRAIN.items()})
PALETTES["CATEGORY10"] = {"kind": "QUALITATIVE", "colors": CATEGORY10}
PALETTES["LAND_USE"] = {"kind": "QUALITATIVE", "semantic": LAND_USE_SEMANTIC, "fallback": "#9E9E9E"}
PALETTES["BASE"] = dict(BASE, kind="BASE")
# 按名字重排一次：字典插入顺序取决于上面几个 update 的先后，重排后错误消息与 enum 顺序才稳定。
PALETTES = {name: PALETTES[name] for name in sorted(PALETTES)}
PALETTE_NAMES = sorted(PALETTES)


def hex_rgb(value):
    """'#08519c' -> (8, 81, 156)。CIMRGBColor.values 要的就是这三个数加 alpha。"""
    text = value.lstrip("#")
    if len(text) != 6:
        raise ValueError(f"色值必须是 #RRGGBB 形式：{value}")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def palette_colors(name, classes):
    if name not in PALETTES:
        raise ValueError(f"未知色板：{name}；可用色板：{', '.join(PALETTE_NAMES)}。")
    steps = PALETTES[name].get("steps")
    if not steps:
        raise ValueError(f"色板 {name} 不支持分级，它是 {PALETTES[name]['kind']} 类型。")
    if classes not in steps:
        raise ValueError(f"色板 {name} 只有 {sorted(steps)} 档；classes={classes} 无对应色值。")
    return steps[classes]


def ramp_colors(name):
    if name not in PALETTES or 7 not in PALETTES[name].get("steps", {}):
        raise ValueError(f"色板 {name} 没有 7 档色带，不能用于栅格拉伸；"
                         f"可用：{', '.join(sorted(SEQUENTIAL)) + ', ' + ', '.join(sorted(TERRAIN))}。")
    return PALETTES[name]["steps"][7]


def land_use_color(value):
    """把地类名映射到语义色。返回 (色值, 是否命中)；未命中给兜底灰，由调用方收集后回报用户。"""
    text = (value or "").strip().lower()
    if not text:
        return PALETTES["LAND_USE"]["fallback"], False
    for keywords, color in LAND_USE_SEMANTIC:
        if any(keyword.lower() in text for keyword in keywords):
            return color, True
    return PALETTES["LAND_USE"]["fallback"], False


def build_classes(values, classes, method):
    """把数值列表变成分级上界。返回的 bounds 长度恒等于 classes。

    末级强制等于最大值：等间距的浮点累加可能差最后一个小数位，那样最大的那个要素
    会掉出所有分级，渲成空白且不报错。
    """
    if not values:
        raise ValueError("该字段没有有效数值，无法分级；请先用 describe 与 read_rows 检查字段内容。")
    low, high = min(values), max(values)
    if high == low:
        raise ValueError(f"该字段取值全部相同（{low:g}），无法分级；请换一个有区分度的字段，"
                         f"或改用 BASE 色板出示意图。")
    if method == "EQUAL_INTERVAL":
        step = (high - low) / classes
        bounds = [low + step * (index + 1) for index in range(classes)]
    else:
        ordered = sorted(values)
        total = len(ordered)
        bounds = [ordered[min(total - 1, max(0, math.ceil((index + 1) * total / classes) - 1))]
                  for index in range(classes)]
    bounds[-1] = high
    return {"minimum": low, "maximum": high, "bounds": bounds}


def collect_values(arcpy, dataset, field, cap=VALUE_CAP):
    """读字段数值。超过 cap 时按固定步长系统抽样，并把「抽过样」如实回报出去。

    抽样必须显式回报：分位数断点在抽样下只是近似值，模型和用户都需要知道这一点，
    否则会以为分级是精确的。
    """
    total = int(arcpy.management.GetCount(dataset)[0])
    values = []
    if total <= cap:
        with arcpy.da.SearchCursor(dataset, [field]) as cursor:
            for row in cursor:
                if row[0] is not None:
                    values.append(float(row[0]))
        return values, False, total
    stride = math.ceil(total / cap)
    with arcpy.da.SearchCursor(dataset, [field]) as cursor:
        for index, row in enumerate(cursor):
            if index % stride == 0 and row[0] is not None:
                values.append(float(row[0]))
    return values, True, total


def collect_categories(arcpy, dataset, field, limit=MAX_UNIQUE_VALUES):
    """收集唯一值（字典序，保证同样数据每次出图配色一致）。超出 limit 即刻返回并标记。"""
    seen = set()
    with arcpy.da.SearchCursor(dataset, [field]) as cursor:
        for row in cursor:
            if row[0] is None:
                continue
            text = str(row[0]).strip()
            if not text:
                continue
            seen.add(text)
            if len(seen) > limit:
                return sorted(seen), True
    return sorted(seen), False


RENDERERS = ("CLASS_BREAKS", "UNIQUE_VALUE", "SIMPLE", "RASTER_RAMP")


def field_types(desc):
    return {item.name: item.type for item in (desc.fields or [])}


def _is_raster(desc):
    return desc.dataType in RASTER_TYPES


def _geometry(desc):
    shape = getattr(desc, "shapeType", None)
    if not shape:
        return None
    return {"Polygon": "Polygon", "Polyline": "Polyline", "Line": "Polyline",
            "Point": "Point", "Multipoint": "Point"}.get(shape, shape)


def renderer_kind(name, desc):
    """由「色板 kind × 数据是栅格还是矢量 × 矢量几何类型」推断 renderer，不作为参数暴露。"""
    if name not in PALETTES:
        raise ValueError(f"未知色板：{name}；可用色板：{', '.join(PALETTE_NAMES)}。")
    kind = PALETTES[name]["kind"]
    if _is_raster(desc):
        if kind in ("SEQUENTIAL", "TERRAIN"):
            return "RASTER_RAMP"
        raise ValueError(f"色板 {name}（{kind}）只适用于矢量要素，而该数据是栅格；"
                         f"栅格请用 {', '.join(sorted(SEQUENTIAL) + sorted(TERRAIN))}。")
    if desc.dataType != "FeatureClass" or _geometry(desc) is None:
        raise ValueError(f"该数据是 {desc.dataType}，没有可渲染的几何，无法符号化。")
    if kind == "SEQUENTIAL":
        return "CLASS_BREAKS"
    if kind == "QUALITATIVE":
        return "UNIQUE_VALUE"
    if kind == "BASE":
        return "SIMPLE"
    raise ValueError(f"色板 {name} 只适用于栅格，而该数据是矢量要素类；"
                     f"矢量请用 {', '.join(sorted(SEQUENTIAL) + ['CATEGORY10', 'LAND_USE', 'BASE'])}。")


def _check_field(style, desc, allowed, wanted, requirement):
    """校验并返回字段名。

    requirement 是写给用户看的类型要求说明，必须出现在错误消息里 —— 只说「类型不对」
    而不说「要什么类型」，模型就没法自己纠正。可用字段列表按 allowed 过滤后给出，
    让模型能直接挑一个合法的换上。
    """
    fields = field_types(desc)
    field = style.get("field")
    usable = ", ".join(k for k, v in sorted(fields.items()) if v in allowed) or "（无）"
    if not field:
        # 标注 spec 没有 palette 键；缺 field 也必须给 ValueError 而不是 KeyError。
        subject = f"色板 {style['palette']} " if "palette" in style else ""
        raise ValueError(f"{subject}走{wanted}，必须提供 field 参数；"
                         f"该数据集可用的字段有：{usable}。")
    if field not in fields:
        raise ValueError(f"字段 {field} 不存在；该数据集的字段有：{', '.join(sorted(fields))}。")
    if fields[field] not in allowed:
        raise ValueError(f"字段 {field} 是 {fields[field]} 类型，而{wanted}要求{requirement}；"
                         f"该数据集可用的字段有：{usable}。")
    return field


NUMERIC_REQUIREMENT = "数值类型（Integer / SmallInteger / Float / Double）"
LABELABLE_REQUIREMENT = "文本或数值类型（日期字段请先在 ArcGIS Pro 里格式化成文本）"


def validate_style_spec(style, desc):
    """校验一条 style，返回它实际要走的 renderer kind。全部拒绝都在复制工程模板之前发生。"""
    kind = renderer_kind(style["palette"], desc)
    # 不生效的参数必须显式拒绝：静默忽略会让模型以为自己的参数起作用了。
    # 唯一值也不接受 classes/method —— 类别数由数据决定，分级方法对它没有意义。
    banned = {"SIMPLE": ("field", "classes", "method"),
              "RASTER_RAMP": ("field", "classes", "method"),
              "UNIQUE_VALUE": ("classes", "method"),
              "CLASS_BREAKS": ()}[kind]
    for name in banned:
        if name in style:
            # 栅格色带按内置拉伸统计上色，没有「按字段分级」这条路（SEQUENTIAL 到栅格同样走
            # RASTER_RAMP 并同样拒绝 field）。矢量才建议「改用分级色板」；对栅格那样说会让模型
            # 照着换一个照样被拒的色板，白跑一轮，所以栅格给专门的提示。
            if kind == "RASTER_RAMP":
                raise ValueError(f"栅格色带（{style['palette']}）按内置拉伸统计上色，不接受 {name} 参数；"
                                 f"请去掉它。若要按字段分级，请先用 reclassify 离散化或转成要素类。")
            raise ValueError(f"色板 {style['palette']} 走{kind}，不接受 {name} 参数；"
                             f"请去掉它，或改用支持分级的色板（{', '.join(sorted(SEQUENTIAL))}）。")
    if kind in ("SIMPLE", "RASTER_RAMP"):
        return kind
    if kind == "CLASS_BREAKS":
        _check_field(style, desc, NUMERIC_FIELD_TYPES, "分级色彩", NUMERIC_REQUIREMENT)
        classes = style.get("classes", 5)
        # 合法档数来自预设色板真正备有色值的档数（CLASS_COUNTS），不是任意区间：schema 与本校验
        # 同源，schema 放行的值这里必然同样放行，反之亦然。
        if classes not in CLASS_COUNTS:
            raise ValueError(f"classes 只能取 {'/'.join(str(count) for count in CLASS_COUNTS)}"
                             f"（预设色板只在这些档数上备有色值），收到 {classes}；"
                             f"分级超过 {MAX_CLASSES} 级制图不可读。")
        method = style.get("method", "EQUAL_INTERVAL")
        if method not in CLASSIFY_METHODS:
            raise ValueError(f"method 只能是 {' 或 '.join(CLASSIFY_METHODS)}，收到 {method}。")
        return kind
    field = _check_field(style, desc, NUMERIC_FIELD_TYPES | TEXT_FIELD_TYPES,
                         "唯一值渲染", LABELABLE_REQUIREMENT)
    if field_types(desc)[field] in ("Float", "Double"):
        raise ValueError(f"字段 {field} 是连续浮点数，做唯一值会产生大量类别且图例不可读；"
                         f"请改用分级色彩（{', '.join(sorted(SEQUENTIAL))}）配 field 与 classes。")
    return kind


def validate_label_spec(label, desc):
    if _is_raster(desc):
        raise ValueError("栅格图层没有属性字段，无法标注；如需注记请用 build_layout 的 title，"
                         "或先把栅格转成要素类。")
    _check_field(label, desc, NUMERIC_FIELD_TYPES | TEXT_FIELD_TYPES, "标注", LABELABLE_REQUIREMENT)
    size = label.get("size", LABEL_SIZE_DEFAULT)
    if not LABEL_SIZES[0] <= size <= LABEL_SIZES[1]:
        raise ValueError(f"标注字号必须在 {LABEL_SIZES[0]}–{LABEL_SIZES[1]} pt 之间，收到 {size}。")


def rgb_color(arcpy, hexcolor, alpha=100):
    """CIMRGBColor 的颜色存在 values 里。red=/green=/blue= 关键字会被静默忽略并渲成灰色。"""
    red, green, blue = hex_rgb(hexcolor)
    color = arcpy.cim.CIMRGBColor()
    color.values = [red, green, blue, alpha]
    return color


def _walk_symbol(node, depth=0):
    if depth > 9 or node is None:
        return
    if isinstance(node, list):
        for item in node:
            yield from _walk_symbol(item, depth + 1)
        return
    yield node
    for name in ("symbol", "symbolLayers", "markerGraphics", "graphic"):
        if hasattr(node, name):
            yield from _walk_symbol(getattr(node, name), depth + 1)


def paint_symbol(arcpy, symbol_ref, fill=None, stroke=None, width=None, size=None):
    """原地改一个 CIMSymbolReference 内部的颜色与尺寸，返回各类改动计数。

    只认 CIMSolidFill / CIMSolidStroke / CIMVectorMarker 三种节点：实测表明改运行时
    生成的默认符号有效，而从零构造的符号会落盘却不渲染。
    arcpy 一律按参数传入、不藏进模块级全局 —— 这是本仓库能离线单测的前提。
    """
    changed = {"fill": 0, "stroke": 0, "width": 0, "size": 0}
    for node in _walk_symbol(symbol_ref):
        kind = type(node).__name__
        if kind == "CIMSolidFill" and fill is not None:
            node.color = rgb_color(arcpy, fill)
            changed["fill"] += 1
        elif kind == "CIMSolidStroke" and stroke is not None:
            node.color = rgb_color(arcpy, stroke)
            changed["stroke"] += 1
            if width is not None:
                node.width = width
                changed["width"] += 1
        elif kind == "CIMVectorMarker" and size is not None:
            node.size = size
            changed["size"] += 1
    return changed


def _class_breaks(arcpy, prototype, plan):
    outline, width = OUTLINES["CLASS_BREAKS"]
    renderer = arcpy.cim.CIMClassBreaksRenderer()
    renderer.field = plan["field"]
    renderer.classBreakType = "GraduatedColor"
    renderer.minimumBreak = plan["minimum"]
    breaks = []
    lower = plan["minimum"]
    for index, upper in enumerate(plan["bounds"]):
        entry = arcpy.cim.CIMClassBreak()
        entry.upperBound = upper
        entry.label = f"{lower:g} - {upper:g}"
        symbol = copy.deepcopy(prototype)
        paint_symbol(arcpy, symbol, fill=plan["colors"][index], stroke=outline, width=width)
        entry.symbol = symbol
        breaks.append(entry)
        lower = upper
    # 容器属性叫 breaks；classBreaks 不是真实属性，赋值会被静默丢弃。
    renderer.breaks = breaks
    return renderer


def _unique_value(arcpy, prototype, plan):
    outline, width = OUTLINES["UNIQUE_VALUE"]
    renderer = arcpy.cim.CIMUniqueValueRenderer()
    renderer.fields = [plan["field"]]
    renderer.useDefaultSymbol = True
    renderer.defaultLabel = "其他"
    default_symbol = copy.deepcopy(prototype)
    paint_symbol(arcpy, default_symbol, fill=PALETTES["LAND_USE"]["fallback"],
                 stroke=outline, width=width)
    renderer.defaultSymbol = default_symbol
    classes = []
    for index, category in enumerate(plan["categories"]):
        value = arcpy.cim.CIMUniqueValue()
        value.fieldValues = [category]
        entry = arcpy.cim.CIMUniqueValueClass()
        entry.values = [value]
        entry.label = category
        entry.visible = True
        entry.editable = True
        symbol = copy.deepcopy(prototype)
        paint_symbol(arcpy, symbol, fill=plan["colors"][index], stroke=outline, width=width)
        entry.symbol = symbol
        classes.append(entry)
    # 容器属性叫 groups，中间还有一层 CIMUniqueValueGroup；直接赋 classes 会被丢弃。
    group = arcpy.cim.CIMUniqueValueGroup()
    group.classes = classes
    group.heading = ""
    renderer.groups = [group]
    return renderer


def _simple(arcpy, prototype, plan):
    paint_symbol(arcpy, prototype, fill=plan.get("fill"), stroke=plan.get("outline"),
                 width=plan.get("outline_width"), size=plan.get("size"))
    return None


def apply_renderer(arcpy, definition, plan):
    """按 plan 原地改符号。SIMPLE 直接改默认符号，不新建 renderer。

    RASTER_RAMP 必须在读 definition.renderer 之前就分派出去：栅格图层的 renderer 恒为
    None（实测结论），先取 .symbol 会直接 AttributeError。分派顺序本身就是这条实测
    约束的落地，不要为了"看起来整齐"把它挪到后面。
    """
    kind = plan["renderer"]
    if kind == "RASTER_RAMP":
        apply_raster_ramp(arcpy, definition, plan)
        return
    if kind == "SIMPLE":
        _simple(arcpy, definition.renderer.symbol, plan)
        return
    prototype = copy.deepcopy(definition.renderer.symbol)
    if kind == "CLASS_BREAKS":
        definition.renderer = _class_breaks(arcpy, prototype, plan)
    elif kind == "UNIQUE_VALUE":
        definition.renderer = _unique_value(arcpy, prototype, plan)
    else:
        raise ValueError(f"未实现的 renderer：{kind}")


def plan_style(arcpy, style, dataset, desc):
    """前置阶段编排器：校验 → 读值 → 算断点/唯一值 → 返回 plan。

    这一步全部发生在复制工程模板之前，因此任何拒绝都不会残留半成品 .aprx。
    返回的 plan 既交给 apply_renderer，也原样回报给模型 —— 模型看不见图，
    只有拿到断点与实际色值才能如实说明「这张图是怎么分级的」。
    """
    kind = validate_style_spec(style, desc)
    palette = style["palette"]
    plan = {"layer": dataset, "palette": palette, "renderer": kind}
    if kind == "RASTER_RAMP":
        plan["colors"] = list(ramp_colors(palette))
        return plan
    if kind == "SIMPLE":
        base = PALETTES["BASE"]
        geometry = _geometry(desc)
        plan["geometry"] = geometry
        if geometry == "Polygon":
            plan.update({"fill": base["fill"], "outline": base["outline"],
                         "outline_width": base["outline_width"]})
        elif geometry == "Polyline":
            plan.update({"fill": None, "outline": base["line"],
                         "outline_width": base["line_width"]})
        else:
            plan.update({"fill": base["point"], "outline": base["point_outline"],
                         "outline_width": 0.5, "size": base["point_size"]})
        return plan
    if kind == "CLASS_BREAKS":
        classes = style.get("classes", 5)
        method = style.get("method", "EQUAL_INTERVAL")
        values, sampled, total = collect_values(arcpy, dataset, style["field"])
        result = build_classes(values, classes, method)
        plan.update({"field": style["field"], "method": method, "classes": classes,
                     "minimum": result["minimum"], "maximum": result["maximum"],
                     "bounds": result["bounds"], "colors": list(palette_colors(palette, classes)),
                     "sampled": sampled, "value_count": total})
        return plan
    categories, exceeded = collect_categories(arcpy, dataset, style["field"])
    if exceeded:
        raise ValueError(f"字段 {style['field']} 的唯一值超过 {MAX_UNIQUE_VALUES} 类"
                         f"（已数到 {len(categories)} 仍未数完），分类图不可读；"
                         f"请改用分级色彩（{', '.join(sorted(SEQUENTIAL))}），"
                         f"或先用 dissolve / calculate_field 归并类别。")
    if not categories:
        raise ValueError(f"字段 {style['field']} 没有非空的类别值，无法做唯一值渲染。")
    if palette == "LAND_USE":
        pairs = [(item, land_use_color(item)) for item in categories]
        colors = [color for _, (color, _) in pairs]
        unmatched = [item for item, (_, matched) in pairs if not matched]
    else:
        colors = list(PALETTES[palette]["colors"][:len(categories)])
        unmatched = []
    plan.update({"field": style["field"], "categories": categories, "colors": colors,
                 "unmatched": unmatched})
    return plan


def apply_raster_ramp(arcpy, definition, plan):
    """栅格符号化改 colorizer，不改 renderer：CIMRasterLayer.renderer 恒为 None，
    而 arcpy.cim 里根本没有任何 CIMRaster*Renderer 类，只有 CIMRaster*Colorizer。
    """
    colorizer = getattr(definition, "colorizer", None)
    if colorizer is None:
        raise ValueError("该栅格图层没有 colorizer，无法上色；请用 describe 确认它是栅格数据集，"
                         "而不是栅格目录或镶嵌数据集的某个子项。")
    colors = plan["colors"]
    ramp = arcpy.cim.CIMMultipartColorRamp()
    segments = []
    for index in range(len(colors) - 1):
        segment = arcpy.cim.CIMLinearContinuousColorRamp()
        segment.fromColor = rgb_color(arcpy, colors[index])
        segment.toColor = rgb_color(arcpy, colors[index + 1])
        segments.append(segment)
    ramp.colorRamps = segments
    colorizer.colorRamp = ramp


# LABEL_SIZE_DEFAULT 已在文件头部定义（validate_label_spec 直接消费），此处只补字色常量。
LABEL_COLOR = "#000000"


def _arcade_field(field):
    """Arcade 里带空格或特殊字符的字段名必须用中括号加引号取。"""
    return f"$feature.{field}" if field.replace("_", "").isalnum() else f"$feature['{field}']"


def apply_label(arcpy, definition, label):
    """改运行时生成的那个 labelClass，不要整个替换列表。

    实测：新建 CIMLabelClass 赋值给 labelClasses 会落盘成功、labelVisibility 也为真，
    但导出图上一个字都没有；改默认那个才真的渲染。
    """
    existing = list(getattr(definition, "labelClasses", None) or [])
    if not existing:
        raise ValueError("该图层没有可用的 labelClass，无法标注；"
                         "请改用 build_layout 的 title 或图廓元素承载文字。")
    klass = existing[0]
    klass.expression = _arcade_field(label["field"])
    klass.expressionEngine = "Arcade"
    klass.visibility = True
    size = label.get("size", LABEL_SIZE_DEFAULT)
    text_symbol = klass.textSymbol.symbol
    text_symbol.fontSize = size
    text_symbol.height = size
    # 字形颜色在 textSymbol.symbol（CIMPolygonSymbol）里的 CIMSolidFill 上。
    paint_symbol(arcpy, text_symbol.symbol, fill=LABEL_COLOR)
    definition.labelClasses = [klass]
    definition.labelVisibility = True
