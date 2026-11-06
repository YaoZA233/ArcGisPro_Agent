# 出图能力增强：符号化与标注 · 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `build_layout` 通过预设色板驱动 CIM renderer 与 labelClass，使 agent 产出的图不再是 Pro 默认随机填色，并能带字段标注。

**Architecture:** 新增 `src/arcgis_agent/symbology.py` 承担色板表、断点计算与 CIM 构造；`cartography.py` 只在图层循环里插入 `getDefinition → symbology → setDefinition`。不新增任何 LLM 可见工具，不新增写入路径，`export_layout` 的只读语义与"绝不修改用户工程"不变量一字不改。

**Tech Stack:** Python 3.11（ArcGIS Pro 自带）、`arcpy.mp` / `arcpy.cim`、Pillow（仅冒烟脚本用于像素统计）、unittest。

**Spec:** `docs/superpowers/specs/2026-11-06-cartography-symbology-design.md`（§8.1 已记录 CIM 真机探测的全部实测结论与偏差表，实现前必读）

## Global Constraints

- 目标运行时：Windows + ArcGIS Pro 3.5.2，Pro Python 3.11（`E:\programs\ArcGIS\bin\Python\envs\arcgispro-py3\python.exe`）。核心模块必须在**不 import arcpy** 的情况下可被系统 Python 导入。
- 禁止往 Esri 的 base Python 环境安装任何包。不得引入 numpy/scipy；断点计算只用标准库（`math`、`statistics` 也不用，手写）。
- 禁止 eval/exec、禁止生成 Python 代码、禁止 shell 执行工具。
- 工具写入必须落在唯一任务目录下，绝不覆盖既有数据集。`len(TOOLS)` 必须保持 **62**（`tests/test_approvals.py:211`）。
- 所有可能被拒绝的校验必须排在 `shutil.copy2(template, staged)` **之前**，使被拒调用不残留半成品 `.aprx`、连 `cartography/` 目录都不创建。
- 错误消息一律中文，指明具体参数并给出可用替代值，风格照 `cartography.py` 的 `_require_degree_box` / `_sr_advice`。
- 离线测试用 `unittest`（不是 pytest）。`./scripts/test.ps1` 是 PowerShell 脚本，**在 Git Bash 里直接执行会报 `=: command not found`**，要用 `powershell -File scripts/test.ps1`，或直接跑它内部等价的命令：`PYTHONPATH=src python -m unittest discover -s tests`。单个测试类用 `PYTHONPATH=src python -m unittest tests.test_symbology.<类名> -v`。
- 离线基线（2026-11-06 实测）：**328 项全绿，7.1s**。每个任务的"全量套件"步骤都以此为起点判断有无回归。
- 测试**绝不 import arcpy、绝不 monkeypatch `sys.modules`**：生产代码把 `arcpy` 作为参数传入，测试传手写桩对象。
- 真机冒烟只用合成数据，写在 `workspace/smoke/<uuid>` 下，绝不用用户数据集造 fixture。
- **CIM 三条铁律**（违反任一条会导致"定义落盘成功但渲染为空/为灰"，且不会报错）：
  1. 颜色必须写 `CIMRGBColor().values = [r, g, b, alpha]`，alpha 取 0–100。`red=`/`green=`/`blue=` 关键字被静默忽略。
  2. 符号必须 `copy.deepcopy(运行时生成的默认 renderer.symbol)` 后改色。**从零构造 `CIMPolygonSymbol` 会落盘但完全不渲染。**
  3. 栅格符号化改 `definition.colorizer`（默认即 `CIMRasterStretchColorizer`），`CIMRasterLayer.renderer` 恒为 `None`。
- 容器属性真名：分级色彩是 `CIMClassBreaksRenderer.breaks`（**不是** `classBreaks`）；唯一值是 `CIMUniqueValueRenderer.groups` → `CIMUniqueValueGroup.classes`（**不是** 直接 `classes`）。
- 标注必须改**已存在的** `definition.labelClasses[0]`，表达式用 Arcade 形式 `$feature.<字段>`；整个替换为新建 `CIMLabelClass` 会落盘成功但不渲染。
- 提交策略：用户明确表示设计文档不需要提交。实现阶段每个任务末尾列出了提交步骤，但**执行前需先征得用户同意**；未获同意就跳过提交步骤，不要自作主张。

---

### Task 1: 色板常量表与完整性契约

**Files:**
- Create: `src/arcgis_agent/symbology.py`
- Test: `tests/test_symbology.py`

**Interfaces:**
- Consumes: 无（本任务是根）
- Produces:
  - `PALETTES: dict[str, dict]` —— 键为色板名，值含 `kind`（`"SEQUENTIAL"`/`"QUALITATIVE"`/`"TERRAIN"`/`"BASE"`）与按 kind 不同的色值字段
  - `PALETTE_NAMES: list[str]` —— `sorted(PALETTES)`，供 `catalog.py` 做 enum
  - `KINDS = ("SEQUENTIAL", "QUALITATIVE", "TERRAIN", "BASE")`
  - `CLASSIFY_METHODS = ("EQUAL_INTERVAL", "QUANTILE")`
  - `MIN_CLASSES = 3`、`MAX_CLASSES = 7`、`MAX_UNIQUE_VALUES = 10`、`VALUE_CAP = 200000`
  - `OUTLINES = {"CLASS_BREAKS": ("#FFFFFF", 0.2), "UNIQUE_VALUE": ("#666666", 0.4)}`
  - `hex_rgb(value: str) -> tuple[int, int, int]`
  - `palette_colors(name: str, classes: int) -> tuple[str, ...]`
  - `ramp_colors(name: str) -> tuple[str, ...]`（恒返回 7 色，供栅格色带）
  - `land_use_color(value: str) -> tuple[str, bool]`（返回 `(色值, 是否命中语义)`）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_symbology.py`：

```python
"""符号化模块的离线测试。绝不 import arcpy：桩对象按参数传入。"""
from __future__ import annotations

import unittest

from arcgis_agent.symbology import (CLASSIFY_METHODS, KINDS, MAX_CLASSES, MAX_UNIQUE_VALUES,
                                    MIN_CLASSES, OUTLINES, PALETTE_NAMES, PALETTES, hex_rgb,
                                    land_use_color, palette_colors, ramp_colors)

HEX = "#0123456789abcdefABCDEF"


class PaletteTableTests(unittest.TestCase):
    def test_names_are_sorted_and_match_table(self):
        self.assertEqual(PALETTES, sorted(PALETTES))
        self.assertEqual(PALETTE_NAMES, sorted(PALETTES))
        self.assertEqual(len(PALETTE_NAMES), 9)

    def test_every_palette_declares_a_known_kind(self):
        for name, entry in PALETTES.items():
            self.assertIn(entry["kind"], KINDS, name)

    def test_sequential_palettes_have_three_five_and_seven_stops(self):
        sequential = [n for n, e in PALETTES.items() if e["kind"] == "SEQUENTIAL"]
        self.assertEqual(sorted(sequential), ["BLUES", "GREENS", "RD_BU", "REDS"])
        for name in sequential:
            steps = PALETTES[name]["steps"]
            self.assertEqual(sorted(steps), [3, 5, 7], name)
            for count, colors in steps.items():
                self.assertEqual(len(colors), count, f"{name}/{count}")

    def test_terrain_palettes_only_carry_the_seven_stop_ramp(self):
        terrain = [n for n, e in PALETTES.items() if e["kind"] == "TERRAIN"]
        self.assertEqual(sorted(terrain), ["ELEVATION", "HILLSHADE_GRAY"])
        for name in terrain:
            self.assertEqual(sorted(PALETTES[name]["steps"]), [7], name)

    def test_every_color_is_a_valid_hex_triplet(self):
        def check(colors, where):
            for color in colors:
                self.assertEqual(len(color), 7, f"{where}: {color}")
                self.assertEqual(color[0], "#", f"{where}: {color}")
                for char in color[1:]:
                    self.assertIn(char, HEX, f"{where}: {color}")
                red, green, blue = hex_rgb(color)
                for channel in (red, green, blue):
                    self.assertTrue(0 <= channel <= 255, f"{where}: {color}")

        for name, entry in PALETTES.items():
            if "steps" in entry:
                for count, colors in entry["steps"].items():
                    check(colors, f"{name}/{count}")
            if "colors" in entry:
                check(entry["colors"], name)
            if "semantic" in entry:
                check([entry["fallback"]], f"{name}/fallback")
                for _, color in entry["semantic"]:
                    check([color], f"{name}/semantic")
            if entry["kind"] == "BASE":
                check([entry["fill"], entry["outline"], entry["line"], entry["point"]], name)

    def test_qualitative_palettes_have_the_exact_expected_cardinality(self):
        self.assertEqual(len(PALETTES["CATEGORY10"]["colors"]), MAX_UNIQUE_VALUES)
        self.assertEqual(len(PALETTES["LAND_USE"]["semantic"]), 8)

    def test_outline_presets_cover_both_classed_renderers(self):
        self.assertEqual(sorted(OUTLINES), ["CLASS_BREAKS", "UNIQUE_VALUE"])
        for _, (color, width) in OUTLINES.items():
            self.assertEqual(len(hex_rgb(color)), 3)
            self.assertGreater(width, 0)

    def test_class_range_and_methods_are_the_approved_ones(self):
        self.assertEqual((MIN_CLASSES, MAX_CLASSES), (3, 7))
        self.assertEqual(CLASSIFY_METHODS, ("EQUAL_INTERVAL", "QUANTILE"))


class ColorHelperTests(unittest.TestCase):
    def test_palette_colors_returns_the_requested_step_count(self):
        self.assertEqual(len(palette_colors("BLUES", 3)), 3)
        self.assertEqual(len(palette_colors("BLUES", 5)), 5)
        self.assertEqual(palette_colors("BLUES", 5)[-1], "#08519c")

    def test_palette_colors_rejects_out_of_range_class_counts(self):
        for bad in (0, 1, 2, 8, 12, -1):
            with self.assertRaises(ValueError):
                palette_colors("BLUES", bad)

    def test_palette_colors_rejects_a_palette_without_steps(self):
        with self.assertRaises(ValueError):
            palette_colors("CATEGORY10", 5)

    def test_ramp_colors_always_returns_seven(self):
        for name in ("BLUES", "GREENS", "REDS", "RD_BU", "ELEVATION", "HILLSHADE_GRAY"):
            self.assertEqual(len(ramp_colors(name)), 7, name)

    def test_ramp_colors_rejects_palettes_without_a_ramp(self):
        for name in ("CATEGORY10", "LAND_USE", "BASE"):
            with self.assertRaises(ValueError):
                ramp_colors(name)


class LandUseMatchingTests(unittest.TestCase):
    def test_each_semantic_class_matches_its_own_keywords(self):
        expected = {"水体": "#3B7DBF", "林地": "#2E7D32", "草地": "#9CC47A", "耕地": "#E3C777",
                    "建成区": "#B85C4E", "裸地": "#A6927B", "湿地": "#6E9BA8", "园地": "#7FA85C"}
        for label, color in expected.items():
            matched, hit = land_use_color(label)
            self.assertEqual(matched, color, label)
            self.assertTrue(hit, label)

    def test_ambiguous_land_cover_terms_resolve_to_the_right_class(self):
        # 「水浇地」含「水」但属耕地；「湿地」含「水」但属湿地；「沼泽地」属湿地。
        # 关键词组顺序必须让更具体的组先匹配，否则会静默错配成水体。
        self.assertEqual(land_use_color("水浇地")[0], "#E3C777")
        self.assertEqual(land_use_color("水田")[0], "#E3C777")
        self.assertEqual(land_use_color("湿地")[0], "#6E9BA8")
        self.assertEqual(land_use_color("沼泽地")[0], "#6E9BA8")
        self.assertEqual(land_use_color("水域")[0], "#3B7DBF")
        self.assertEqual(land_use_color("果园")[0], "#7FA85C")

    def test_english_keywords_match_case_insensitively(self):
        self.assertEqual(land_use_color("WATER")[0], "#3B7DBF")
        self.assertEqual(land_use_color("Forest")[0], "#2E7D32")
        self.assertEqual(land_use_color("urban area")[0], "#B85C4E")

    def test_unmatched_values_fall_back_and_report_false(self):
        color, hit = land_use_color("其他")
        self.assertEqual(color, PALETTES["LAND_USE"]["fallback"])
        self.assertFalse(hit)
        self.assertFalse(land_use_color("")[1])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `PYTHONPATH=src python -m unittest tests.test_symbology -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'arcgis_agent.symbology'`

- [ ] **Step 3: 写实现**

创建 `src/arcgis_agent/symbology.py`：

```python
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

import copy
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `PYTHONPATH=src python -m unittest tests.test_symbology -v`
Expected: PASS，全部 15 项 OK

- [ ] **Step 5: 确认核心仍可在无 arcpy 环境导入**

Run: `PYTHONPATH=src python -c "import arcgis_agent.symbology as s; print(len(s.PALETTE_NAMES))"`
Expected: 输出 `9`，无 ImportError

- [ ] **Step 6: 提交（需先征得用户同意）**

```bash
git add src/arcgis_agent/symbology.py tests/test_symbology.py
git commit -m "feat: 出图色板常量表与语义色匹配"
```

---

### Task 2: 断点与唯一值计算

**Files:**
- Modify: `src/arcgis_agent/symbology.py`（追加函数）
- Test: `tests/test_symbology.py`（追加测试类）

**Interfaces:**
- Consumes: Task 1 的 `MAX_CLASSES`、`MIN_CLASSES`、`MAX_UNIQUE_VALUES`、`VALUE_CAP`、`CLASSIFY_METHODS`
- Produces:
  - `build_classes(values: list[float], classes: int, method: str) -> dict` —— 返回 `{"minimum": float, "maximum": float, "bounds": list[float]}`，`bounds` 长度恒等于 `classes` 且严格覆盖到 `maximum`
  - `collect_values(arcpy, dataset: str, field: str, cap: int = VALUE_CAP) -> tuple[list[float], bool, int]` —— 返回 `(值列表, 是否抽样, 要素总数)`
  - `collect_categories(arcpy, dataset: str, field: str, limit: int = MAX_UNIQUE_VALUES) -> tuple[list[str], bool]` —— 返回 `(按字典序排序的唯一值, 是否超限)`

- [ ] **Step 1: 写失败测试**

在 `tests/test_symbology.py` 末尾（`if __name__` 之前）追加：

```python
from arcgis_agent.symbology import VALUE_CAP, build_classes, collect_categories, collect_values

# 9 个面，VALUE = i*13+5 → 5,18,31,44,57,70,83,96,109
NINE = [5.0, 18.0, 31.0, 44.0, 57.0, 70.0, 83.0, 96.0, 109.0]


class BuildClassesTests(unittest.TestCase):
    def test_equal_interval_splits_the_observed_range(self):
        result = build_classes(NINE, 5, "EQUAL_INTERVAL")
        self.assertEqual(result["minimum"], 5.0)
        self.assertEqual(result["maximum"], 109.0)
        self.assertEqual(len(result["bounds"]), 5)
        for expected, actual in zip((25.8, 46.6, 67.4, 88.2, 109.0), result["bounds"]):
            self.assertAlmostEqual(expected, actual, places=6)

    def test_equal_interval_last_bound_is_exactly_the_maximum(self):
        # 浮点累加可能差最后一个小数位；末级必须精确覆盖最大值，否则最大要素会掉出分级。
        result = build_classes([0.1, 0.2, 0.3], 3, "EQUAL_INTERVAL")
        self.assertEqual(result["bounds"][-1], result["maximum"])

    def test_quantile_bounds_are_order_statistics(self):
        result = build_classes(NINE, 5, "QUANTILE")
        self.assertEqual(result["bounds"], [18.0, 44.0, 70.0, 96.0, 109.0])

    def test_quantile_bounds_never_decrease(self):
        for count in (3, 5, 7):
            bounds = build_classes(NINE, count, "QUANTILE")["bounds"]
            self.assertEqual(bounds, sorted(bounds), count)
            self.assertEqual(len(bounds), count)

    def test_quantile_on_skewed_data_stays_monotonic(self):
        skewed = [1.0] * 20 + [2.0, 3.0, 500.0]
        bounds = build_classes(skewed, 5, "QUANTILE")["bounds"]
        self.assertEqual(bounds, sorted(bounds))
        self.assertEqual(bounds[-1], 500.0)

    def test_negative_and_mixed_sign_values(self):
        result = build_classes([-30.0, -10.0, 0.0, 10.0, 30.0], 5, "EQUAL_INTERVAL")
        self.assertEqual(result["minimum"], -30.0)
        self.assertAlmostEqual(result["bounds"][0], -18.0, places=6)
        self.assertEqual(result["bounds"][-1], 30.0)

    def test_extreme_outlier_does_not_break_the_bounds(self):
        result = build_classes([1.0, 2.0, 3.0, 1e9], 4, "EQUAL_INTERVAL")
        self.assertEqual(result["bounds"][-1], 1e9)
        self.assertEqual(len(result["bounds"]), 4)

    def test_minimum_class_count_still_works(self):
        self.assertEqual(len(build_classes(NINE, MIN_CLASSES, "EQUAL_INTERVAL")["bounds"]), 3)

    def test_empty_values_are_refused(self):
        with self.assertRaises(ValueError) as caught:
            build_classes([], 5, "EQUAL_INTERVAL")
        self.assertIn("没有有效数值", str(caught.exception))

    def test_all_identical_values_are_refused(self):
        # 全部相同 → 断点退化，会渲出一张看似正常但语义错误的单色图且不报错。
        with self.assertRaises(ValueError) as caught:
            build_classes([7.0] * 12, 5, "EQUAL_INTERVAL")
        self.assertIn("全部相同", str(caught.exception))

    def test_fewer_values_than_classes_still_produces_that_many_bounds(self):
        result = build_classes([1.0, 9.0], 5, "QUANTILE")
        self.assertEqual(len(result["bounds"]), 5)
        self.assertEqual(result["bounds"][-1], 9.0)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return iter(self.rows)

    def __exit__(self, *exc):
        return False


class FakeArcpy:
    """只桩 symbology 真正调用的那一小截 arcpy 表面。"""

    def __init__(self, rows, total=None):
        self.rows = rows
        self.total = len(rows) if total is None else total
        self.cursor_calls = []

        class _da:
            def SearchCursor(inner_self, dataset, fields):
                self.cursor_calls.append((dataset, tuple(fields)))
                return FakeCursor(self.rows)

        class _management:
            def GetCount(inner_self, dataset):
                return [str(self.total)]

        self.da = _da()
        self.management = _management()


class CollectValuesTests(unittest.TestCase):
    def test_reads_every_value_below_the_cap(self):
        fake = FakeArcpy([(v,) for v in NINE])
        values, sampled, total = collect_values(fake, "C:/d.gdb/zones", "VALUE", cap=1000)
        self.assertEqual(values, NINE)
        self.assertFalse(sampled)
        self.assertEqual(total, 9)
        self.assertEqual(fake.cursor_calls, [("C:/d.gdb/zones", ("VALUE",))])

    def test_nulls_are_dropped_not_counted_as_zero(self):
        fake = FakeArcpy([(1.0,), (None,), (3.0,)])
        values, _, _ = collect_values(fake, "d", "V", cap=1000)
        self.assertEqual(values, [1.0, 3.0])

    def test_sampling_kicks_in_above_the_cap_and_is_reported(self):
        rows = [(float(i),) for i in range(100)]
        fake = FakeArcpy(rows, total=100)
        values, sampled, total = collect_values(fake, "d", "V", cap=10)
        self.assertTrue(sampled)
        self.assertEqual(total, 100)
        self.assertLessEqual(len(values), 10)
        self.assertEqual(values[0], 0.0)

    def test_default_cap_is_the_documented_one(self):
        fake = FakeArcpy([(1.0,)])
        collect_values(fake, "d", "V")
        self.assertEqual(VALUE_CAP, 200000)


class CollectCategoriesTests(unittest.TestCase):
    def test_returns_sorted_distinct_values(self):
        rows = [("林地",), ("水体",), ("林地",), ("耕地",)]
        fake = FakeArcpy(rows)
        categories, exceeded = collect_categories(fake, "d", "CATEGORY")
        self.assertEqual(categories, sorted(["林地", "水体", "耕地"]))
        self.assertFalse(exceeded)

    def test_blank_and_null_values_are_skipped(self):
        fake = FakeArcpy([(None,), ("",), ("  ",), ("水体",)])
        categories, exceeded = collect_categories(fake, "d", "C")
        self.assertEqual(categories, ["水体"])
        self.assertFalse(exceeded)

    def test_exceeding_the_limit_is_reported_with_the_offending_count(self):
        rows = [(f"类{i}",) for i in range(MAX_UNIQUE_VALUES + 3)]
        fake = FakeArcpy(rows)
        categories, exceeded = collect_categories(fake, "d", "C")
        self.assertTrue(exceeded)
        self.assertEqual(len(categories), MAX_UNIQUE_VALUES + 1)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `PYTHONPATH=src python -m unittest tests.test_symbology -v`
Expected: FAIL，`ImportError: cannot import name 'build_classes'`

- [ ] **Step 3: 写实现**

在 `symbology.py` 末尾追加：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `PYTHONPATH=src python -m unittest tests.test_symbology -v`
Expected: PASS，累计约 33 项 OK

- [ ] **Step 5: 提交（需先征得用户同意）**

```bash
git add src/arcgis_agent/symbology.py tests/test_symbology.py
git commit -m "feat: 分级断点与唯一值收集"
```

---

### Task 3: 样式与标注校验

**Files:**
- Modify: `src/arcgis_agent/symbology.py`
- Test: `tests/test_symbology.py`

**Interfaces:**
- Consumes: `PALETTES`、`PALETTE_NAMES`、`NUMERIC_FIELD_TYPES`、`TEXT_FIELD_TYPES`、`RASTER_TYPES`（从 `.analysis_tools` 导入，不重复定义）
- Produces:
  - `RENDERERS = ("CLASS_BREAKS", "UNIQUE_VALUE", "SIMPLE", "RASTER_RAMP")`
  - `renderer_kind(name: str, desc) -> str` —— 由色板 kind × 数据类型/几何类型推断，返回上述四者之一或抛 `ValueError`
  - `validate_style_spec(style: dict, desc) -> str` —— 校验一条 style，返回 renderer kind
  - `validate_label_spec(label: dict, desc) -> None`
  - `field_types(desc) -> dict[str, str]` —— 从 `desc.fields` 提取 `{字段名: 类型}`

`desc` 是 `arcpy.Describe` 的结果，测试用桩对象提供 `dataType`、`shapeType`、`fields`（元素有 `.name` 与 `.type`）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_symbology.py`：

```python
from arcgis_agent.symbology import RENDERERS, renderer_kind, validate_label_spec, validate_style_spec


class FakeField:
    def __init__(self, name, kind):
        self.name = name
        self.type = kind


class FakeDesc:
    def __init__(self, data_type, shape_type=None, fields=()):
        self.dataType = data_type
        self.shapeType = shape_type
        self.fields = [FakeField(*item) if isinstance(item, tuple) else item for item in fields]


POLY_FIELDS = (("OBJECTID", "OID"), ("SHAPE", "Geometry"), ("VALUE", "Double"),
               ("CATEGORY", "String"), ("NAME", "String"), ("COUNT", "Integer"))


def polygon_desc():
    return FakeDesc("FeatureClass", "Polygon", POLY_FIELDS)


def line_desc():
    return FakeDesc("FeatureClass", "Polyline", POLY_FIELDS)


def point_desc():
    return FakeDesc("FeatureClass", "Point", POLY_FIELDS)


def raster_desc():
    return FakeDesc("RasterDataset", None, ())


class RendererKindTests(unittest.TestCase):
    def test_sequential_on_vector_is_class_breaks(self):
        for name in ("BLUES", "GREENS", "REDS", "RD_BU"):
            self.assertEqual(renderer_kind(name, polygon_desc()), "CLASS_BREAKS", name)

    def test_sequential_on_raster_is_a_ramp(self):
        self.assertEqual(renderer_kind("BLUES", raster_desc()), "RASTER_RAMP")

    def test_qualitative_on_vector_is_unique_value(self):
        self.assertEqual(renderer_kind("CATEGORY10", polygon_desc()), "UNIQUE_VALUE")
        self.assertEqual(renderer_kind("LAND_USE", point_desc()), "UNIQUE_VALUE")

    def test_qualitative_on_raster_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            renderer_kind("CATEGORY10", raster_desc())
        self.assertIn("栅格", str(caught.exception))

    def test_terrain_on_raster_is_a_ramp(self):
        self.assertEqual(renderer_kind("ELEVATION", raster_desc()), "RASTER_RAMP")
        self.assertEqual(renderer_kind("HILLSHADE_GRAY", raster_desc()), "RASTER_RAMP")

    def test_terrain_on_vector_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            renderer_kind("ELEVATION", polygon_desc())
        self.assertIn("栅格", str(caught.exception))

    def test_base_follows_the_geometry(self):
        for desc in (polygon_desc(), line_desc(), point_desc()):
            self.assertEqual(renderer_kind("BASE", desc), "SIMPLE")

    def test_base_on_raster_is_refused(self):
        with self.assertRaises(ValueError):
            renderer_kind("BASE", raster_desc())

    def test_unknown_palette_is_refused_with_the_available_list(self):
        with self.assertRaises(ValueError) as caught:
            renderer_kind("RAINBOW", polygon_desc())
        self.assertIn("BLUES", str(caught.exception))

    def test_tables_are_refused_because_they_cannot_render(self):
        with self.assertRaises(ValueError):
            renderer_kind("BLUES", FakeDesc("Table", None, POLY_FIELDS))

    def test_every_kind_is_declared(self):
        self.assertEqual(RENDERERS, ("CLASS_BREAKS", "UNIQUE_VALUE", "SIMPLE", "RASTER_RAMP"))


class ValidateStyleSpecTests(unittest.TestCase):
    def test_class_breaks_requires_a_numeric_field(self):
        validate_style_spec({"layer": "L", "palette": "BLUES", "field": "VALUE",
                             "classes": 5, "method": "EQUAL_INTERVAL"}, polygon_desc())

    def test_class_breaks_refuses_a_text_field_and_lists_numeric_ones(self):
        with self.assertRaises(ValueError) as caught:
            validate_style_spec({"layer": "L", "palette": "BLUES", "field": "CATEGORY"},
                                polygon_desc())
        message = str(caught.exception)
        self.assertIn("VALUE", message)
        self.assertIn("数值", message)

    def test_missing_field_lists_every_field_name(self):
        with self.assertRaises(ValueError) as caught:
            validate_style_spec({"layer": "L", "palette": "BLUES", "field": "NOPE"}, polygon_desc())
        self.assertIn("CATEGORY", str(caught.exception))

    def test_class_breaks_requires_the_field_argument(self):
        with self.assertRaises(ValueError) as caught:
            validate_style_spec({"layer": "L", "palette": "BLUES"}, polygon_desc())
        self.assertIn("field", str(caught.exception))

    def test_unique_value_requires_a_field(self):
        with self.assertRaises(ValueError):
            validate_style_spec({"layer": "L", "palette": "CATEGORY10"}, polygon_desc())

    def test_unique_value_accepts_text_and_coded_fields(self):
        validate_style_spec({"layer": "L", "palette": "CATEGORY10", "field": "CATEGORY"},
                            polygon_desc())

    def test_unique_value_refuses_a_continuous_float_field(self):
        # 连续浮点做唯一值几乎必然超出 10 类上限，且图例不可读。
        with self.assertRaises(ValueError) as caught:
            validate_style_spec({"layer": "L", "palette": "CATEGORY10", "field": "VALUE"},
                                polygon_desc())
        self.assertIn("分级", str(caught.exception))

    def test_unique_value_refuses_classes_and_method(self):
        # 类别数由数据决定、分级方法对它无意义；静默忽略会让模型以为参数生效了。
        for extra in ({"classes": 5}, {"method": "QUANTILE"}):
            with self.assertRaises(ValueError) as caught:
                validate_style_spec(dict({"layer": "L", "palette": "CATEGORY10",
                                          "field": "CATEGORY"}, **extra), polygon_desc())
            self.assertIn("不接受", str(caught.exception))

    def test_base_needs_no_field_and_refuses_one(self):
        validate_style_spec({"layer": "L", "palette": "BASE"}, polygon_desc())
        with self.assertRaises(ValueError) as caught:
            validate_style_spec({"layer": "L", "palette": "BASE", "field": "VALUE"}, polygon_desc())
        self.assertIn("不接受", str(caught.exception))

    def test_ramp_refuses_classes_and_method(self):
        with self.assertRaises(ValueError) as caught:
            validate_style_spec({"layer": "L", "palette": "ELEVATION", "classes": 5}, raster_desc())
        self.assertIn("不接受", str(caught.exception))

    def test_sequential_on_raster_refuses_a_field(self):
        with self.assertRaises(ValueError):
            validate_style_spec({"layer": "L", "palette": "BLUES", "field": "VALUE"}, raster_desc())

    def test_geometry_and_oid_fields_cannot_be_styled(self):
        for field in ("SHAPE", "OBJECTID"):
            with self.assertRaises(ValueError):
                validate_style_spec({"layer": "L", "palette": "BLUES", "field": field},
                                    polygon_desc())


class ValidateLabelSpecTests(unittest.TestCase):
    def test_text_and_numeric_fields_are_labelable(self):
        validate_label_spec({"layer": "L", "field": "NAME", "size": 9}, polygon_desc())
        validate_label_spec({"layer": "L", "field": "COUNT", "size": 12}, polygon_desc())

    def test_missing_field_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            validate_label_spec({"layer": "L", "field": "NOPE"}, polygon_desc())
        self.assertIn("NAME", str(caught.exception))

    def test_geometry_and_oid_fields_are_not_labelable(self):
        for field in ("SHAPE", "OBJECTID"):
            with self.assertRaises(ValueError):
                validate_label_spec({"layer": "L", "field": field}, polygon_desc())

    def test_raster_layers_cannot_be_labelled(self):
        with self.assertRaises(ValueError) as caught:
            validate_label_spec({"layer": "L", "field": "NAME"}, raster_desc())
        self.assertIn("栅格", str(caught.exception))

    def test_date_fields_are_refused_because_arcade_needs_formatting(self):
        desc = FakeDesc("FeatureClass", "Polygon", (("WHEN", "Date"),))
        with self.assertRaises(ValueError):
            validate_label_spec({"layer": "L", "field": "WHEN"}, desc)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `PYTHONPATH=src python -m unittest tests.test_symbology -v`
Expected: FAIL，`ImportError: cannot import name 'renderer_kind'`

- [ ] **Step 3: 写实现**

在 `symbology.py` 顶部 import 区加一行（`RASTER_TYPES` 已在 `analysis_tools.py:20` 定义，复用而非重复）：

```python
from .analysis_tools import RASTER_TYPES
```

在文件末尾追加：

```python
RENDERERS = ("CLASS_BREAKS", "UNIQUE_VALUE", "SIMPLE", "RASTER_RAMP")
UNLABELABLE = frozenset({"OID", "Geometry", "Blob", "Raster", "GUID", "GlobalID", "XML",
                         "Date", "DateOnly", "TimeOnly", "DateTimeOffset"})


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


def _check_field(style, desc, allowed, wanted):
    fields = field_types(desc)
    field = style.get("field")
    if not field:
        raise ValueError(f"色板 {style['palette']} 走{wanted}，必须提供 field 参数；"
                         f"可用字段：{', '.join(sorted(fields))}。")
    if field not in fields:
        raise ValueError(f"字段 {field} 不存在；该数据集的字段有：{', '.join(sorted(fields))}。")
    if fields[field] in UNLABELABLE:
        raise ValueError(f"字段 {field} 是 {fields[field]} 类型，不能用来{wanted}；"
                         f"可用字段：{', '.join(k for k, v in sorted(fields.items()) if v in allowed)}。")
    if fields[field] not in allowed:
        raise ValueError(f"字段 {field} 是 {fields[field]} 类型，不能用来{wanted}；"
                         f"该数据集的{wanted}可用字段有："
                         f"{', '.join(k for k, v in sorted(fields.items()) if v in allowed)}。")
    return field


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
            raise ValueError(f"色板 {style['palette']} 走{kind}，不接受 {name} 参数；"
                             f"请去掉它，或改用支持分级的色板（{', '.join(sorted(SEQUENTIAL))}）。")
    if kind in ("SIMPLE", "RASTER_RAMP"):
        return kind
    if kind == "CLASS_BREAKS":
        _check_field(style, desc, NUMERIC_FIELD_TYPES, "分级色彩")
        classes = style.get("classes", 5)
        if not MIN_CLASSES <= classes <= MAX_CLASSES:
            raise ValueError(f"classes 必须在 {MIN_CLASSES}–{MAX_CLASSES} 之间，收到 {classes}；"
                             f"7 级以上制图不可读。")
        method = style.get("method", "EQUAL_INTERVAL")
        if method not in CLASSIFY_METHODS:
            raise ValueError(f"method 只能是 {' 或 '.join(CLASSIFY_METHODS)}，收到 {method}。")
        return kind
    field = _check_field(style, desc, NUMERIC_FIELD_TYPES | TEXT_FIELD_TYPES, "唯一值渲染")
    if field_types(desc)[field] in NUMERIC_FIELD_TYPES and field_types(desc)[field] in ("Float", "Double"):
        raise ValueError(f"字段 {field} 是连续浮点数，做唯一值会产生大量类别且图例不可读；"
                         f"请改用分级色彩（{', '.join(sorted(SEQUENTIAL))}）配 field 与 classes。")
    return kind


def validate_label_spec(label, desc):
    if _is_raster(desc):
        raise ValueError("栅格图层没有属性字段，无法标注；如需注记请用 build_layout 的 title，"
                         "或先把栅格转成要素类。")
    _check_field(label, desc, NUMERIC_FIELD_TYPES | TEXT_FIELD_TYPES, "标注")
    size = label.get("size", 9)
    if not 6 <= size <= 24:
        raise ValueError(f"标注字号必须在 6–24 pt 之间，收到 {size}。")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `PYTHONPATH=src python -m unittest tests.test_symbology -v`
Expected: PASS，累计约 58 项 OK

- [ ] **Step 5: 跑全量离线套件确认没有回归**

Run: `PYTHONPATH=src python -m unittest discover -s tests`
Expected: 全绿（基线 328 项 + 本次新增），退出码 0

- [ ] **Step 6: 提交（需先征得用户同意）**

```bash
git add src/arcgis_agent/symbology.py tests/test_symbology.py
git commit -m "feat: 符号化与标注的前置校验"
```

---

### Task 4: catalog.py 的 schema 与跨参数校验

**Files:**
- Modify: `src/arcgis_agent/catalog.py:25-32`（枚举常量区）、`:161-169`（`build_layout` 注册）、`:321-328`（`validate_call` 的 `build_layout` 分支）
- Test: `tests/test_symbology.py`

**Interfaces:**
- Consumes: Task 1–3 的 `PALETTE_NAMES`、`CLASSIFY_METHODS`、`MIN_CLASSES`、`MAX_CLASSES`、`MAX_UNIQUE_VALUES`
- Produces: `build_layout` 的 `properties` 新增 `styles` 与 `labels`（均 optional）；`validate_call("build_layout", …)` 覆盖规格 §7 的第 1、2、8 条（跨条目校验；第 3–7、9 条依赖 `desc`，在 Task 8 的执行期前置阶段由 `validate_style_spec` / `validate_label_spec` 完成）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_symbology.py`：

```python
from arcgis_agent.catalog import TOOLS, validate_call


def base_layout(**extra):
    arguments = {"title": "湖南省坡度分级图", "layers": ["C:/d.gdb/slope"],
                 "output_name": "slope_map"}
    arguments.update(extra)
    return arguments


class BuildLayoutSchemaTests(unittest.TestCase):
    def test_styles_and_labels_are_optional_additions(self):
        properties = TOOLS["build_layout"].properties
        self.assertIn("styles", properties)
        self.assertIn("labels", properties)
        for name in ("styles", "labels"):
            self.assertNotIn(name, TOOLS["build_layout"].required)
            self.assertEqual(properties[name]["type"], "array")
            self.assertEqual(properties[name]["maxItems"], 12)

    def test_style_enum_matches_the_palette_table(self):
        # schema 常量与实现常量互锁：色板表改了而 schema 没改，这里就会红。
        schema = TOOLS["build_layout"].properties["styles"]["items"]["properties"]
        self.assertEqual(sorted(schema["palette"]["enum"]), PALETTE_NAMES)
        self.assertEqual(sorted(schema["method"]["enum"]), sorted(CLASSIFY_METHODS))
        self.assertEqual(schema["classes"]["minimum"], MIN_CLASSES)
        self.assertEqual(schema["classes"]["maximum"], MAX_CLASSES)

    def test_label_size_range_matches_the_validator(self):
        schema = TOOLS["build_layout"].properties["labels"]["items"]["properties"]
        self.assertEqual(schema["size"]["minimum"], 6)
        self.assertEqual(schema["size"]["maximum"], 24)

    def test_tool_count_is_unchanged(self):
        # 本期不新增任何 LLM 可见工具。
        self.assertEqual(len(TOOLS), 62)


class BuildLayoutCrossValidationTests(unittest.TestCase):
    def test_bare_call_still_validates(self):
        validate_call("build_layout", base_layout())

    def test_style_targeting_an_unknown_layer_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            validate_call("build_layout", base_layout(
                styles=[{"layer": "C:/d.gdb/other", "palette": "BLUES"}]))
        self.assertIn("C:/d.gdb/slope", str(caught.exception))

    def test_label_targeting_an_unknown_layer_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            validate_call("build_layout", base_layout(
                labels=[{"layer": "C:/d.gdb/other", "field": "NAME"}]))
        self.assertIn("layers", str(caught.exception))

    def test_styling_the_same_layer_twice_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            validate_call("build_layout", base_layout(
                styles=[{"layer": "C:/d.gdb/slope", "palette": "BLUES"},
                        {"layer": "C:/d.gdb/slope", "palette": "REDS"}]))
        self.assertIn("两次", str(caught.exception))

    def test_too_many_styles_is_refused_by_the_schema(self):
        layers = [f"C:/d.gdb/l{i}" for i in range(13)]
        with self.assertRaises(ValueError):
            validate_call("build_layout", base_layout(
                layers=layers,
                styles=[{"layer": item, "palette": "BASE"} for item in layers]))

    def test_empty_styles_and_labels_are_allowed(self):
        validate_call("build_layout", base_layout(styles=[], labels=[]))

    def test_existing_scale_bar_exclusion_still_holds(self):
        with self.assertRaises(ValueError):
            validate_call("build_layout", base_layout(
                elements=["SCALE_BAR", "DUAL_SCALE_BAR"]))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `PYTHONPATH=src python -m unittest tests.test_symbology.BuildLayoutSchemaTests -v`
Expected: FAIL，`KeyError: 'styles'`

- [ ] **Step 3: 写实现**

在 `catalog.py` 的枚举常量区（第 32 行 `EXPORT_EXTENSIONS` 之后）加：

```python
from .symbology import CLASSIFY_METHODS, MAX_CLASSES, MIN_CLASSES, PALETTE_NAMES

# 与 LAYERS 的 maxItems 一致：一个图层最多一条样式、一条标注。
STYLE_ITEMS = {"type": "array", "maxItems": 12, "items": {"type": "object", "additionalProperties": False,
    "required": ["layer", "palette"],
    "properties": {
        "layer": text("要符号化的图层，必须是 layers 里的同一个绝对路径"),
        "palette": {"type": "string", "enum": PALETTE_NAMES},
        "field": text("分级或唯一值依据的字段名", 64),
        "classes": {"type": "integer", "minimum": MIN_CLASSES, "maximum": MAX_CLASSES},
        "method": {"type": "string", "enum": list(CLASSIFY_METHODS)}}}}
LABEL_ITEMS = {"type": "array", "maxItems": 12, "items": {"type": "object", "additionalProperties": False,
    "required": ["layer", "field"],
    "properties": {
        "layer": text("要标注的图层，必须是 layers 里的同一个绝对路径"),
        "field": text("标注取自的字段名，文本或数值类型", 64),
        "size": {"type": "integer", "minimum": 6, "maximum": 24}}}}
```

这条 import 链已实测安全（2026-11-06）：`gis_tools.py` 与 `catalog.py` 顶层都只 import 标准库，本地模块一律在函数内惰性导入，所以 `catalog → symbology → analysis_tools → gis_tools` 不成环；导入 `analysis_tools` 后 `sys.modules` 里没有 `arcpy`，离线可导入性成立。**不要把这个 import 挪进函数里"保险"** —— 那只会让 schema 常量在导入期失去互锁校验。

把 `build_layout` 的注册（第 161-169 行）改为：

```python
    "build_layout": tool("创建出图工程与布局", "mp.ArcGISProject.createLayout / createMapFrame / createMapSurroundElement", {
        "title": text("图名，同时作为布局名称", 120),
        "layers": LAYERS,
        "output_name": text("新工程文件基名（ASCII），生成 cartography/<name>.aprx", 64, pattern=r"[A-Za-z][A-Za-z0-9_]{0,63}"),
        "page_size": {"type": "string", "enum": PAGE_SIZE_NAMES},
        "orientation": {"type": "string", "enum": ORIENTATIONS},
        "elements": {"type": "array", "items": {"type": "string", "enum": LAYOUT_ELEMENTS}, "minItems": 0, "maxItems": 4, "uniqueItems": True},
        "extent": MAP_EXTENT,
        "styles": STYLE_ITEMS,
        "labels": LABEL_ITEMS}, optional=("page_size", "orientation", "elements", "extent", "styles", "labels"), writes=True,
        effect="在任务目录的 cartography 子目录新建独立出图工程（复制 Pro 自带空白模板，不改动任何已有工程），添加图层、按 styles 指定的预设色板符号化、按 labels 标注、加图幅图名与所选项，再用 export_layout 导出图片。换配色需重建工程"),
```

把 `validate_call` 的 `build_layout` 分支（第 321-328 行）改为：

```python
    if name == "build_layout":
        elements = arguments.get("elements") or []
        if "SCALE_BAR" in elements and "DUAL_SCALE_BAR" in elements:
            raise ValueError("SCALE_BAR 与 DUAL_SCALE_BAR 只能二选一，两者会重叠在同一位置")
        if "extent" in arguments:
            box = arguments["extent"]
            if box["xmax"] <= box["xmin"] or box["ymax"] <= box["ymin"]:
                raise ValueError("extent 需要满足 xmax>xmin 且 ymax>ymin")
        declared = list(arguments["layers"])
        styled = []
        for entry in (arguments.get("styles") or []) + (arguments.get("labels") or []):
            if entry["layer"] not in declared:
                raise ValueError(f"styles/labels 里的 layer「{entry['layer']}」不在 layers 中；"
                                 f"本次 layers 是：{', '.join(declared)}。")
        for entry in arguments.get("styles") or []:
            if entry["layer"] in styled:
                raise ValueError(f"图层「{entry['layer']}」被符号化了两次；一个图层只能有一条 styles。")
            styled.append(entry["layer"])
```

- [ ] **Step 4: 运行测试确认通过**

Run: `PYTHONPATH=src python -m unittest tests.test_symbology -v`
Expected: PASS，累计约 69 项 OK

- [ ] **Step 5: 跑全量离线套件，重点看工具契约与技能包校验**

Run: `PYTHONPATH=src python -m unittest discover -s tests`
Expected: 全绿。特别确认 `tests/test_approvals.py:211` 的 `len(TOOLS) == 62` 与 `tests/test_cartography.py` 未回归

- [ ] **Step 6: 确认新 schema 确实进到了模型的 function 定义里**

Run: `PYTHONPATH=src python -c "from arcgis_agent.deepseek import tool_schemas; import json; s=[t for t in tool_schemas() if t['function']['name']=='build_layout'][0]; print(sorted(s['function']['parameters']['properties']))"`
Expected: 输出含 `'labels'` 与 `'styles'`

- [ ] **Step 7: 提交（需先征得用户同意）**

```bash
git add src/arcgis_agent/catalog.py tests/test_symbology.py
git commit -m "feat: build_layout 增加 styles 与 labels 参数"
```

---

### Task 5: CIM 助手与矢量 renderer

**Files:**
- Modify: `src/arcgis_agent/symbology.py`
- Test: `tests/test_symbology.py`

**Interfaces:**
- Consumes: `PALETTES`、`OUTLINES`、`palette_colors`、`hex_rgb`、`land_use_color`、`build_classes`、`renderer_kind`
- Produces:
  - `rgb_color(arcpy, hexcolor: str, alpha: int = 100)` —— 返回 `CIMRGBColor`，颜色写在 `.values`
  - `paint_symbol(symbol_ref, fill=None, stroke=None, width=None, size=None) -> dict` —— 原地改一个 `CIMSymbolReference` 内部的填充色/轮廓色/轮廓宽/点大小，返回各类改动计数
  - `apply_renderer(arcpy, definition, plan) -> None` —— 按 `plan["renderer"]` 原地改 `definition.renderer`；`plan` 形如 Task 8 的 `plan_style` 产出
  - `plan_style(arcpy, style: dict, dataset: str, desc) -> dict` —— 前置阶段的编排器：校验 → 读值 → 算断点/唯一值 → 返回可直接交给 `apply_renderer` 的 plan，也是回报给模型的 `symbology` 段内容

`plan` 的四种形态（`apply_renderer` 按 `renderer` 字段分派）：

```python
{"renderer": "CLASS_BREAKS", "palette": "BLUES", "field": "VALUE", "method": "EQUAL_INTERVAL",
 "classes": 5, "minimum": 5.0, "bounds": [...], "colors": [...], "sampled": False, "value_count": 9}
{"renderer": "UNIQUE_VALUE", "palette": "CATEGORY10", "field": "CATEGORY",
 "categories": [...], "colors": [...], "unmatched": []}
{"renderer": "SIMPLE", "palette": "BASE", "geometry": "Polygon",
 "fill": "#DCE6F1", "outline": "#5B7A99", "outline_width": 0.7}
{"renderer": "RASTER_RAMP", "palette": "ELEVATION", "colors": (…7 色…)}
```

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_symbology.py`。桩只模拟 symbology 真正触碰的那一小截 CIM 表面：

```python
from arcgis_agent.symbology import apply_renderer, paint_symbol, plan_style, rgb_color
from arcgis_agent.symbology import CATEGORY10


class CimRGBColor:
    def __init__(self):
        self.values = None


class CimSolidFill:
    def __init__(self):
        self.color = None


class CimSolidStroke:
    def __init__(self):
        self.color = None
        self.width = 0.7


class CimVectorMarker:
    def __init__(self):
        self.size = 10.0
        self.markerGraphics = []


class CimPolygonSymbol:
    def __init__(self, layers=None):
        self.symbolLayers = layers if layers is not None else [CimSolidStroke(), CimSolidFill()]


class CimSymbolReference:
    def __init__(self, symbol=None):
        self.symbol = symbol if symbol is not None else CimPolygonSymbol()


class CimClassBreak:
    def __init__(self):
        self.upperBound = 0
        self.label = ""
        self.symbol = None


class CimClassBreaksRenderer:
    def __init__(self):
        self.field = ""
        self.classBreakType = ""
        self.minimumBreak = 0
        self.breaks = []


class CimUniqueValue:
    def __init__(self):
        self.fieldValues = []


class CimUniqueValueClass:
    def __init__(self):
        self.values = []
        self.label = ""
        self.symbol = None
        self.visible = False
        self.editable = False


class CimUniqueValueGroup:
    def __init__(self):
        self.classes = []
        self.heading = ""


class CimUniqueValueRenderer:
    def __init__(self):
        self.fields = []
        self.groups = []
        self.useDefaultSymbol = False
        self.defaultSymbol = None
        self.defaultLabel = ""


class StubCim:
    CIMRGBColor = CimRGBColor
    CIMClassBreak = CimClassBreak
    CIMClassBreaksRenderer = CimClassBreaksRenderer
    CIMUniqueValue = CimUniqueValue
    CIMUniqueValueClass = CimUniqueValueClass
    CIMUniqueValueGroup = CimUniqueValueGroup
    CIMUniqueValueRenderer = CimUniqueValueRenderer


class StubArcpy:
    """只有 symbology 会碰的那一小截表面；真实构造形态由真机冒烟证明。"""
    cim = StubCim()


class StubRenderer:
    def __init__(self):
        self.symbol = CimSymbolReference()


class StubDefinition:
    def __init__(self):
        self.renderer = StubRenderer()


def prototype_colors(symbol_ref):
    """把符号里的填充色与轮廓色读出来，供断言。"""
    fills, strokes = [], []
    stack = [symbol_ref]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if isinstance(node, CimSolidFill):
            fills.append(node.color.values)
        elif isinstance(node, CimSolidStroke):
            strokes.append((node.color.values, node.width))
        for name in ("symbol", "symbolLayers", "markerGraphics"):
            if hasattr(node, name):
                stack.append(getattr(node, name))
    return fills, strokes


class RgbColorTests(unittest.TestCase):
    def test_color_lands_in_values_not_in_named_attributes(self):
        # 实测铁律：red=/green=/blue= 会被静默忽略，渲成灰色。
        made = rgb_color(StubArcpy(), "#08519c")
        self.assertEqual(made.values, [8, 81, 156, 100])

    def test_alpha_can_be_lowered(self):
        self.assertEqual(rgb_color(StubArcpy(), "#ffffff", alpha=50).values[3], 50)


class PaintSymbolTests(unittest.TestCase):
    def test_paints_fill_and_stroke_separately(self):
        reference = CimSymbolReference()
        changed = paint_symbol(StubArcpy(), reference, fill="#08519c", stroke="#FFFFFF", width=0.2)
        fills, strokes = prototype_colors(reference)
        self.assertEqual(fills, [[8, 81, 156, 100]])
        self.assertEqual(strokes, [([255, 255, 255, 100], 0.2)])
        self.assertEqual(changed, {"fill": 1, "stroke": 1, "width": 1, "size": 0})

    def test_fill_only_leaves_the_stroke_alone(self):
        reference = CimSymbolReference()
        original = reference.symbol.symbolLayers[0].color
        paint_symbol(StubArcpy(), reference, fill="#3182bd")
        self.assertIs(reference.symbol.symbolLayers[0].color, original)

    def test_marker_size_is_set_for_points(self):
        marker = CimVectorMarker()
        graphic_symbol = CimPolygonSymbol()
        marker.markerGraphics = [type("G", (), {"symbol": graphic_symbol})()]
        reference = CimSymbolReference(marker)
        changed = paint_symbol(StubArcpy(), reference, fill="#3B6E8F", size=6.0)
        self.assertEqual(marker.size, 6.0)
        self.assertEqual(changed["size"], 1)


class ApplyClassBreaksTests(unittest.TestCase):
    def plan(self):
        return {"renderer": "CLASS_BREAKS", "palette": "BLUES", "field": "VALUE",
                "method": "EQUAL_INTERVAL", "classes": 5, "minimum": 5.0,
                "bounds": [25.8, 46.6, 67.4, 88.2, 109.0],
                "colors": palette_colors("BLUES", 5), "sampled": False, "value_count": 9}

    def test_breaks_container_is_used_not_classbreaks(self):
        definition = StubDefinition()
        apply_renderer(StubArcpy(), definition, self.plan())
        renderer = definition.renderer
        self.assertIsInstance(renderer, CimClassBreaksRenderer)
        self.assertEqual(len(renderer.breaks), 5)
        self.assertFalse(hasattr(renderer, "classBreaks"))

    def test_each_break_carries_its_own_upper_bound_label_and_color(self):
        definition = StubDefinition()
        apply_renderer(StubArcpy(), definition, self.plan())
        bounds = [item.upperBound for item in definition.renderer.breaks]
        self.assertEqual(bounds, [25.8, 46.6, 67.4, 88.2, 109.0])
        # 颜色以 values 列表形式落在符号里，所以期望值也必须是 list 而非 tuple。
        actual = [prototype_colors(item.symbol)[0][0]
                  for item in definition.renderer.breaks]
        self.assertEqual(actual[4], [8, 81, 156, 100])    # #08519c，最深一档
        self.assertEqual(actual[3], [49, 130, 189, 100])  # #3182bd
        self.assertEqual(actual[0], [239, 243, 255, 100])  # #eff3ff，最浅一档

    def test_field_and_break_type_are_set(self):
        definition = StubDefinition()
        apply_renderer(StubArcpy(), definition, self.plan())
        self.assertEqual(definition.renderer.field, "VALUE")
        self.assertEqual(definition.renderer.classBreakType, "GraduatedColor")
        self.assertEqual(definition.renderer.minimumBreak, 5.0)

    def test_every_class_symbol_is_a_distinct_object(self):
        # 共用同一个符号对象会让五级渲成同一种颜色。
        definition = StubDefinition()
        apply_renderer(StubArcpy(), definition, self.plan())
        symbols = [id(item.symbol) for item in definition.renderer.breaks]
        self.assertEqual(len(set(symbols)), 5)


class ApplyUniqueValueTests(unittest.TestCase):
    def plan(self, palette="CATEGORY10"):
        categories = ["水体", "林地", "耕地", "建成区", "草地"]
        colors = [land_use_color(item)[0] if palette == "LAND_USE" else CATEGORY10[i]
                  for i, item in enumerate(categories)]
        return {"renderer": "UNIQUE_VALUE", "palette": palette, "field": "CATEGORY",
                "categories": categories, "colors": colors, "unmatched": []}

    def test_groups_container_wraps_the_classes(self):
        definition = StubDefinition()
        apply_renderer(StubArcpy(), definition, self.plan())
        renderer = definition.renderer
        self.assertIsInstance(renderer, CimUniqueValueRenderer)
        self.assertEqual(len(renderer.groups), 1)
        self.assertEqual(len(renderer.groups[0].classes), 5)
        self.assertEqual(renderer.fields, ["CATEGORY"])

    def test_each_class_carries_its_field_value_and_label(self):
        definition = StubDefinition()
        apply_renderer(StubArcpy(), definition, self.plan())
        first = definition.renderer.groups[0].classes[0]
        self.assertEqual(first.values[0].fieldValues, ["水体"])
        self.assertEqual(first.label, "水体")
        self.assertTrue(first.visible)

    def test_land_use_semantic_colors_reach_the_symbols(self):
        definition = StubDefinition()
        apply_renderer(StubArcpy(), definition, self.plan("LAND_USE"))
        water = definition.renderer.groups[0].classes[0]
        self.assertEqual(prototype_colors(water.symbol)[0][0], [59, 125, 191, 100])


class ApplySimpleTests(unittest.TestCase):
    def test_polygon_gets_fill_and_outline(self):
        definition = StubDefinition()
        apply_renderer(StubArcpy(), definition, {
            "renderer": "SIMPLE", "palette": "BASE", "geometry": "Polygon",
            "fill": "#DCE6F1", "outline": "#5B7A99", "outline_width": 0.7})
        fills, strokes = prototype_colors(definition.renderer.symbol)
        self.assertEqual(fills, [[220, 230, 241, 100]])
        self.assertEqual(strokes, [([91, 122, 153, 100], 0.7)])

    def test_line_paints_the_stroke_only(self):
        definition = StubDefinition()
        definition.renderer.symbol = CimSymbolReference(
            type("L", (), {"symbolLayers": [CimSolidStroke()]})())
        apply_renderer(StubArcpy(), definition, {
            "renderer": "SIMPLE", "palette": "BASE", "geometry": "Polyline",
            "fill": None, "outline": "#4D4D4D", "outline_width": 1.2})
        _, strokes = prototype_colors(definition.renderer.symbol)
        self.assertEqual(strokes, [([77, 77, 77, 100], 1.2)])

    def test_point_sets_marker_size(self):
        marker = CimVectorMarker()
        marker.markerGraphics = [type("G", (), {"symbol": CimPolygonSymbol()})()]
        definition = StubDefinition()
        definition.renderer.symbol = CimSymbolReference(marker)
        apply_renderer(StubArcpy(), definition, {
            "renderer": "SIMPLE", "palette": "BASE", "geometry": "Point",
            "fill": "#3B6E8F", "outline": "#FFFFFF", "outline_width": 0.5, "size": 6.0})
        self.assertEqual(marker.size, 6.0)


class PlanStyleTests(unittest.TestCase):
    def test_class_breaks_plan_reads_values_and_computes_bounds(self):
        fake = FakeArcpy([(v,) for v in NINE])
        fake.cim = StubCim()
        plan = plan_style(fake, {"layer": "L", "palette": "BLUES", "field": "VALUE",
                                 "classes": 5, "method": "EQUAL_INTERVAL"}, "L", polygon_desc())
        self.assertEqual(plan["renderer"], "CLASS_BREAKS")
        self.assertEqual(plan["bounds"][-1], 109.0)
        self.assertEqual(len(plan["colors"]), 5)
        self.assertFalse(plan["sampled"])
        self.assertEqual(plan["value_count"], 9)

    def test_unique_value_plan_collects_sorted_categories(self):
        fake = FakeArcpy([("林地",), ("水体",), ("耕地",)])
        fake.cim = StubCim()
        plan = plan_style(fake, {"layer": "L", "palette": "CATEGORY10", "field": "CATEGORY"},
                          "L", polygon_desc())
        self.assertEqual(plan["categories"], sorted(["林地", "水体", "耕地"]))
        self.assertEqual(len(plan["colors"]), 3)

    def test_unique_value_over_the_limit_is_refused_with_the_real_count(self):
        fake = FakeArcpy([(f"类{i}",) for i in range(MAX_UNIQUE_VALUES + 2)])
        fake.cim = StubCim()
        with self.assertRaises(ValueError) as caught:
            plan_style(fake, {"layer": "L", "palette": "CATEGORY10", "field": "CATEGORY"},
                       "L", polygon_desc())
        self.assertIn(str(MAX_UNIQUE_VALUES), str(caught.exception))

    def test_land_use_reports_unmatched_values(self):
        fake = FakeArcpy([("水体",), ("其他",), ("未知类",)])
        fake.cim = StubCim()
        plan = plan_style(fake, {"layer": "L", "palette": "LAND_USE", "field": "CATEGORY"},
                          "L", polygon_desc())
        self.assertEqual(plan["unmatched"], ["其他", "未知类"])

    def test_simple_plan_needs_no_data_read(self):
        fake = FakeArcpy([])
        fake.cim = StubCim()
        plan = plan_style(fake, {"layer": "L", "palette": "BASE"}, "L", polygon_desc())
        self.assertEqual(plan["renderer"], "SIMPLE")
        self.assertEqual(plan["geometry"], "Polygon")
        self.assertEqual(fake.cursor_calls, [])

    def test_sampling_is_reported_in_the_plan(self):
        fake = FakeArcpy([(float(i),) for i in range(50)], total=50)
        fake.cim = StubCim()
        plan = plan_style(fake, {"layer": "L", "palette": "BLUES", "field": "VALUE",
                                 "classes": 3}, "L", polygon_desc())
        self.assertTrue(plan["sampled"])
```

注意 `FakeArcpy`（Task 2 定义）需要能挂 `cim` 属性；测试里直接赋值即可，无需改类。

- [ ] **Step 2: 运行测试确认失败**

Run: `PYTHONPATH=src python -m unittest tests.test_symbology.ApplyClassBreaksTests -v`
Expected: FAIL，`ImportError: cannot import name 'apply_renderer'`

- [ ] **Step 3: 写实现**

在 `symbology.py` 末尾追加：

```python
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
```

继续追加 renderer 构造与编排：

```python
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
    """按 plan 原地改 definition.renderer。SIMPLE 直接改默认符号，不新建 renderer。"""
    kind = plan["renderer"]
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `PYTHONPATH=src python -m unittest tests.test_symbology -v`
Expected: PASS，累计约 90 项 OK

- [ ] **Step 5: 跑全量离线套件**

Run: `PYTHONPATH=src python -m unittest discover -s tests`
Expected: 全绿，退出码 0

- [ ] **Step 6: 提交（需先征得用户同意）**

```bash
git add src/arcgis_agent/symbology.py tests/test_symbology.py
git commit -m "feat: CIM 矢量符号化（分级色彩/唯一值/简单符号）"
```

---

### Task 6: 栅格 colorizer

**Files:**
- Modify: `src/arcgis_agent/symbology.py`
- Test: `tests/test_symbology.py`

**Interfaces:**
- Consumes: `ramp_colors`、`rgb_color`、Task 5 的 `apply_renderer`
- Produces:
  - `apply_raster_ramp(arcpy, definition, plan) -> None` —— 改 `definition.colorizer.colorRamp`，**不碰 `definition.renderer`**（栅格图层的 renderer 恒为 `None`）
  - `plan_style` 的 `RASTER_RAMP` 分支补齐 `colors` 字段
  - `apply_renderer` 遇到 `RASTER_RAMP` 时委派给 `apply_raster_ramp`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_symbology.py`：

```python
from arcgis_agent.symbology import apply_raster_ramp


class CimLinearContinuousColorRamp:
    def __init__(self):
        self.fromColor = None
        self.toColor = None


class CimMultipartColorRamp:
    def __init__(self):
        self.colorRamps = []
        self.weights = []


class CimRasterStretchColorizer:
    def __init__(self):
        self.colorRamp = None
        self.colorScheme = ""


class StubRasterDefinition:
    def __init__(self):
        self.renderer = None          # 实测：栅格图层的 renderer 恒为 None
        self.colorizer = CimRasterStretchColorizer()


StubCim.CIMLinearContinuousColorRamp = CimLinearContinuousColorRamp
StubCim.CIMMultipartColorRamp = CimMultipartColorRamp


class RasterRampTests(unittest.TestCase):
    def plan(self, palette="ELEVATION"):
        return {"renderer": "RASTER_RAMP", "palette": palette, "colors": ramp_colors(palette)}

    def test_ramp_replaces_the_colorizer_not_the_renderer(self):
        definition = StubRasterDefinition()
        apply_raster_ramp(StubArcpy(), definition, self.plan())
        self.assertIsNone(definition.renderer)
        self.assertIsNotNone(definition.colorizer.colorRamp)

    def test_seven_stops_become_six_linear_segments(self):
        definition = StubRasterDefinition()
        apply_raster_ramp(StubArcpy(), definition, self.plan())
        ramp = definition.colorizer.colorRamp
        self.assertIsInstance(ramp, CimMultipartColorRamp)
        self.assertEqual(len(ramp.colorRamps), 6)

    def test_each_segment_joins_adjacent_stops(self):
        definition = StubRasterDefinition()
        colors = ramp_colors("ELEVATION")
        apply_raster_ramp(StubArcpy(), definition, self.plan())
        for index, segment in enumerate(definition.colorizer.colorRamp.colorRamps):
            self.assertEqual(segment.fromColor.values[:3], list(hex_rgb(colors[index])))
            self.assertEqual(segment.toColor.values[:3], list(hex_rgb(colors[index + 1])))

    def test_sequential_palettes_also_work_on_rasters(self):
        definition = StubRasterDefinition()
        apply_raster_ramp(StubArcpy(), definition, self.plan("BLUES"))
        self.assertEqual(len(definition.colorizer.colorRamp.colorRamps), 6)

    def test_apply_renderer_delegates_raster_plans(self):
        definition = StubRasterDefinition()
        apply_renderer(StubArcpy(), definition, self.plan())
        self.assertIsNotNone(definition.colorizer.colorRamp)

    def test_missing_colorizer_is_refused_rather_than_silently_skipped(self):
        definition = StubRasterDefinition()
        definition.colorizer = None
        with self.assertRaises(ValueError) as caught:
            apply_raster_ramp(StubArcpy(), definition, self.plan())
        self.assertIn("colorizer", str(caught.exception))

    def test_raster_plan_carries_seven_colors(self):
        fake = FakeArcpy([])
        fake.cim = StubCim()
        plan = plan_style(fake, {"layer": "L", "palette": "ELEVATION"}, "L", raster_desc())
        self.assertEqual(plan["renderer"], "RASTER_RAMP")
        self.assertEqual(len(plan["colors"]), 7)
        self.assertEqual(fake.cursor_calls, [])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `PYTHONPATH=src python -m unittest tests.test_symbology.RasterRampTests -v`
Expected: FAIL，`ImportError: cannot import name 'apply_raster_ramp'`

- [ ] **Step 3: 写实现**

在 `symbology.py` 里，把 `plan_style` 的开头改为先处理栅格分支（放在 `kind == "SIMPLE"` 判断之前）：

```python
    if kind == "RASTER_RAMP":
        plan["colors"] = list(ramp_colors(palette))
        return plan
```

在 `apply_renderer` 里把 `else` 分支替换为委派：

```python
    elif kind == "RASTER_RAMP":
        apply_raster_ramp(arcpy, definition, plan)
    else:
        raise ValueError(f"未实现的 renderer：{kind}")
```

并在 `symbology.py` 末尾追加：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `PYTHONPATH=src python -m unittest tests.test_symbology -v`
Expected: PASS，累计约 97 项 OK

- [ ] **Step 5: 提交（需先征得用户同意）**

```bash
git add src/arcgis_agent/symbology.py tests/test_symbology.py
git commit -m "feat: 栅格 colorizer 多段色带"
```

---

### Task 7: 标注

**Files:**
- Modify: `src/arcgis_agent/symbology.py`
- Test: `tests/test_symbology.py`

**Interfaces:**
- Consumes: `validate_label_spec`、`rgb_color`、`paint_symbol`
- Produces:
  - `apply_label(arcpy, definition, label) -> None` —— 原地改 `definition.labelClasses[0]` 并置 `definition.labelVisibility = True`
  - `LABEL_SIZE_DEFAULT = 9`、`LABEL_COLOR = "#000000"`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_symbology.py`：

```python
from arcgis_agent.symbology import LABEL_COLOR, LABEL_SIZE_DEFAULT, apply_label


class CimTextSymbol:
    def __init__(self):
        self.fontSize = 10
        self.height = 10
        self.fontFamilyName = ""
        self.symbol = CimPolygonSymbol()


class CimLabelClass:
    def __init__(self):
        self.expression = "$feature.OBJECTID"
        self.expressionEngine = "Arcade"
        self.visibility = False
        self.textSymbol = CimSymbolReference(CimTextSymbol())


class StubLabelDefinition:
    def __init__(self, count=1):
        self.renderer = StubRenderer()
        self.labelClasses = [CimLabelClass() for _ in range(count)]
        self.labelVisibility = False


class ApplyLabelTests(unittest.TestCase):
    def test_expression_uses_arcade_field_syntax(self):
        definition = StubLabelDefinition()
        apply_label(StubArcpy(), definition, {"layer": "L", "field": "NAME", "size": 9})
        self.assertEqual(definition.labelClasses[0].expression, "$feature.NAME")
        self.assertEqual(definition.labelClasses[0].expressionEngine, "Arcade")

    def test_visibility_is_turned_on_both_levels(self):
        definition = StubLabelDefinition()
        self.assertFalse(definition.labelVisibility)
        apply_label(StubArcpy(), definition, {"layer": "L", "field": "NAME"})
        self.assertTrue(definition.labelVisibility)
        self.assertTrue(definition.labelClasses[0].visibility)

    def test_size_reaches_both_fontsize_and_height(self):
        definition = StubLabelDefinition()
        apply_label(StubArcpy(), definition, {"layer": "L", "field": "NAME", "size": 16})
        symbol = definition.labelClasses[0].textSymbol.symbol
        self.assertEqual(symbol.fontSize, 16)
        self.assertEqual(symbol.height, 16)

    def test_default_size_is_the_documented_one(self):
        definition = StubLabelDefinition()
        apply_label(StubArcpy(), definition, {"layer": "L", "field": "NAME"})
        self.assertEqual(definition.labelClasses[0].textSymbol.symbol.fontSize,
                         LABEL_SIZE_DEFAULT)

    def test_glyph_color_lands_in_the_inner_polygon_symbol(self):
        definition = StubLabelDefinition()
        apply_label(StubArcpy(), definition, {"layer": "L", "field": "NAME"})
        fills, _ = prototype_colors(definition.labelClasses[0].textSymbol.symbol.symbol)
        self.assertEqual(fills, [list(hex_rgb(LABEL_COLOR)) + [100]])

    def test_the_existing_label_class_is_mutated_not_replaced(self):
        # 实测：整个替换 labelClasses 会落盘成功但不渲染。
        definition = StubLabelDefinition()
        original = definition.labelClasses[0]
        apply_label(StubArcpy(), definition, {"layer": "L", "field": "NAME"})
        self.assertIs(definition.labelClasses[0], original)
        self.assertEqual(len(definition.labelClasses), 1)

    def test_a_layer_without_any_label_class_is_refused(self):
        definition = StubLabelDefinition(count=0)
        with self.assertRaises(ValueError) as caught:
            apply_label(StubArcpy(), definition, {"layer": "L", "field": "NAME"})
        self.assertIn("labelClass", str(caught.exception))

    def test_field_names_with_spaces_are_quoted_for_arcade(self):
        definition = StubLabelDefinition()
        apply_label(StubArcpy(), definition, {"layer": "L", "field": "Pop 2020"})
        self.assertEqual(definition.labelClasses[0].expression, "$feature['Pop 2020']")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `PYTHONPATH=src python -m unittest tests.test_symbology.ApplyLabelTests -v`
Expected: FAIL，`ImportError: cannot import name 'apply_label'`

- [ ] **Step 3: 写实现**

在 `symbology.py` 末尾追加：

```python
LABEL_SIZE_DEFAULT = 9
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `PYTHONPATH=src python -m unittest tests.test_symbology -v`
Expected: PASS，累计约 105 项 OK

- [ ] **Step 5: 提交（需先征得用户同意）**

```bash
git add src/arcgis_agent/symbology.py tests/test_symbology.py
git commit -m "feat: 图层标注（Arcade 表达式与字号字色）"
```

---

### Task 8: cartography.py 接线

**Files:**
- Modify: `src/arcgis_agent/cartography.py:132-149`（`_add_title` 的颜色写法）、`:152-196`（`_fill_layout`）、`:198-220`（`_build_layout`）
- Test: `tests/test_cartography.py`（追加测试类与桩能力）

**Interfaces:**
- Consumes: `plan_style`、`validate_label_spec`、`apply_renderer`、`apply_label`
- Produces: `build_layout` 的返回结果新增 `symbology` 段（每图层的 plan 列表）与 `labels` 段

- [ ] **Step 1: 写失败测试**

在 `tests/test_cartography.py` 末尾追加。三处前置准备：

1. 该文件的 `TestCase` 已有 `self.root`（临时任务目录）与模块级 `base()`（最小合法 `build_layout` 参数），直接复用；本任务再加一个 `styled_base()`。
2. `StubArcpy` 需补 `cim` 属性。`tests/` 不是可导入的包（无 `__init__.py`），**不要为此新增 `__init__.py` 改变现有测试布局** —— 把 Task 5/6/7 定义的 `CimRGBColor`、`CimClassBreaksRenderer`、`CimUniqueValueRenderer`、`CimMultipartColorRamp`、`CimTextSymbol`、`CimLabelClass` 等桩类照抄进 `test_cartography.py`，组装成一个本地 `StubCim` 后赋给 `StubArcpy.cim`。
3. `_fill_layout` 现在会对有样式或有标注的图层调用 `layer.getDefinition("V3")` 与 `layer.setDefinition(definition)`，所以该文件里的**图层桩必须补这两个方法**，并把收到的 definition 记下来供断言：

```python
class StubLayer:                      # 若文件里已有同职责的桩，就地补这两个方法
    def __init__(self, name):
        self.name = name
        self.definitions_received = []

    def getDefinition(self, version):
        assert version == "V3"
        return StubLayerDefinition()

    def setDefinition(self, definition):
        self.definitions_received.append(definition)


class StubLayerDefinition:
    def __init__(self):
        self.renderer = StubSimpleRenderer()   # 持有 .symbol（CimSymbolReference 桩）
        self.labelClasses = [StubLabelClass()]
        self.labelVisibility = False
```

`StubMap.addDataFromPath` 要返回这个 `StubLayer` 实例并把它留住，测试才能事后检查 `definitions_received`。

然后追加辅助函数与测试类：

```python
def styled_base(palette="BLUES", field="VALUE"):
    """在 base() 之上挂一条样式，字段名与桩数据的字段保持一致。

    classes/method 只对分级色彩有意义：唯一值路径会显式拒绝它们（见 Task 3），
    所以按色板 kind 决定是否带上。
    """
    arguments = base()
    style = {"layer": arguments["layers"][0], "palette": palette, "field": field}
    if PALETTES[palette]["kind"] == "SEQUENTIAL":
        style.update({"classes": 5, "method": "EQUAL_INTERVAL"})
    arguments["styles"] = [style]
    return arguments
```

（`PALETTES` 需在 `test_cartography.py` 顶部从 `arcgis_agent.symbology` 导入。）

```python
class SymbologyWiringTests(unittest.TestCase):
    def test_styles_are_planned_before_the_template_is_copied(self):
        # 前置校验不变量：被拒的样式不能留下半成品 .aprx，也不能创建 cartography 目录。
        arguments = base()
        arguments["styles"] = [{"layer": "C:/nope.gdb/missing", "palette": "BLUES"}]
        with self.assertRaises(ValueError):
            cartography._build_layout(StubArcpy(), arguments, self.root)
        self.assertFalse((self.root / "cartography").exists())

    def test_a_refused_style_leaves_no_staged_project_behind(self):
        arguments = base()
        arguments["styles"] = [{"layer": arguments["layers"][0], "palette": "ELEVATION"}]
        with self.assertRaises(ValueError):
            cartography._build_layout(StubArcpy(), arguments, self.root)
        leftovers = [p.name for p in self.root.rglob("*.aprx")]
        self.assertEqual(leftovers, [])

    def test_result_reports_the_symbology_plan(self):
        result = cartography._build_layout(StubArcpy(), styled_base(), self.root)
        self.assertIn("symbology", result)
        self.assertEqual(result["symbology"][0]["palette"], "BLUES")
        self.assertEqual(result["symbology"][0]["renderer"], "CLASS_BREAKS")
        self.assertEqual(len(result["symbology"][0]["bounds"]), 5)

    def test_result_reports_labels(self):
        arguments = styled_base()
        arguments["labels"] = [{"layer": arguments["layers"][0], "field": "NAME", "size": 12}]
        result = cartography._build_layout(StubArcpy(), arguments, self.root)
        self.assertEqual(result["labels"], [{"layer": arguments["layers"][0],
                                             "field": "NAME", "size": 12}])

    def test_unmatched_land_use_values_are_surfaced(self):
        arguments = styled_base(palette="LAND_USE", field="CATEGORY")
        result = cartography._build_layout(StubArcpy(), arguments, self.root)
        self.assertIn("unmatched", result["symbology"][0])

    def test_omitting_styles_keeps_the_previous_behaviour(self):
        # 向后兼容：不传 styles/labels 时返回结构除新增键外与改动前一致。
        result = cartography._build_layout(StubArcpy(), base(), self.root)
        self.assertEqual(result["symbology"], [])
        self.assertEqual(result["labels"], [])
        self.assertEqual(result["page_size"], "A4")
        self.assertIn("next_step", result)

    def test_title_color_is_written_through_values_not_keywords(self):
        # 既有缺陷回归测试：red=/green=/blue= 会被静默忽略，黑色只是碰巧等于默认值。
        recorded = []
        arcpy_stub = StubArcpy()
        arcpy_stub.cim.CIMRGBColor = lambda: recorded.append("constructed") or CimRGBColor()
        cartography._build_layout(arcpy_stub, base(), self.root)
        self.assertTrue(recorded)
```

再往 `SymbologyWiringTests` 里补三项，验证符号化与标注真的落到了图层定义上。**断言桩图层收到的 definition，而不是断言调用序列** —— `cartography.py` 用 `from .symbology import …` 直接绑定了函数名，monkeypatch `symbology` 的模块属性对它没有任何作用：

```python
    def test_definition_reaches_the_layer_with_a_class_breaks_renderer(self):
        stub = StubArcpy()
        cartography._build_layout(stub, styled_base(), self.root)
        layer = stub.last_map.layers_added[0]
        self.assertEqual(len(layer.definitions_received), 1)
        definition = layer.definitions_received[0]
        self.assertEqual(type(definition.renderer).__name__, "CimClassBreaksRenderer")
        self.assertEqual(len(definition.renderer.breaks), 5)

    def test_definition_reaches_the_layer_with_labels_turned_on(self):
        arguments = styled_base()
        arguments["labels"] = [{"layer": arguments["layers"][0], "field": "NAME", "size": 12}]
        stub = StubArcpy()
        cartography._build_layout(stub, arguments, self.root)
        definition = stub.last_map.layers_added[0].definitions_received[0]
        self.assertTrue(definition.labelVisibility)
        self.assertEqual(definition.labelClasses[0].expression, "$feature.NAME")
        self.assertEqual(definition.labelClasses[0].textSymbol.symbol.fontSize, 12)

    def test_unstyled_layers_are_never_round_tripped_through_cim(self):
        # 不带 styles/labels 时完全不碰 getDefinition，保持改动前的行为与开销。
        stub = StubArcpy()
        cartography._build_layout(stub, base(), self.root)
        self.assertEqual(stub.last_map.layers_added[0].definitions_received, [])
```

`StubArcpy` 需暴露 `last_map`（最近一次 `listMaps()[0]` 返回的桩地图），桩地图需把 `addDataFromPath` 创建的图层留在 `layers_added` 列表里。若该文件的桩已有等价能力，直接复用，不要重复造。

- [ ] **Step 2: 运行测试确认失败**

Run: `PYTHONPATH=src python -m unittest tests.test_cartography.SymbologyWiringTests -v`
Expected: FAIL，`KeyError: 'symbology'` 或 `TypeError`（`_build_layout` 尚不认 styles）

- [ ] **Step 3: 写实现**

先修 `_add_title` 的既有缺陷（`cartography.py:142`）：

```python
    symbol.color = arcpy.cim.CIMRGBColor(red=0, green=0, blue=0)
```

改为：

```python
    # 颜色必须写进 values：red=/green=/blue= 关键字会被静默忽略，
    # 现在的黑色只是 values 未设置时的默认值碰巧撞对的。
    title_color = arcpy.cim.CIMRGBColor()
    title_color.values = [0, 0, 0, 100]
    symbol.color = title_color
```

在 `cartography.py` 顶部 import 区加：

```python
from .symbology import apply_label, apply_renderer, plan_style, validate_label_spec
```

把 `_fill_layout` 的签名与图层循环改为接收并应用 plans：

```python
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
            if dataset in by_dataset or dataset in labels_by_dataset:
                definition = layer.getDefinition("V3")
                if dataset in by_dataset:
                    apply_renderer(arcpy, definition, by_dataset[dataset])
                if dataset in labels_by_dataset:
                    apply_label(arcpy, definition, labels_by_dataset[dataset])
                layer.setDefinition(definition)
            layers.append(layer.name)
```

其余部分（坐标系一致性检查、建布局、图幅、相机、图廓元素、图名、保存、返回）保持不变，只在返回字典里追加两个键：

```python
        return {"project": str(project_path), "layout": layout.name, "map": map_object.name, "layers": layers,
                "page_mm": [width, height], "page_size": arguments.get("page_size", "A4"),
                "orientation": arguments.get("orientation", "LANDSCAPE"), "elements": placed,
                "spatial_reference": {"name": reference.name, "wkid": reference.factoryCode},
                "extent": {"xmin": extent.XMin, "ymin": extent.YMin, "xmax": extent.XMax, "ymax": extent.YMax},
                "extent_source": "requested" if "extent" in arguments else "data_extent",
                "symbology": [plan for _, plan in plans],
                "labels": list(labels_by_dataset.values()),
                "project_modified": False, "next_step": "用 export_layout 传入本 project 路径与 layout 名称导出图片"}
```

把 `_build_layout` 改为在复制模板**之前**完成全部样式与标注规划：

```python
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
```

注意 `_reserve(project_path)` 会创建 `cartography/` 目录，而它排在规划之后 —— 这正是"被拒调用不创建目录"这条不变量的实现位置，不要把它提前。

`tests/test_cartography.py` 的 `StubArcpy` 补上 `cim` 属性（桩类已按 Step 1 前置准备第 2 条抄进本文件，不要跨文件 import，也不要为此给 `tests/` 加 `__init__.py`）：

```python
StubArcpy.cim = StubCim
```

- [ ] **Step 4: 运行测试确认通过**

Run: `PYTHONPATH=src python -m unittest tests.test_cartography tests.test_symbology -v`
Expected: PASS

- [ ] **Step 5: 跑全量离线套件**

Run: `PYTHONPATH=src python -m unittest discover -s tests`
Expected: 全绿，退出码 0；`test_approvals.py` 的 62 断言与既有 cartography 契约测试均未回归

- [ ] **Step 6: 提交（需先征得用户同意）**

```bash
git add src/arcgis_agent/cartography.py tests/test_cartography.py tests/test_symbology.py
git commit -m "feat: build_layout 应用符号化与标注，并修正图名颜色写法"
```

---

### Task 9: 真机冒烟与验证记录

**Files:**
- Create: `scripts/smoke_symbology.py`
- Modify: `docs/validation.md`（新增批次，插在最前面）

**Interfaces:**
- Consumes: 全部已实现的 `build_layout` 符号化路径
- Produces: `scripts/smoke_symbology.py`，用 Pro Python 运行，退出码 0 表示全绿

- [ ] **Step 1: 写冒烟脚本**

沿用 `scripts/smoke_cartography.py` 的惯例：`Traced` 包装 runtime 记录全部 refusals、`checks` dict + `all()` + 退出码、`ink()` 像素占比、`digest()` SHA256、fixture 写 `workspace/smoke/<uuid>`。

**探测脚本是本任务最直接的可复用素材**：`workspace/probe-cim/probe_cim{,2,3,4,5,6}.py`（本地忽略目录，未入库）里有已经跑通的 fixture 构造、`export_and_count`、颜色直方图统计与各类断言。写冒烟脚本时直接从 `probe_cim6.py` 抄已验证的部分，不要凭记忆重写 —— 那里面的 CIM 属性名和像素断言都是实测过的。探测阶段最后一轮 `run6-04f464ca` 六项全绿，可作为期望结果的参照。

关键新增能力是**颜色直方图断言**，它比 `ink()` 强一个量级 —— 不仅证明"图上有墨"，还证明"色板真的落到了图上、且分级归属正确"：

```python
"""真机冒烟：符号化与标注。必须用 ArcGIS Pro Python 运行，不是系统 Python。

fixture 全部是 workspace/smoke/<uuid> 下的合成数据，绝不使用用户数据集。
除 ink() 像素占比外，本脚本用颜色直方图断言色板真的落到了导出图上：
探测阶段已证明像素数可反推分级归属（9 个面分 2/2/2/2/1 时，各档像素恰为
72200×4 + 36100），因此这里的断言同时覆盖配色与分类正确性。
"""
```

`checks` 至少覆盖：

- `class_breaks_palette_lands`：BLUES 5 档在导出 PNG 中全部命中（容差 ≤4/通道）
- `class_breaks_pixel_counts_match_the_data`：各档像素数与要素分布吻合（沿用探测阶段的 2/2/2/2/1 断言）
- `unique_value_palette_lands`：CATEGORY10 前 5 色全部命中，且像素数吻合类别分布
- `land_use_semantic_colors`：水体面命中 `#3B7DBF`
- `land_use_unmatched_reported`：返回的 `symbology[0]["unmatched"]` 含注入的"其他"类
- `simple_polygon_fill`：BASE 的 `#DCE6F1` 命中
- `simple_line_and_point`：线与点各自的固定色命中
- `raster_elevation_ramp`：ELEVATION 7 色全部命中
- `labels_render`：带标注与不带标注两次导出的 `ink()` 存在显著差异，且深色像素增加
- `label_color_is_custom`：字色命中
- `title_color_still_black_after_the_values_fix`：图名区域仍是黑色（`_add_title` 修正的回归）
- `export_is_read_only`：导出前后 `.aprx` 的 SHA256 一致
- `source_data_unchanged`：源 fixture 的 SHA256 一致
- 九条拒绝路径各一项，且**每项都断言零残留**（沿用 `only_published_files_remain` 的逐目录扫描）
- `sampled_flag_reported`：把 `VALUE_CAP` 临时调小以触发抽样，断言返回里 `sampled is True`

导出 4 张代表性图到 `workspace/smoke/<uuid>/review/`，供人眼验收（冒烟不替代这一步）：专题分级、土地利用唯一值、地形晕渲叠分层设色、带标注规范图。

- [ ] **Step 2: 运行冒烟**

Run:
```bash
PYTHONIOENCODING=utf-8 "E:/programs/ArcGIS/bin/Python/envs/arcgispro-py3/python.exe" scripts/smoke_symbology.py
```
Expected: `ok: true`，全部 checks 为真，退出码 0。若有红项，`refusals` 会给出实际错误文本。

- [ ] **Step 3: 独占复跑一次**

`docs/validation.md:56` 记录过一次"三项同红但代码与通过版本完全一致"的经历，原因是整机并行跑离线套件与像素统计。出问题的检查项要独占复跑确认。

Run: 同 Step 2，期间不并行跑其他负载。
Expected: 全绿。

- [ ] **Step 4: 人眼验收**

打开 `workspace/smoke/<uuid>/review/` 下的 4 张图给用户确认。避让效果与整体观感无法机器判定，这一步不能跳。

- [ ] **Step 5: 写 `docs/validation.md` 新批次**

在文件最前面（批次倒序）插入新批次，沿用既有格式：先离线测试（Python 版本 + 项数 + 耗时 + 退出码），再真机（Pro Python 版本 + checks 数 + `ok: true`），然后列实测通过项、拒绝路径、安全不变量，以及**实测得出的 API 结论**（把设计规格 §8.1 的偏差表搬过来，参照第 57 行的既有写法）。

必须以加粗 `**本轮未验证**：` 段落收尾，如实写清：出图成果的业务级人眼验收结论、标注避让只做了最小实现、栅格分级未做（并记录"colorizer 自带 `stretchStats.histogram`，将来做栅格分级无需自己读像元"这条新发现）、色板观感未经制图专业评审。

- [ ] **Step 6: 提交（需先征得用户同意）**

```bash
git add scripts/smoke_symbology.py docs/validation.md
git commit -m "test: 符号化与标注的真机冒烟与验证记录"
```

---

### Task 10: 提示词、技能包与文档

**Files:**
- Modify: `src/arcgis_agent/prompts/system.md:52-54`（`## 出图` 章节）
- Modify: `src/arcgis_agent/skills/interpolation_surface_map.json:19-20`
- Modify: `src/arcgis_agent/skills/csv_points_to_density_map.json:16-17`
- Modify: `docs/tools.md:65-66`、`:95-102`
- Modify: `docs/architecture.md`
- Modify: `CHANGELOG.md`、`README.md`、`README.en.md`
- Test: `tests/test_system_prompt.py`、`tests/test_skills.py`

**Interfaces:**
- Consumes: 已实现的全部能力
- Produces: 模型能正确选用色板；两个内置技能包默认就出符号正确的图

- [ ] **Step 1: 写失败测试**

在 `tests/test_system_prompt.py` 的 `REQUIRED_MARKERS` 里加入符号化相关标记（沿用该文件既有的标记检查机制）：

```python
        "styles",
        "色板",
        "labels",
        "重建工程",
```

在 `tests/test_skills.py` 里追加断言，确认两个技能包的出图步骤带上了 `styles`。该文件已从 `arcgis_agent.skills` 导入 `load_skills`，它返回 `{技能名: 完整 payload}`，直接用即可，不要另造加载函数：

```python
class SkillSymbologyTests(unittest.TestCase):
    def test_map_skills_declare_symbology(self):
        packs = load_skills()
        for name in ("interpolation_surface_map", "csv_points_to_density_map"):
            self.assertIn(name, packs)
            build = next(step for step in packs[name]["steps"] if step["tool"] == "build_layout")
            self.assertIn("styles", build["arguments"], name)
            for style in build["arguments"]["styles"]:
                self.assertIn("layer", style, name)
                self.assertIn("palette", style, name)

    def test_skill_packs_still_pass_the_loader_validation(self):
        # load_skills() 内部会对每个包跑 validate_skill；新加的 styles 步骤必须仍合法。
        self.assertTrue(load_skills())
```

- [ ] **Step 2: 运行测试确认失败**

Run: `PYTHONPATH=src python -m unittest tests.test_system_prompt tests.test_skills -v`
Expected: FAIL

- [ ] **Step 3: 改提示词**

把 `prompts/system.md` 的 `## 出图` 章节（第 52-54 行）扩写，在既有一段之后追加：

```markdown
出图前先想清楚这张图要说明什么，再用 `styles` 选色板：连续数值分级用 `BLUES`/`GREENS`/`REDS`（越高越警示用 `REDS`），双向偏离用 `RD_BU`，类别用 `CATEGORY10`，土地利用现状用 `LAND_USE`（按地类名自动配语义色），DEM 高程用 `ELEVATION`，晕渲用 `HILLSHADE_GRAY`，只想要一张干净的示意图用 `BASE`。分级要传 `field`、`classes`（3–7）与 `method`（`EQUAL_INTERVAL` 或 `QUANTILE`）；类别要传 `field`。色板与数据类型不匹配、字段不存在或类型不对都会被拒绝并列出可用字段，按提示改即可。`labels` 传 `layer`、`field`、`size` 即可标注，栅格不能标注。

配色由预设色板决定，不要试图自己指定颜色 —— 你看不见导出的图，无法判断配色好坏。返回结果里的 `symbology` 段给出了实际断点与色值，汇报时要把它说清楚（分了哪几级、每级范围、是否抽样近似），不要只说"已出图"。`LAND_USE` 的 `unmatched` 列出了没匹配上语义色的地类名，必须一并告知用户。**换配色需要重建工程**（`output_name` 不能与已有成果重名），所以第一次就尽量选对色板。
```

- [ ] **Step 4: 改技能包**

`interpolation_surface_map.json` 第 19 行的 `build_layout` 步骤改为：

```json
    {"tool": "build_layout", "arguments": {"title": "{{params.title}}", "layers": ["{{steps.shade.output}}", "{{steps.contours.output}}"], "output_name": "surface_map", "styles": [{"layer": "{{steps.shade.output}}", "palette": "ELEVATION"}, {"layer": "{{steps.contours.output}}", "palette": "BASE"}]}, "note": "extent 省略时自动取图层范围并集；坐标系不一致会被拒绝；晕渲栅格用 ELEVATION，等高线用 BASE"},
```

`csv_points_to_density_map.json` 第 16 行改为：

```json
    {"tool": "build_layout", "arguments": {"title": "{{params.title}}", "layers": ["{{steps.density.output}}"], "output_name": "density_map", "styles": [{"layer": "{{steps.density.output}}", "palette": "REDS"}]}, "note": "密度越高越警示，用 REDS；可再加 points 图层配 BASE 叠加采样点"},
```

同时在两个技能包的 `pitfalls` 里各加一条：

```json
    "色板与数据类型不匹配会被拒绝：ELEVATION/HILLSHADE_GRAY 只给栅格，CATEGORY10/LAND_USE/BASE 只给矢量",
```

- [ ] **Step 5: 运行测试确认通过**

Run: `PYTHONPATH=src python -m unittest tests.test_system_prompt tests.test_skills -v`
Expected: PASS

- [ ] **Step 6: 确认提示词仍在预算内**

Run: `PYTHONPATH=src python -c "from arcgis_agent.deepseek import SYSTEM; print(len(SYSTEM))"`
Expected: 字符数仍远低于 8000 上限（改动前约 3709）。注意常量名是 `SYSTEM`（`deepseek.py:17`），不是 `SYSTEM_PROMPT`。

- [ ] **Step 7: 改文档**

- `docs/tools.md` 第 65 行的 `build_layout` 表格行补 `styles?`、`labels?` 两个参数与返回的 `symbology`/`labels` 段说明；"制图与出图约定"节（第 95 行起）新增一小节，写清 9 个色板、kind × 数据类型兼容矩阵、`classes` 3–7、唯一值上限 10、轮廓与字号固定不开放、换配色需重建工程。
- `docs/architecture.md` 补 `symbology.py` 的模块边界（不碰工程文件、不做 IO、只变换 CIM definition）与"前置校验"不变量。
- `CHANGELOG.md` 在最前面加 2026-11-06 条目：日期、具体行为变更（`build_layout` 新增 styles/labels；9 个预设色板；等间距与分位数分级；Arcade 标注；修正 `_add_title` 的颜色写法）、影响模块、验证方式（离线项数 + 真机冒烟 checks 数）、已知限制（栅格分级未做、标注避让为最小实现、色板观感未经制图专业评审、换配色需重建工程）。
- `README.md` / `README.en.md` 的出图能力说明同步。

- [ ] **Step 8: 跑全量离线套件**

Run: `PYTHONPATH=src python -m unittest discover -s tests`
Expected: 全绿，退出码 0

- [ ] **Step 9: 提交（需先征得用户同意）**

```bash
git add src/arcgis_agent/prompts/system.md src/arcgis_agent/skills/ docs/ CHANGELOG.md README.md README.en.md tests/
git commit -m "docs: 出图符号化与标注的提示词、技能包与文档同步"
```

---

## 自审记录

写完后对照规格逐条核过：

**规格覆盖**：§3.1 五个函数 → Task 1–3、5–7；§3.2 接入点 → Task 8；§3.3 前置校验不变量 → Task 8 Step 1 的两个残留断言；§4 色板表与兼容矩阵 → Task 1、3；§4 固定不开放的样式细节 → Task 1 的 `OUTLINES`/`MIN_CLASSES`/`MAX_CLASSES`/`MAX_UNIQUE_VALUES`；§5.1 schema → Task 4；§5.2 组件清单 → 每个 Task 的 Files 段一一对应；§6 数据流与 `symbology` 返回段 → Task 8；§7 九条校验 → 第 1、2、8 条在 Task 4（schema 层可判），第 3–7、9 条在 Task 3（需 `desc`）；§8.1 探测 → 已在写计划前执行完毕，结论回写规格；§8.2 离线测试 → Task 1–8；§8.3 真机冒烟 → Task 9；§8.4 文档与技能 → Task 10；§9 七条验收 → Task 4 Step 5（工具数）、Task 8 Step 5（向后兼容）、Task 9（只读性、零残留、人眼验收）。

**发现并修掉的问题**（都已就地改正，不留给执行者踩）：

1. `paint_symbol` 初稿把 arcpy 藏进模块级 `_ARCPY_HOLDER` 全局，与"arcpy 一律按参数传入"的仓库惯例冲突，且会破坏离线可测性 —— 已改为显式首参，测试调用形式同步为 `paint_symbol(StubArcpy(), …)`。
2. 分级断言里期望值写成 tuple，而 `CIMRGBColor.values` 是 list，`assertEqual` 会假失败 —— 已统一为 list。
3. `validate_style_spec` 初稿只对 `SIMPLE`/`RASTER_RAMP` 拒绝多余参数，却让唯一值静默忽略 `classes`/`method`，与"不静默忽略"原则自相矛盾 —— 已改为按 renderer kind 查禁用参数表，并补了对应测试。
4. `_build_layout` 初稿用 `described[style["layer"]]` 取 desc，未知图层会抛 `KeyError` 而非 `ValueError`，用户拿不到可操作的中文提示 —— 已加 `_resolve` 显式转成 `ValueError`。
5. 接线测试初稿靠 monkeypatch `symbology.apply_renderer` 断言调用序列，但 `cartography.py` 用 `from .symbology import …` 绑定了名字，补丁根本不生效 —— 已改为断言桩图层收到的 definition 内容（测行为而非调用序列）。
6. Task 10 初稿引用了并不存在的 `load_pack()` 与 `SYSTEM_PROMPT`；核实后实际 API 是 `skills.load_skills()`（返回 `{名: payload}`）与 `deepseek.SYSTEM` —— 已改正。

**类型一致性**：`plan_style` 产出的四种 plan 形态在 Task 5 的 Interfaces 段完整列出，Task 6 的 `RASTER_RAMP` 分支与 Task 8 的 `symbology` 返回段都按同一形状引用；`renderer_kind` 的返回值、`RENDERERS` 元组、`validate_style_spec` 的禁用参数表、`apply_renderer` 的分派分支四处字面量一致（`CLASS_BREAKS`/`UNIQUE_VALUE`/`SIMPLE`/`RASTER_RAMP`）。CIM 属性名与探测实测一致：`breaks`（非 `classBreaks`）、`groups → classes`（非直接 `classes`）、`colorizer`（非 `renderer`）、`values`（非 `red/green/blue`）。
