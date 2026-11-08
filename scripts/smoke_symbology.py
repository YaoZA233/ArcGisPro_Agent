"""真机冒烟：符号化与标注。必须用 ArcGIS Pro Python 运行，不是系统 Python。

fixture 全部是 workspace/smoke/<uuid> 下的合成数据，绝不使用用户数据集。
除 ink() 像素占比外，本脚本用颜色直方图断言色板真的落到了导出图上：
探测阶段已证明像素数可反推分级归属（9 个面分 2/2/2/2/1 时，各档像素恰为
72200×4 + 36100），因此这里的断言同时覆盖配色与分类正确性。

四条离线桩证明不了的假设在这里一次性在真机上结算（Task 5/6/7 的交接项）：
  1. 真机反序列化出来的填充层 Python 类名是否就是 CIMSolidFill —— paint_symbol 按类名分派。
     探测阶段的 dump 用 type(obj).__name__ 记的确实是 Python 类名，但那些对象是同一进程里
     getDefinition 直接拿到的；这里改成"另一个进程打开已保存工程的副本"再读类名，证的是
     持久化往返之后分派依据仍然成立（本机 3.5.2 的图层定义对象没有 .JSON 属性，
     不存在第二个"判别字段"可对照）；
  2. 栅格色带的 CIMMultipartColorRamp.colorRamps / CIMLinearContinuousColorRamp.fromColor|toColor
     是不是 3.5.2 的活属性名，以及是否需要 weights；
  3. 标注容器重赋值（definition.labelClasses = [klass]）在真机导出里到底出不出字；
  4. _add_title 从 CIMRGBColor(red=0,...) 改成 .values=[0,0,0,100] 之后图名是否仍是黑色。
探测脚本 workspace/probe-cim/probe_cim6.py（run6-04f464ca 六项全绿）是本脚本色板命中与
export_and_count 统计口径的直接素材来源。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from arcgis_agent import symbology
from arcgis_agent.cartography import MARGIN, TITLE_BAND
from arcgis_agent.runtime import ArcPyRuntime

PAGE_WIDTH_MM, PAGE_HEIGHT_MM = 297.0, 210.0   # A4 横向，与 build_layout 的默认版面一致
MEASURE_DPI = 150                              # 直方图测量用的导出分辨率
TEXT_DPI = 300                                 # 文字类像元断言专用：低分辨率下抗锯齿吃掉满覆盖
BLUES5 = list(symbology.SEQUENTIAL["BLUES"][5])
ELEV7 = list(symbology.ramp_colors("ELEVATION"))
GRAY7 = list(symbology.ramp_colors("HILLSHADE_GRAY"))
CATEGORY5 = list(symbology.CATEGORY10[:5])
WATER = "#3B7DBF"
UNMATCHED_VALUE = "盐田"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_manifest(folder, top_level_only=False):
    """fixture 目录的内容指纹：相对路径 → 该文件的 SHA256。

    ArcGIS 在打开/渲染数据时会在 .gdb 与栅格旁边临时落锁文件、统计缓存（.aux.xml）、
    金字塔附属文件，名字每次都不一样。因此这里不做"整个目录摘要相等"这种必然误报的断言，
    而是分开看：已存在的文件内容有没有变、有没有消失（这是'源数据被改'的真正含义），
    以及新出现了哪些文件（副作用，如实报出但不算作被修改）。

    锁文件在本机实测是读不了的（`_gdb.<主机>.<pid>.<n>.sr.lock` 被 ArcGIS 独占，
    open() 直接 PermissionError），所以读不了的文件记为 `<locked>` 而不抛异常：
    它们的名字本来就命中 TRANSIENT，不参与"内容有没有变"的判定。
    """
    paths = Path(folder).iterdir() if top_level_only else Path(folder).rglob("*")
    manifest = {}
    for path in sorted(paths, key=str):
        if not path.is_file():
            continue
        try:
            manifest[path.relative_to(folder).as_posix()] = digest(path)
        except OSError:
            manifest[path.relative_to(folder).as_posix()] = "<locked>"
    return manifest


TRANSIENT = re.compile(r"(?i)(\.lock$|\.dslock$|^\.?lock|lock\.|\.tmp$)")


def manifest_drift(before, after):
    """已存在文件的内容有没有变 / 有没有消失 —— 这才是"源数据被改"的含义。

    ArcGIS 打开数据源时会在 .gdb 里落锁文件，名字每次都可能不同，因此锁文件既不算被改
    也不算被删（单独列在 transient 里如实报出）。新建的缓存文件（.aux.xml、金字塔）只记
    在 created 里供人审阅，不当作违约。
    """
    def strict(manifest):
        return {key: value for key, value in manifest.items() if not TRANSIENT.search(Path(key).name)}

    before_strict, after_strict = strict(before), strict(after)
    changed = sorted(key for key in before_strict if key in after_strict
                     and before_strict[key] != after_strict[key])
    deleted = sorted(set(before_strict) - set(after_strict))
    created = sorted(set(after_strict) - set(before_strict))
    transient = sorted(key for key in set(before) | set(after) if TRANSIENT.search(Path(key).name))
    return {"changed": changed, "deleted": deleted, "created": created, "transient": transient,
            "clean": not changed and not deleted}


def message(result):
    error = result.get("error")
    if isinstance(error, dict):
        return error.get("message", "")
    return error or ""


class Traced:
    """Records every refusal, so a red run says which message came back instead of only which check failed.

    跨参数校验（styles/labels 的 layer 不在 layers 中、同一图层重复）在父进程的 validate_call 里
    就抛 ValueError，根本不起 worker；其余校验在 worker 里返回 ok:false。两条路径都要进 refusals，
    否则拒绝路径的实测错误文本就丢了。
    """

    def __init__(self, runtime):
        self.runtime, self.refusals = runtime, []

    def call(self, name, arguments, run):
        try:
            result = self.runtime.call(name, arguments, run)
        except ValueError as exc:
            result = {"ok": False, "error": {"type": "ValueError", "message": str(exc)}}
        if result.get("ok") is not True:
            self.refusals.append({"tool": name, "output_name": arguments.get("output_name"),
                                  "error": message(result)[:240]})
        return result


def ink(path):
    """Fraction of non-white pixels: proves the map frame actually rendered the data.

    阈值 <250 的由来见 smoke_cartography.py：Pro 给新图层随机选的默认填色在亮度上接近白色。
    用直方图（256 个灰度级计数）而不是逐像素遍历，300 dpi 的成果图也不用把像素全读进内存。
    """
    try:
        from PIL import Image
    except ImportError:
        return {"available": False, "bytes": Path(path).stat().st_size}
    histogram_l = Image.open(str(path)).convert("L").histogram()
    total = sum(histogram_l) or 1
    return {"available": True, "bytes": Path(path).stat().st_size,
            "ratio": round(sum(histogram_l[:250]) / total, 5)}


def histogram(path):
    """RGB 直方图（探测阶段验证过的统计口径）；getcolors 比逐像素 Counter 快两个量级。"""
    from PIL import Image
    image = Image.open(str(path)).convert("RGB")
    colors = image.getcolors(maxcolors=100000000)
    if colors is None:
        return Counter(image.getdata())
    return Counter({key: count for count, key in colors})


def hex_rgb(value):
    return symbology.hex_rgb(value)


def expected_values(hexcolor):
    return list(hex_rgb(hexcolor)) + [100]


def hit(counts, hexcolor, tolerance=4):
    """色板命中：导出图里落在该色 ±tolerance/通道 的像素数（探测阶段的实测口径）。"""
    target = hex_rgb(hexcolor)
    return sum(n for color, n in counts.items()
               if all(abs(color[i] - target[i]) <= tolerance for i in range(3)))


def palette_hits(counts, palette, tolerance=4):
    return {color: hit(counts, color, tolerance) for color in palette}


def dark_pixels(counts, threshold=300):
    """近黑像素总数（R+G+B < threshold）：标注字形与图名线划的公共载体。"""
    return sum(n for color, n in counts.items() if sum(color) < threshold)


def black_pixels(counts):
    return sum(n for color, n in counts.items() if max(color) < 60)


def proportional(hits, weights):
    """各档像素数是否按要素数等比：每像素当量 = 像素数 / 该类要素数，取均值后逐项比。

    分级图与唯一值图的面元大小相同（都是 1°×1°），因此像素数应当严格正比于要素数；
    某档 0 像素或某档吞掉两档，都说明断点或类别归属错了 —— 这正是"落盘成功但渲错"的静默类。
    """
    units = [value / count for value, count in zip(hits, weights) if count]
    if not units:
        return {"per_pixel_unit": [], "max_deviation": None, "all_positive": all(value > 0 for value in hits)}
    mean = sum(units) / len(units)
    return {"per_pixel_unit": [round(item, 1) for item in units],
            "max_deviation": round(max(abs(item - mean) / mean for item in units), 4),
            "all_positive": all(value > 0 for value in hits)}


def color_values_of(color):
    values = getattr(color, "values", None) if color is not None else None
    return list(values) if isinstance(values, (list, tuple)) else None


def _has(node, name):
    """CIM 对象读不存在的属性时是抛 AttributeError 而不是返回 None，getattr 的默认值能兜住。"""
    return getattr(node, name, "<absent>") != "<absent>"


def walk_cim(node, depth=0):
    """遍历真机 CIM 树。

    比 production 的 _walk_symbol 宽（另加 breaks/groups/classes/values/colorRamps 五条容器边），
    目的是"读回整棵树看看真机给了什么类名"，而不是复现分派逻辑 —— 复现了就是自证。
    """
    if depth > 12 or node is None:
        return
    if isinstance(node, (list, tuple)):
        for item in node:
            yield from walk_cim(item, depth + 1)
        return
    yield node
    for name in ("symbol", "symbolLayers", "markerGraphics", "graphic",
                 "breaks", "groups", "classes", "values", "colorRamps"):
        try:
            child = getattr(node, name, None)
        except Exception:
            continue
        if child is not None:
            yield from walk_cim(child, depth + 1)


def main():
    import arcpy

    started = time.time()
    root = (REPO / "workspace/smoke" / ("symbology_" + uuid.uuid4().hex)).resolve()
    root.mkdir(parents=True)
    run = root / "out"
    review = root / "review"
    readback = root / "readback"
    review.mkdir()        # 人眼验收图与 review/hillshade-skipped.txt 都落在这里
    readback.mkdir()      # 真机 CIM 读回一律在 .aprx 副本上做，绝不 in-process 开生产工程
    folder_for = {"01_thematic_class_breaks": "thematic_class_breaks",
                  "02_land_use_unique_value": "land_use_unique_value",
                  "03_terrain_hillshade_over_tint": "terrain_hillshade_over_tint",
                  "03b_terrain_elevation_only": "terrain_elevation_only",
                  "04_standards_deliverable_with_labels": "standards_with_labels"}

    # ---------------- fixture（全部合成，写在 workspace/smoke/<uuid> 下）----------------
    gdb = arcpy.management.CreateFileGDB(str(root), "input.gdb")[0]
    sr = arcpy.SpatialReference(4326)
    poly = str(Path(gdb) / "zones")
    arcpy.management.CreateFeatureclass(gdb, "zones", "POLYGON", spatial_reference=sr)
    arcpy.management.AddField(poly, "VALUE", "DOUBLE")
    arcpy.management.AddField(poly, "FLATVALUE", "DOUBLE")
    for name in ("CATEGORY", "NAME"):
        arcpy.management.AddField(poly, name, "TEXT", field_length=32)
    # 标注字段类型拒绝路径需要一个既非文本也非数值的字段类型
    arcpy.management.AddField(poly, "ARCHIVED", "DATE")
    cats = ["水体", "林地", "耕地", "建成区", "草地"]
    # 9 个 1°×1° 面、VALUE=5..109 步长 13 → 等间距 5 档恰好分 2/2/2/2/1（与探测阶段同一分布）
    with arcpy.da.InsertCursor(poly, ["SHAPE@", "VALUE", "FLATVALUE", "CATEGORY", "NAME"]) as rows:
        for index in range(9):
            x0, y0 = 110.0 + (index % 3), 25.0 + (index // 3)
            ring = arcpy.Array([arcpy.Point(x0, y0), arcpy.Point(x0 + 1, y0), arcpy.Point(x0 + 1, y0 + 1),
                                arcpy.Point(x0, y0 + 1), arcpy.Point(x0, y0)])
            rows.insertRow([arcpy.Polygon(ring, sr), float(index * 13 + 5), 7.0, cats[index % 5],
                            f"区域{index + 1}"])
    zone_values = [float(row[0]) for row in arcpy.da.SearchCursor(poly, ["VALUE"])]

    # 唯一值基数拒绝路径：12 个互不相同的类别值（> MAX_UNIQUE_VALUES=10）
    many = str(Path(gdb) / "many")
    arcpy.management.CreateFeatureclass(gdb, "many", "POLYGON", spatial_reference=sr)
    arcpy.management.AddField(many, "CAT12", "TEXT", field_length=16)
    with arcpy.da.InsertCursor(many, ["SHAPE@", "CAT12"]) as rows:
        for index in range(12):
            x0, y0 = 116.0 + (index % 4) * 0.2, 27.0 + (index // 4) * 0.2
            ring = arcpy.Array([arcpy.Point(x0, y0), arcpy.Point(x0 + 0.18, y0), arcpy.Point(x0 + 0.18, y0 + 0.18),
                                arcpy.Point(x0, y0 + 0.18), arcpy.Point(x0, y0)])
            rows.insertRow([arcpy.Polygon(ring, sr), f"CAT{index + 1:02d}"])

    line = str(Path(gdb) / "roads")
    arcpy.management.CreateFeatureclass(gdb, "roads", "POLYLINE", spatial_reference=sr)
    with arcpy.da.InsertCursor(line, ["SHAPE@"]) as rows:
        for index in range(3):
            rows.insertRow([arcpy.Polyline(arcpy.Array([arcpy.Point(110.2 + index * 0.2, 25.2),
                                                        arcpy.Point(112.8, 27.4 + index * 0.2)]), sr)])
    point = str(Path(gdb) / "samples")
    arcpy.management.CreateFeatureclass(gdb, "samples", "POINT", spatial_reference=sr)
    # 点图层要参与标注 A/B：它几乎全白底，是唯一能让 ink() 对字形敏感的底图
    # （面/栅格专题图的填充本身就已计入 ink，字形叠加在填充上不改变非白像素占比）。
    arcpy.management.AddField(point, "SITE", "TEXT", field_length=32)
    with arcpy.da.InsertCursor(point, ["SHAPE@", "SITE"]) as rows:
        for index in range(4):
            rows.insertRow([arcpy.Point(110.5 + index * 0.7, 25.5 + index * 0.6), f"样本{index + 1}"])

    # 土地利用面：4 个 0.4°×0.4° 小面，最后一类的值故意匹配不上任何语义关键词
    land = str(Path(gdb) / "landuse")
    arcpy.management.CreateFeatureclass(gdb, "landuse", "POLYGON", spatial_reference=sr)
    arcpy.management.AddField(land, "LU_TYPE", "TEXT", field_length=32)
    with arcpy.da.InsertCursor(land, ["SHAPE@", "LU_TYPE"]) as rows:
        for index, kind in enumerate(["水体", "林地", "耕地", UNMATCHED_VALUE]):
            x0, y0 = 114.0 + index * 0.45, 25.0
            ring = arcpy.Array([arcpy.Point(x0, y0), arcpy.Point(x0 + 0.4, y0), arcpy.Point(x0 + 0.4, y0 + 0.4),
                                arcpy.Point(x0, y0 + 0.4), arcpy.Point(x0, y0)])
            rows.insertRow([arcpy.Polygon(ring, sr), kind])

    # 合成地形：三座高斯山 + 缓坡起伏，值域连续 → 分层设色 7 个色标必然各有像元落在其上。
    # 实测（probe_raster_fixture4）：ASCII 栅格在本机被 ASCIIToRaster 拒绝、CreateRandomRaster
    # 的坐标系是 Unknown 且 DefineProjection 对栅格报"参数无效"，两者都会被 build_layout 的
    # "坐标系未知"拒掉；鱼网面 + PolygonToRaster 继承要素类坐标系，是唯一可用且观感平滑的路径。
    def smooth_surface(x, y):
        value = (900.0 * math.exp(-(((x - 111.0) / 0.55) ** 2 + ((y - 27.0) / 0.55) ** 2))
                 + 1500.0 * math.exp(-(((x - 112.3) / 0.45) ** 2 + ((y - 26.0) / 0.6) ** 2))
                 + 600.0 * math.exp(-(((x - 110.4) / 0.4) ** 2 + ((y - 25.6) / 0.35) ** 2)))
        return 300.0 + value + 120.0 * math.sin((x - 110.0) * 2.4) * math.cos((y - 25.0) * 2.6)

    cell, columns, rows_count = 0.05, 60, 60
    fishnet = str(Path(gdb) / "terrain_cells")
    arcpy.management.CreateFeatureclass(gdb, "terrain_cells", "POLYGON", spatial_reference=sr)
    arcpy.management.AddField(fishnet, "ELEV", "DOUBLE")
    with arcpy.da.InsertCursor(fishnet, ["SHAPE@", "ELEV"]) as rows:
        for row in range(rows_count):
            for column in range(columns):
                x0, y0 = 110.0 + column * cell, 25.0 + row * cell
                ring = arcpy.Array([arcpy.Point(x0, y0), arcpy.Point(x0 + cell, y0),
                                    arcpy.Point(x0 + cell, y0 + cell), arcpy.Point(x0, y0 + cell),
                                    arcpy.Point(x0, y0)])
                rows.insertRow([arcpy.Polygon(ring, sr), smooth_surface(x0 + cell / 2, y0 + cell / 2)])
    dem = str(Path(gdb) / "dem")
    arcpy.conversion.PolygonToRaster(fishnet, "ELEV", dem, "CELL_CENTER", "#", cell)
    shade, hillshade_ok = str(root / "shade.tif"), False
    try:
        # 本机实测签名：Hillshade(输入栅格, 方位角, 高度角, model_shadows, z_factor) 返回 Raster 对象，
        # 没有输出栅格参数，必须 .save()
        arcpy.sa.Hillshade(dem, 315, 45, "NO_SHADOWS", 1).save(shade)
        hillshade_ok = True
    except Exception as exc:
        (review / "hillshade-skipped.txt").write_text(
            f"Hillshade 不可用，地形图退化为纯分层设色：{type(exc).__name__}: {exc}", encoding="utf-8")

    def feature_table(dataset, field):
        table = {}
        with arcpy.da.SearchCursor(dataset, [field]) as cursor:
            for row in cursor:
                table[row[0]] = table.get(row[0], 0) + 1
        return table

    category_table = feature_table(poly, "CATEGORY")
    land_table = feature_table(land, "LU_TYPE")
    source_state = {"gdb": tree_manifest(gdb), "root": tree_manifest(root, top_level_only=True),
                    "shade": digest(shade) if hillshade_ok else None,
                    "counts": {Path(item).name: int(arcpy.management.GetCount(item)[0])
                               for item in (poly, many, line, point, land, fishnet)}}

    runtime = Traced(ArcPyRuntime(Path(sys.executable)))
    checks, measures = {}, {}
    cartography = run / "cartography"

    def listing():
        return sorted(path.name for path in cartography.iterdir()) if cartography.is_dir() else []

    def publish(tag, title, layers, styles=None, labels=None, elements=None, dpi=MEASURE_DPI, count=True):
        """生产路径：build_layout → export_layout PNG，返回实测数据。"""
        arguments = {"title": title, "layers": list(layers), "output_name": tag}
        if styles is not None:
            arguments["styles"] = styles
        if labels is not None:
            arguments["labels"] = labels
        if elements is not None:
            arguments["elements"] = elements
        build = runtime.call("build_layout", arguments, run)
        data = build.get("data") or {}
        aprx = cartography / f"{tag}.aprx"
        png = cartography / f"{tag}_sheet.png"
        export = {"ok": False, "error": {"message": build.get("error") or "未执行导出"}}
        if build.get("ok") is True:
            export = runtime.call("export_layout", {"project": str(aprx), "output_name": f"{tag}_sheet",
                                                    "format": "PNG", "dpi": dpi}, run)
        return {"build": build, "ok": build.get("ok") is True and export.get("ok") is True and png.is_file(),
                "data": data, "aprx": aprx, "png": png, "export_data": export.get("data") or {},
                "counts": histogram(png) if (count and png.is_file()) else Counter(),
                "ink": ink(png) if png.is_file() else {"missing": True},
                "export_error": message(export)[:200], "build_error": message(build)[:200]}

    def refused(tag, arguments, needle):
        """拒绝路径：必须报出预期文本，且 cartography 目录一个文件都不能多。"""
        before = listing()
        result = runtime.call("build_layout", dict(arguments, output_name=tag), run)
        after = listing()
        checks[f"refuse_{tag}"] = (result.get("ok") is False and needle in message(result)
                                   and after == before and not (cartography / f"{tag}.aprx").exists())
        measures[f"refusal_{tag}"] = {"message": message(result)[:200], "files_before": len(before),
                                      "files_after": len(after), "expected_needle": needle}

    # ---------------- 1. 分级色彩（BLUES 5 档）----------------
    cb = publish("classbreaks", "分级色彩冒烟", [poly],
                 styles=[{"layer": poly, "palette": "BLUES", "field": "VALUE", "classes": 5}], elements=[])
    plan = (cb["data"].get("symbology") or [{}])[0]
    bounds = plan.get("bounds") or []
    # 各档要素数 = 累计"小于等于上界"的条数逐档相减（上界本身不能参与减法）
    cumulative = [sum(1 for value in zone_values if value <= upper) for upper in bounds]
    weights = [count - previous for count, previous in zip(cumulative, [0] + cumulative[:-1])]
    cb_hits = [hit(cb["counts"], color) for color in BLUES5]
    measures["class_breaks"] = {"plan": {key: plan.get(key) for key in
                                         ("renderer", "minimum", "maximum", "classes", "method", "colors", "sampled")},
                                "bounds": [round(item, 3) for item in bounds],
                                "features_per_class": weights, "hits": cb_hits,
                                "palette_hits": palette_hits(cb["counts"], BLUES5), "ink": cb["ink"],
                                "distinct_colors": len(cb["counts"])}
    checks["class_breaks_build_and_export"] = cb["ok"] and cb["data"].get("project_modified") is False
    checks["class_breaks_palette_lands"] = all(value > 0 for value in cb_hits)
    ratio = proportional(cb_hits, weights)
    measures["class_breaks_ratio"] = dict(ratio, hits=cb_hits, features_per_class=weights)
    checks["class_breaks_pixel_counts_match_the_data"] = (len(weights) == 5 and ratio["all_positive"]
                                                        and ratio["max_deviation"] is not None
                                                        and ratio["max_deviation"] < 0.12)

    # ---------------- 2. 唯一值（CATEGORY10）----------------
    uv = publish("uniquevalue", "唯一值冒烟", [poly],
                 styles=[{"layer": poly, "palette": "CATEGORY10", "field": "CATEGORY"}], elements=[])
    uv_plan = (uv["data"].get("symbology") or [{}])[0]
    uv_categories = uv_plan.get("categories") or []
    uv_colors = uv_plan.get("colors") or []
    uv_weights = [category_table.get(item, 0) for item in uv_categories]
    uv_hits = [hit(uv["counts"], color) for color in uv_colors]
    measures["unique_value"] = {"categories": uv_categories, "colors": uv_colors,
                                "features_per_category": uv_weights, "hits": uv_hits,
                                "palette_hits": palette_hits(uv["counts"], CATEGORY5), "ink": uv["ink"]}
    checks["unique_value_build_and_export"] = uv["ok"]
    checks["unique_value_palette_lands"] = (CATEGORY5 == uv_colors[:5]
                                            and all(hit(uv["counts"], color) > 0 for color in CATEGORY5))
    uv_ratio = proportional(uv_hits, uv_weights)
    measures["unique_value_ratio"] = dict(uv_ratio, hits=uv_hits, features_per_category=uv_weights)
    checks["unique_value_pixel_counts_match_the_data"] = (uv_ratio["all_positive"]
                                                        and uv_ratio["max_deviation"] is not None
                                                        and uv_ratio["max_deviation"] < 0.12)

    # ---------------- 3. 土地利用语义色 + 未匹配回报 ----------------
    lu = publish("landuse", "土地利用冒烟", [land],
                 styles=[{"layer": land, "palette": "LAND_USE", "field": "LU_TYPE"}], elements=[])
    lu_plan = (lu["data"].get("symbology") or [{}])[0]
    lu_colors = dict(zip(lu_plan.get("categories") or [], lu_plan.get("colors") or []))
    fallback = symbology.PALETTES["LAND_USE"]["fallback"]
    checks["land_use_build_and_export"] = lu["ok"]
    checks["land_use_semantic_colors"] = (lu_colors.get("水体") == WATER
                                          and lu_colors.get("林地") == "#2E7D32"
                                          and lu_colors.get("耕地") == "#E3C777"
                                          and all(hit(lu["counts"], color) > 0 for color in
                                                  ("#3B7DBF", "#2E7D32", "#E3C777")))
    checks["land_use_unmatched_reported"] = (UNMATCHED_VALUE in (lu_plan.get("unmatched") or [])
                                            and lu_colors.get(UNMATCHED_VALUE) == fallback
                                            and hit(lu["counts"], fallback) > 0
                                            and all(land_table.get(item, 0) > 0
                                                    for item in lu_plan.get("categories") or []))
    measures["land_use"] = {"categories": lu_plan.get("categories"), "colors": lu_colors,
                            "unmatched": lu_plan.get("unmatched"),
                            "features_per_category": {item: land_table.get(item, 0)
                                                      for item in lu_colors},
                            "hits": {item: hit(lu["counts"], color) for item, color in lu_colors.items()},
                            "ink": lu["ink"]}

    # ---------------- 4. BASE 简单符号（面 / 线 / 点）----------------
    sp = publish("simplepoly", "面示意图", [poly], styles=[{"layer": poly, "palette": "BASE"}], elements=[])
    sl = publish("simpleline", "线示意图", [line], styles=[{"layer": line, "palette": "BASE"}], elements=[])
    spoint = publish("simplepoint", "点示意图", [point], styles=[{"layer": point, "palette": "BASE"}], elements=[])
    checks["simple_polygon_fill"] = sp["ok"] and hit(sp["counts"], symbology.BASE["fill"]) > 0
    checks["simple_line_and_point"] = (sl["ok"] and hit(sl["counts"], symbology.BASE["line"]) > 0
                                       and spoint["ok"] and hit(spoint["counts"], symbology.BASE["point"]) > 0)
    measures["simple"] = {"polygon_hit": hit(sp["counts"], symbology.BASE["fill"]),
                          "line_hit": hit(sl["counts"], symbology.BASE["line"]),
                          "point_hit": hit(spoint["counts"], symbology.BASE["point"]),
                          "polygon_plan": (sp["data"].get("symbology") or [{}])[0],
                          "line_plan": (sl["data"].get("symbology") or [{}])[0],
                          "point_plan": (spoint["data"].get("symbology") or [{}])[0],
                          "ink": {"polygon": sp["ink"], "line": sl["ink"], "point": spoint["ink"]}}

    # ---------------- 5. 栅格分层设色（ELEVATION 7 色）----------------
    ra = publish("raster", "栅格分层设色", [dem], styles=[{"layer": dem, "palette": "ELEVATION"}], elements=[])
    raster_plan = (ra["data"].get("symbology") or [{}])[0]
    raster_hits = palette_hits(ra["counts"], ELEV7)
    checks["raster_elevation_ramp"] = (ra["ok"] and all(value > 0 for value in raster_hits.values())
                                       and raster_plan.get("renderer") == "RASTER_RAMP"
                                       and raster_plan.get("colors") == ELEV7)
    measures["raster_elevation"] = {"hits": raster_hits, "ink": ra["ink"], "plan": raster_plan,
                                    "distinct_colors": len(ra["counts"])}

    # 观测项（不计入通过/失败）：同一次 build 里叠两个栅格色带，真机是否还能渲出色板色。
    # 探测阶段（probe_raster_fixture4）实测：单栅格 7 色全中，双栅格叠放时分层设色被
    # 饱和红/绿（#ff2138 / #00fc22 一类不在任何色板里的颜色）取代 —— 属于"落盘成功但渲错"这一类，
    # 这里把它量化记录下来，交控制器决定是否需要修复轮。
    if hillshade_ok:
        pair = publish("rasterpair", "双栅格叠放观测", [dem, shade],
                       styles=[{"layer": dem, "palette": "ELEVATION"},
                               {"layer": shade, "palette": "HILLSHADE_GRAY"}], elements=[])
        saturated = sum(n for color, n in pair["counts"].items()
                        if max(color) - min(color) > 60)
        measures["raster_pair_observation"] = {
            "ok": pair["ok"], "elevation_hits": palette_hits(pair["counts"], ELEV7),
            "hillshade_hits": palette_hits(pair["counts"], GRAY7),
            "saturated_pixels_outside_any_palette": saturated,
            "top8": [("#%02x%02x%02x" % key, value) for key, value in pair["counts"].most_common(8)],
            "note": "饱和色像素不在 ELEVATION/HILLSHADE_GRAY/BASE/CATEGORY10 任一色板内，疑为叠放渲染异常"}

    # ---------------- 6. 标注 ----------------
    # 判据分两条，因为 ink()（非白像素占比）对底图敏感：
    #  • 面/栅格专题图填充本身早已全部计入 ink，字形压在填充上时非白像素占比几乎不变
    #    （run2 实测：同数据带标注与不带标注两次导出的 ink 差只有 6e-05，而近黑像素涨了 2847）；
    #    所以对专题图用"近黑像素增量"这条真正能区分字形的判据。
    #  • 点示意图底图几乎全白（ink 0.00716），字形是主要的非白来源，
    #    于是规格要求的"两次导出 ink() 存在显著差异"必须在这种底图上量才有意义。
    lb = publish("labeled", "分级加标注冒烟", [poly],
                 styles=[{"layer": poly, "palette": "BLUES", "field": "VALUE", "classes": 5}],
                 labels=[{"layer": poly, "field": "NAME", "size": 18}], elements=[])
    label_plan = (lb["data"].get("labels") or [{}])[0]
    plain_dark, plain_black = dark_pixels(cb["counts"]), black_pixels(cb["counts"])
    label_dark, label_black = dark_pixels(lb["counts"]), black_pixels(lb["counts"])
    black_growth = label_black - plain_black
    pp = publish("pointplain", "点示意图（无标注）", [point],
                 styles=[{"layer": point, "palette": "BASE"}], elements=[], dpi=TEXT_DPI)
    pl = publish("pointlabel", "点示意图（有标注）", [point],
                 styles=[{"layer": point, "palette": "BASE"}],
                 labels=[{"layer": point, "field": "SITE", "size": 14}], elements=[], dpi=TEXT_DPI)
    ink_delta = abs(pl["ink"].get("ratio", 0) - pp["ink"].get("ratio", 0))
    point_dark = (dark_pixels(pp["counts"]), dark_pixels(pl["counts"]))
    point_black = (black_pixels(pp["counts"]), black_pixels(pl["counts"]))
    measures["labels"] = {"plan": label_plan, "thematic_ink_plain": cb["ink"],
                          "thematic_ink_labeled": lb["ink"], "thematic_ink_delta":
                          round(abs(lb["ink"].get("ratio", 0) - cb["ink"].get("ratio", 0)), 6),
                          "dark_plain": plain_dark, "dark_labeled": label_dark,
                          "black_plain": plain_black, "black_labeled": label_black,
                          "black_growth": black_growth,
                          "palette_hits_labeled": palette_hits(lb["counts"], BLUES5),
                          "white_base_ink_plain": pp["ink"], "white_base_ink_labeled": pl["ink"],
                          "white_base_ink_delta": round(ink_delta, 6),
                          "white_base_dark": list(point_dark), "white_base_black": list(point_black),
                          "white_base_plan": (pl["data"].get("labels") or [{}])[0],
                          "white_base_dpi": TEXT_DPI}
    checks["labels_build_and_export"] = lb["ok"] and pl["ok"]
    checks["label_plan_echoed_in_result"] = (label_plan.get("field") == "NAME" and label_plan.get("size") == 18)
    # 规格要求的 ink() 显著差异：同一份数据、同一版面、同一分辨率，两次导出只差 labels 参数。
    # 文字类像元断言必须在 300 dpi 上做：run3 在 150 dpi 上实测 14pt 中文字形只多出 1196 个非白
    # 像元，其中近黑 245、深色 314 —— 笔画宽度不足 2 像元，抗锯齿吃掉了满覆盖像元，
    # 阈值定在 500 就变成在考分辨率而不是考渲染（图名那条也是同一个道理，见第 7 节）。
    checks["labels_render"] = (pp["ok"] and pl["ok"] and ink_delta > 0.0002
                               and point_dark[1] - point_dark[0] > 400
                               and point_black[1] - point_black[0] > 300)
    # 专题底图上的同一件事，用对底图不敏感的近黑像素增量来证
    checks["labels_render_on_thematic_base"] = (black_growth > 800 and label_dark > plain_dark)

    # ---------------- 7. 图名仍为黑色（_add_title 改用 .values 之后的回归）----------------
    # 必须在 300 dpi 上量：run2 在 150 dpi 上图名带的主色是 #898989（近黑仅 68 px），
    # 但同一版式在 200/300 dpi 上纯黑分别为 2461/4516 px —— 20pt 中文字在 150 dpi 下笔画
    # 宽不足 2 像元，抗锯齿让字形永远达不到满覆盖，这是统计口径问题而不是颜色没生效。
    # 细笔画的颜色断言只能用高分辨率，这条要写进验证记录。
    from PIL import Image

    def band_box(size, dpi):
        """版面 mm → 像元（行 0 在页面顶端）：图名带在页面顶部 MARGIN .. MARGIN+TITLE_BAND 之间。"""
        scale = dpi / 25.4
        width_px, height_px = size
        return (min(int(MARGIN * scale), width_px - 1), min(int((MARGIN + 4.0) * scale), height_px - 1),
                min(int((PAGE_WIDTH_MM - MARGIN) * scale), width_px),
                min(int((MARGIN + TITLE_BAND) * scale), height_px))

    def band_counts(path, dpi):
        """图名带的 RGB 直方图。"""
        image = Image.open(str(path)).convert("RGB")
        return Counter(image.crop(band_box(image.size, dpi)).getdata())

    title_dpi = 300
    title_export = runtime.call("export_layout", {"project": str(cb["aprx"]), "output_name": "classbreaks_300",
                                                 "format": "PNG", "dpi": title_dpi}, run)
    title_image = Image.open(str(cartography / "classbreaks_300.png")).convert("RGB")
    title_crop = title_image.crop(band_box(title_image.size, title_dpi))
    mask = title_crop.convert("L").point(lambda value: 255 if value < 250 else 0)
    # 图名带里除了字形还有一条铺满整行的横线（图幅上沿），必须按行剔除后再统计，
    # 否则"主色"量到的是那条线而不是字（run2 在 150 dpi 上正是这样误判的）。
    row_ink = [sum(mask.crop((0, row, mask.width, row + 1)).histogram()[1:]) for row in range(mask.height)]
    line_rows = [row for row, count in enumerate(row_ink) if count >= 0.8 * mask.width]
    glyph_rows = [row for row, count in enumerate(row_ink) if 0 < count < 0.8 * mask.width]
    groups, current = [], []
    for row in glyph_rows:
        if current and row != current[-1] + 1:
            groups.append(current)
            current = []
        current.append(row)
    if current:
        groups.append(current)
    glyph_group = max(groups, key=len) if groups else []
    title_band = (Counter(title_crop.crop((0, glyph_group[0], title_crop.width, glyph_group[-1] + 1)).getdata())
                  if glyph_group else Counter())
    title_black = black_pixels(title_band)
    title_nonwhite = sum(count for color, count in title_band.items() if max(color) < 250)
    glyph_mode = next((item for item in title_band.most_common() if max(item[0]) < 250), None)
    glyph_height_px = len(glyph_group)
    inked_columns = [column for column in range(mask.width)
                     if any(mask.getpixel((column, row)) == 255 for row in glyph_group)]
    glyph_left = min(inked_columns, default=None)
    glyph_right = max(inked_columns, default=None)
    title_band_150 = band_counts(cb["png"], MEASURE_DPI)
    # 图名的 CIM 往返：把图名带的横线（图幅上沿）排除后再量字形行段，并读回元素确认
    # CIMSymbolReference/CIMTextSymbol 结构被保存。旧实现把 CIMTextSymbol 直接挂到 graphic，
    # Pro 会静默丢弃它，导致标题回退成约 10pt 的左对齐默认字；当前实现使用可持久化结构。
    title_copy = readback / "title.aprx"
    shutil.copy2(cb["aprx"], title_copy)
    title_project = arcpy.mp.ArcGISProject(str(title_copy))
    title_elements = [item for item in title_project.listLayouts()[0].getDefinition("V3").elements
                      if type(item).__name__ == "CIMGraphicElement"]
    title_graphic = getattr(title_elements[0], "graphic", None) if title_elements else None
    title_symbol_reference = getattr(title_graphic, "symbol", None) if title_graphic is not None else None
    title_symbol = getattr(title_symbol_reference, "symbol", None) if title_symbol_reference is not None else None
    del title_project
    measures["title_symbol_round_trip"] = {
        "graphic_element_count": len(title_elements),
        "graphic_type": type(title_graphic).__name__ if title_graphic is not None else None,
        "text_survived": getattr(title_graphic, "text", None),
        "symbol_reference_is_none": title_symbol_reference is None,
        "symbol_reference_type": type(title_symbol_reference).__name__ if title_symbol_reference is not None else None,
        "text_symbol_is_none": title_symbol is None,
        "text_symbol_type": type(title_symbol).__name__ if title_symbol is not None else None,
        "text_symbol_height": getattr(title_symbol, "height", None),
        "text_symbol_alignment": str(getattr(title_symbol, "horizontalAlignment", None))}
    measures["title_band"] = {"dpi": title_dpi, "export_ok": title_export.get("ok"),
                              "band_px": [title_crop.width, title_crop.height],
                              "rule_rows_excluded": line_rows,
                              "black_pixels": title_black, "nonwhite_pixels": title_nonwhite,
                              "black_fraction": round(title_black / (title_nonwhite or 1), 4),
                              "glyph_mode": [("#%02x%02x%02x" % glyph_mode[0], glyph_mode[1])] if glyph_mode else None,
                              "glyph_rows": [glyph_group[0], glyph_group[-1]] if glyph_group else None,
                              "glyph_height_px": glyph_height_px,
                              "glyph_implied_pt": round(glyph_height_px / 0.8 * 72.0 / title_dpi, 1),
                              "glyph_column_range": [glyph_left, glyph_right],
                              "glyph_centered": (glyph_left is not None
                                                 and abs((glyph_left + glyph_right) / 2 - title_crop.width / 2)
                                                 < 0.1 * title_crop.width),
                              "requested_pt": 20,
                              "top5": [("#%02x%02x%02x" % key, value) for key, value in title_band.most_common(5)],
                              "low_dpi_control_150": {"black_pixels": black_pixels(title_band_150),
                                                      "nonwhite_pixels": sum(count for color, count in
                                                                             title_band_150.items() if max(color) < 250),
                                                      "note": "150 dpi 下抗锯齿使笔画达不到满覆盖、且图幅上沿那条横线"
                                                              "正好铺满整行（主色 #898989），不能用来判断图名颜色"}}
    checks["title_color_still_black_after_the_values_fix"] = (
        title_export.get("ok") is True and title_black > 1000 and glyph_mode is not None
        and max(glyph_mode[0]) < 40)
    checks["title_symbol_round_trip_persists_formatting"] = (
        title_export.get("ok") is True
        and measures["title_symbol_round_trip"]["symbol_reference_is_none"] is False
        and measures["title_symbol_round_trip"]["text_symbol_is_none"] is False
        and measures["title_symbol_round_trip"]["text_symbol_height"] == 20.0)

    # ---------------- 8. 只读性 ----------------
    before = digest(cb["aprx"])
    again = runtime.call("export_layout", {"project": str(cb["aprx"]), "output_name": "classbreaks_second",
                                          "format": "PNG", "dpi": 96}, run)
    checks["export_is_read_only"] = (again.get("ok") is True and digest(cb["aprx"]) == before
                                     and (again.get("data") or {}).get("project_modified") is False)
    after_state = {"gdb": tree_manifest(gdb), "root": tree_manifest(root, top_level_only=True),
                   "shade": digest(shade) if hillshade_ok else None,
                   "counts": {Path(item).name: int(arcpy.management.GetCount(item)[0])
                              for item in (poly, many, line, point, land, fishnet)}}
    gdb_drift = manifest_drift(source_state["gdb"], after_state["gdb"])
    root_drift = manifest_drift(source_state["root"], after_state["root"])
    checks["source_data_unchanged"] = (gdb_drift["clean"] and root_drift["clean"]
                                       and after_state["counts"] == source_state["counts"]
                                       and after_state["shade"] == source_state["shade"])
    measures["source_state"] = {"gdb_drift": gdb_drift, "root_drift": root_drift,
                                "counts_before": source_state["counts"], "counts_after": after_state["counts"],
                                "gdb_file_count": len(list(Path(gdb).rglob("*"))),
                                "note": "created 里的锁文件/.aux.xml/金字塔是 ArcGIS 渲染时的缓存副作用，"
                                        "不是源数据被改写；changed/deleted 非空才算违约。"}

    # ---------------- 9. 九条前置校验（各断言零残留）----------------
    refused("layer_not_in_layers",
            {"title": "残", "layers": [poly],
             "styles": [{"layer": str(line), "palette": "BLUES", "field": "VALUE", "classes": 5}]},
            "不在 layers 中")
    refused("duplicate_style_layer",
            {"title": "残", "layers": [poly],
             "styles": [{"layer": poly, "palette": "BLUES", "field": "VALUE", "classes": 5},
                        {"layer": poly, "palette": "REDS", "field": "VALUE", "classes": 5}]},
            "被符号化了两次")
    refused("terrain_palette_on_vector",
            {"title": "残", "layers": [poly], "styles": [{"layer": poly, "palette": "ELEVATION"}]},
            "只适用于栅格")
    refused("class_breaks_on_text_field",
            {"title": "残", "layers": [poly],
             "styles": [{"layer": poly, "palette": "BLUES", "field": "CATEGORY", "classes": 5}]},
            "分级色彩要求")
    refused("unique_value_cardinality",
            {"title": "残", "layers": [many],
             "styles": [{"layer": many, "palette": "CATEGORY10", "field": "CAT12"}]},
            "唯一值超过")
    refused("unknown_field",
            {"title": "残", "layers": [poly],
             "styles": [{"layer": poly, "palette": "BLUES", "field": "NO_SUCH_FIELD", "classes": 5}]},
            "不存在")
    refused("constant_field_values",
            {"title": "残", "layers": [poly],
             "styles": [{"layer": poly, "palette": "BLUES", "field": "FLATVALUE", "classes": 5}]},
            "取值全部相同")
    refused("field_on_base_palette",
            {"title": "残", "layers": [poly], "styles": [{"layer": poly, "palette": "BASE", "field": "VALUE"}]},
            "不接受 field 参数")
    refused("label_on_unsupported_field",
            {"title": "残", "layers": [poly], "labels": [{"layer": poly, "field": "ARCHIVED"}]},
            "标注要求")
    stray = [name for name in listing()
             if len(Path(name).stem.split(".")) > 1
             or not name.endswith((".aprx", ".png", ".pdf", ".tif", ".jpg", ".svg"))]
    checks["only_published_files_remain"] = stray == []
    measures["cartography_dir"] = listing()

    # ---------------- 10. 抽样标志（进程内直连真 arcpy + 真 fixture）----------------
    # worker 是子进程，父进程改常量它看不见；这里把 collect_values 的默认上限临时调小到 2
    # （要素共 9 条），走的仍是 plan_style → collect_values 这条生产代码路径。
    desc = arcpy.Describe(poly)
    defaults = symbology.collect_values.__defaults__
    symbology.collect_values.__defaults__ = (2,)
    try:
        sampled_plan = symbology.plan_style(arcpy, {"layer": poly, "palette": "BLUES", "field": "VALUE",
                                                    "classes": 5}, poly, desc)
    finally:
        symbology.collect_values.__defaults__ = defaults
    checks["sampled_flag_reported"] = (sampled_plan.get("sampled") is True
                                       and sampled_plan.get("value_count") == 9
                                       and plan.get("sampled") is False)
    measures["sampled"] = {"sampled_plan": {key: sampled_plan.get(key) for key in
                                            ("sampled", "value_count", "minimum", "maximum", "bounds")},
                           "worker_plan_sampled": plan.get("sampled"), "value_cap": symbology.VALUE_CAP}

    # ---------------- 11. 四条离线证不了的假设：真机 CIM 读回 ----------------
    def readback_layer(item, tag):
        """在 .aprx 的副本上读回，绝不 in-process 打开生产工程（只读性核验只留给 worker）。"""
        target = readback / f"{tag}.aprx"
        shutil.copy2(item["aprx"], target)
        project = arcpy.mp.ArcGISProject(str(target))
        try:
            return project.listMaps()[0].listLayers()[0].getDefinition("V3")
        finally:
            del project

    node_types, fill_evidence = {}, {}
    for tag, item in (("classbreaks", cb), ("uniquevalue", uv), ("simplepoly", sp), ("labeled", lb)):
        definition = readback_layer(item, tag)
        renderer = getattr(definition, "renderer", None)
        names = Counter(type(node).__name__ for node in walk_cim(renderer))
        fills = [node for node in walk_cim(renderer) if type(node).__name__ == "CIMSolidFill"]
        node_types[tag] = {"renderer": type(renderer).__name__,
                           "count_CIMSolidFill": names.get("CIMSolidFill", 0),
                           "count_CIMSolidStroke": names.get("CIMSolidStroke", 0),
                           "count_CIMVectorMarker": names.get("CIMVectorMarker", 0),
                           "other_node_types": sorted(key for key in names
                                                      if key not in ("CIMSolidFill", "CIMSolidStroke",
                                                                    "CIMVectorMarker"))[:8]}
        fill_evidence[tag] = [color_values_of(getattr(node, "color", None)) for node in fills]
    # 假设 1：真机反序列化的填充层 Python 类名就是 CIMSolidFill（paint_symbol 的分派依据）
    checks["runtime_fill_class_name_is_cimsolidfill"] = (
        all(node_types[tag]["count_CIMSolidFill"] > 0 for tag in node_types)
        and node_types["classbreaks"]["renderer"] == "CIMClassBreaksRenderer"
        and node_types["uniquevalue"]["renderer"] == "CIMUniqueValueRenderer")
    # 类名对上了还不够：读回来的填充色必须真的等于色板色，否则同样是"落盘但渲错"
    measures["runtime_fill_class"] = {"node_types": node_types,
                                      "class_breaks_fill_values": fill_evidence["classbreaks"],
                                      "unique_value_fill_values": fill_evidence["uniquevalue"][:5],
                                      "simple_polygon_fill_values": fill_evidence["simplepoly"]}
    checks["runtime_fill_colors_match_the_palette"] = (expected_values(BLUES5[0]) in fill_evidence["classbreaks"]
                                                      and expected_values(symbology.BASE["fill"])
                                                      in fill_evidence["simplepoly"])

    # 判别字段对照：本机 3.5.2 的图层定义对象根本没有 .JSON 属性（run2 实测 AttributeError），
    # 所以 paint_symbol 的分派依据只有 Python 类名一种，上面用真机读回的类名直接证明。
    definition = readback_layer(cb, "classbreaks_json")
    measures["json_discriminator"] = {
        "python_class_name": type(definition.renderer).__name__,
        "definition_has_json_attribute": hasattr(definition, "JSON"),
        "note": "探测 dump 里的 __type__ 就是 type(obj).__name__，不是 CIM 的 JSON 判别字段；"
                "分派依据只能是 Python 类名，本项以持久化往返之后的类名为证。"}

    # 假设 2：栅格 colorizer 的真机属性名与段数（weights 是否需要由 raster_elevation_ramp 的命中证明）
    definition = readback_layer(ra, "raster")
    colorizer = getattr(definition, "colorizer", None)
    ramp = getattr(colorizer, "colorRamp", None) if colorizer is not None else None
    segments = list(getattr(ramp, "colorRamps", []) or []) if ramp is not None else []
    stops = []
    for segment in segments:
        stops.append(color_values_of(getattr(segment, "fromColor", None)))
        stops.append(color_values_of(getattr(segment, "toColor", None)))
    weights_value = getattr(ramp, "weights", None) if ramp is not None else None
    ramp_info = {"colorizer_type": type(colorizer).__name__ if colorizer is not None else None,
                 "colorizer_keys": sorted(vars(colorizer).keys()) if colorizer is not None else [],
                 "ramp_type": type(ramp).__name__ if ramp is not None else None,
                 "ramp_keys": sorted(vars(ramp).keys()) if ramp is not None else [],
                 "segment_types": sorted({type(segment).__name__ for segment in segments}),
                 "segment_keys": sorted(vars(segments[0]).keys()) if segments else [],
                 "segment_count": len(segments), "stop_values": stops,
                 "weights": list(weights_value) if isinstance(weights_value, (list, tuple)) else weights_value,
                 "renderer_is_none": getattr(definition, "renderer", None) is None}
    measures["raster_ramp_round_trip"] = ramp_info
    checks["raster_ramp_property_spellings_live"] = (
        ramp_info["ramp_type"] == "CIMMultipartColorRamp" and ramp_info["segment_count"] == 6
        and ramp_info["segment_types"] == ["CIMLinearContinuousColorRamp"]
        and stops[:2] == [expected_values(ELEV7[0]), expected_values(ELEV7[1])]
        and stops[-1] == expected_values(ELEV7[6]) and ramp_info["renderer_is_none"] is True)
    checks["raster_ramp_needs_no_weights"] = (all(value > 0 for value in raster_hits.values())
                                             and ramp_info["segment_count"] == 6)

    # 假设 3：标注容器重赋值之后真机导出出不出字（labels_render 是像素证据，这里是结构证据）
    definition = readback_layer(lb, "labeled")
    classes = list(definition.labelClasses or [])
    klass = classes[0] if classes else None
    glyph_fills = [color_values_of(getattr(node, "color", None))
                   for node in walk_cim(getattr(getattr(klass, "textSymbol", None), "symbol", None))
                   if type(node).__name__ == "CIMSolidFill"] if klass is not None else []
    glyph_symbol = getattr(getattr(klass, "textSymbol", None), "symbol", None) if klass is not None else None
    label_info = {"label_class_count": len(classes), "label_visibility": getattr(definition, "labelVisibility", None),
                  "expression": getattr(klass, "expression", None) if klass is not None else None,
                  "expression_engine": getattr(klass, "expressionEngine", None) if klass is not None else None,
                  "visibility": getattr(klass, "visibility", None) if klass is not None else None,
                  # 本机 3.5.2 反读到的 CIMTextSymbol 只有 height，没有 fontSize（读它直接
                  # AttributeError），所以 apply_label 里的 fontSize 赋值是挂不上盘的悬浮属性，
                  # 真正生效的是 height —— 记下来，别让它以后被当成"字号没生效"。
                  "font_size_attribute": "absent" if not _has(glyph_symbol, "fontSize") else "present",
                  "height": getattr(glyph_symbol, "height", None),
                  "glyph_fill_values": glyph_fills}
    measures["label_round_trip"] = label_info
    checks["label_container_round_trip_renders"] = (label_info["label_class_count"] == 1
                                                   and label_info["label_visibility"] is True
                                                   and label_info["visibility"] is True
                                                   and label_info["expression"] == "$feature.NAME"
                                                   and black_growth > 800)
    # 字号 18 真的落到了持久化后的符号上（走 height，不是 fontSize）
    checks["label_size_lands_on_the_round_tripped_symbol"] = (label_info["height"] == 18
                                                              and label_info["font_size_attribute"] == "absent")
    # 字形颜色确实写进了 textSymbol 的 CIMSolidFill（黑值本身不足以说明是我们设的，
    # 那由下面的 label_color_follows_the_constant 换色实验证明）。
    checks["label_color_is_custom"] = (bool(glyph_fills)
                                       and all(values == expected_values(symbology.LABEL_COLOR)
                                               for values in glyph_fills if values))

    # 字色真的由 LABEL_COLOR 决定：进程内换一个颜色再走 apply_label，证明它不是运行时默认值碰巧撞对
    original_label_color = symbology.LABEL_COLOR
    swapped_fills = []
    symbology.LABEL_COLOR = "#C00000"
    try:
        swapped_definition = readback_layer(lb, "labeled_red")
        symbology.apply_label(arcpy, swapped_definition, {"field": "NAME", "size": 14})
        swapped_klass = list(swapped_definition.labelClasses)[0]
        swapped_fills = [color_values_of(getattr(node, "color", None))
                         for node in walk_cim(swapped_klass.textSymbol.symbol)
                         if type(node).__name__ == "CIMSolidFill"]
    finally:
        symbology.LABEL_COLOR = original_label_color
    measures["label_color_swapped"] = swapped_fills
    checks["label_color_follows_the_constant"] = expected_values("#C00000") in (swapped_fills or [])

    # ---------------- 12. 人眼验收图（冒烟不替代这一步）----------------
    def review_sheet(number, title, layers, styles, labels=None, dpi=200):
        tag = folder_for[number]
        item = publish(tag, title, layers, styles=styles, labels=labels, dpi=dpi, count=False)
        if not item["ok"]:
            return {"ok": False, "error": item["build_error"] or item["export_error"]}
        target = review / f"{number}.png"
        shutil.copy2(item["png"], target)
        return {"ok": True, "path": str(target), "ink": item["ink"], "elements": item["data"].get("elements"),
                "symbology": item["data"].get("symbology"), "labels": item["data"].get("labels")}

    measures["review"] = {}
    measures["review"]["01_thematic_class_breaks"] = review_sheet(
        "01_thematic_class_breaks", "湖南省示范分级专题图", [poly],
        [{"layer": poly, "palette": "BLUES", "field": "VALUE", "classes": 5}])
    measures["review"]["02_land_use_unique_value"] = review_sheet(
        "02_land_use_unique_value", "土地利用现状分类图", [land],
        [{"layer": land, "palette": "LAND_USE", "field": "LU_TYPE"}])
    if hillshade_ok:
        measures["review"]["03_terrain_hillshade_over_tint"] = review_sheet(
            "03_terrain_hillshade_over_tint", "地形晕渲叠分层设色", [dem, shade],
            [{"layer": dem, "palette": "ELEVATION"}, {"layer": shade, "palette": "HILLSHADE_GRAY"}])
    else:
        measures["review"]["03_terrain_hillshade_over_tint"] = {"ok": False, "error": "晕渲栅格不可用，见 review/hillshade-skipped.txt"}
    measures["review"]["03b_terrain_elevation_only"] = review_sheet(
        "03b_terrain_elevation_only", "分层设色（未叠晕渲）", [dem], [{"layer": dem, "palette": "ELEVATION"}])
    measures["review"]["04_standards_deliverable_with_labels"] = review_sheet(
        "04_standards_deliverable_with_labels", "规范成果图（含注记）", [poly],
        [{"layer": poly, "palette": "BLUES", "field": "VALUE", "classes": 5}],
        labels=[{"layer": poly, "field": "NAME", "size": 12}], dpi=300)

    checks["review_maps_exported"] = sum(1 for item in measures["review"].values() if item.get("ok")) >= 4
    checks["refusals_all_reported"] = len(runtime.refusals) >= 9

    # 收尾再核一次源数据：拒绝路径、真机读回、成果图导出都发生在第 8 节之后，
    # 它们同样只该读 fixture。这里只记实测差集，不再新增一条断言（判据与第 8 节同一条）。
    final_state = {"gdb": tree_manifest(gdb), "root": tree_manifest(root, top_level_only=True),
                   "counts": {Path(item).name: int(arcpy.management.GetCount(item)[0])
                              for item in (poly, many, line, point, land, fishnet)}}
    measures["source_state_at_exit"] = {"gdb_drift": manifest_drift(source_state["gdb"], final_state["gdb"]),
                                        "root_drift": manifest_drift(source_state["root"], final_state["root"]),
                                        "counts_match": final_state["counts"] == source_state["counts"]}

    print(json.dumps({"ok": all(checks.values()), "checks": checks, "measures": measures,
                      "refusals": runtime.refusals, "hillshade_available": hillshade_ok,
                      "elapsed_seconds": round(time.time() - started, 1),
                      "version": arcpy.GetInstallInfo()["Version"], "run_dir": str(root)},
                     ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
