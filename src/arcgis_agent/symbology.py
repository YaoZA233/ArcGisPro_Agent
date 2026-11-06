"""预设色板驱动的符号化与标注：把 CIM renderer 与 labelClass 挂到图层定义上。

所有 CIM 构造形态都在本机 ArcGIS Pro 3.5.2 上实测过（见设计规格 §8.1 的偏差表）。
三条实测约束决定了本模块的写法，违反任一条都会「定义落盘成功但渲染为空或为灰」且不报错：
  * CIMRGBColor 的颜色存在 values=[r,g,b,alpha]，red/green/blue 关键字被静默忽略；
  * 从零构造的符号会落盘但完全不渲染，必须深拷贝运行时生成的默认符号再改色；
  * 栅格符号化走 definition.colorizer，CIMRasterLayer.renderer 恒为 None。

本模块不碰工程文件、不做 IO、不决定输出路径：只接受 CIM definition 并原地改好它。
工程文件的打开与保存全部归 cartography.py。
"""
from __future__ import annotations

import math

KINDS = ("SEQUENTIAL", "QUALITATIVE", "TERRAIN", "BASE")
CLASSIFY_METHODS = ("EQUAL_INTERVAL", "QUANTILE")
MIN_CLASSES, MAX_CLASSES = 3, 7
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
