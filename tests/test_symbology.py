"""符号化模块的离线测试。绝不 import arcpy：桩对象按参数传入。"""
from __future__ import annotations

import unittest

from arcgis_agent.catalog import LAYERS, TOOLS, validate_call
from arcgis_agent.symbology import (CLASSIFY_METHODS, KINDS, LABEL_SIZES, MAX_CLASSES,
                                    MAX_UNIQUE_VALUES, MIN_CLASSES, OUTLINES, PALETTE_NAMES,
                                    PALETTES, RENDERERS, VALUE_CAP, build_classes,
                                    collect_categories, collect_values, hex_rgb, land_use_color,
                                    palette_colors, ramp_colors, renderer_kind,
                                    validate_label_spec, validate_style_spec)

HEX = "#0123456789abcdefABCDEF"


class PaletteTableTests(unittest.TestCase):
    def test_names_are_sorted_and_match_table(self):
        # PALETTES 本身按键排序，使错误消息里列举的色板顺序与 schema enum 都是确定的。
        self.assertEqual(list(PALETTES), sorted(PALETTES))
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
        # style 路径带 palette：缺 field 的消息必须与 brief 逐字节一致 —— 钉住守卫的真分支。
        self.assertEqual("色板 BLUES 走分级色彩，必须提供 field 参数；"
                         "该数据集可用的字段有：COUNT, VALUE。", str(caught.exception))

    def test_unknown_method_is_refused_with_the_allowed_list(self):
        # build_classes 把未知 method 静默当成 QUANTILE；只有这道闸门拦住它。
        with self.assertRaises(ValueError) as caught:
            validate_style_spec({"layer": "L", "palette": "BLUES", "field": "VALUE",
                                 "classes": 5, "method": "NATURAL_BREAKS"}, polygon_desc())
        message = str(caught.exception)
        self.assertIn("EQUAL_INTERVAL", message)
        self.assertIn("QUANTILE", message)
        self.assertIn("NATURAL_BREAKS", message)

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

    def test_absent_field_key_raises_value_error_not_keyerror(self):
        # 标注 spec 没有 palette 键；缺 field 也必须报中文 ValueError 而非 KeyError('palette')
        # —— 钉住 _check_field 的守卫（symbology.py 的 subject 分支），删掉守卫本测试即红。
        with self.assertRaises(ValueError) as caught:
            validate_label_spec({"layer": "L"}, polygon_desc())
        self.assertIn("field", str(caught.exception))


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
            # 与 LAYERS 的上限互锁：一个图层最多一条样式、一条标注，上限必须同涨同跌。
            self.assertEqual(properties[name]["maxItems"], LAYERS["maxItems"])

    def test_style_enum_matches_the_palette_table(self):
        # schema 常量与实现常量互锁：色板表改了而 schema 没改，这里就会红。
        schema = TOOLS["build_layout"].properties["styles"]["items"]["properties"]
        self.assertEqual(sorted(schema["palette"]["enum"]), PALETTE_NAMES)
        self.assertEqual(sorted(schema["method"]["enum"]), sorted(CLASSIFY_METHODS))
        self.assertEqual(schema["classes"]["minimum"], MIN_CLASSES)
        self.assertEqual(schema["classes"]["maximum"], MAX_CLASSES)

    def test_label_size_range_matches_the_validator(self):
        # schema 上下限与 validate_label_spec 的运行时范围同源于 symbology.LABEL_SIZES：
        # 任何一边与常量脱钩（schema 重新写死字面量、或校验器私自放宽范围），这里即红。
        schema = TOOLS["build_layout"].properties["labels"]["items"]["properties"]
        self.assertEqual(schema["size"]["minimum"], LABEL_SIZES[0])
        self.assertEqual(schema["size"]["maximum"], LABEL_SIZES[1])
        # 规格 §5.1 把模型可见的字号契约定为 6–24 pt：改 LABEL_SIZES 是契约变更，必须显式
        # 过审（护栏写法镜像上面 PaletteTableTests 对 len(PALETTE_NAMES) == 9 的钉死）。
        self.assertEqual(tuple(LABEL_SIZES), (6, 24))
        for size in LABEL_SIZES:
            validate_label_spec({"layer": "L", "field": "NAME", "size": size}, polygon_desc())
        for size in (LABEL_SIZES[0] - 1, LABEL_SIZES[1] + 1):
            with self.assertRaises(ValueError) as caught:
                validate_label_spec({"layer": "L", "field": "NAME", "size": size}, polygon_desc())
            self.assertIn(str(size), str(caught.exception))

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
        # 报错必须点名出错的参数（styles），而不是笼统的 "styles/labels"。
        self.assertIn("styles 里的 layer", str(caught.exception))
        self.assertIn("C:/d.gdb/slope", str(caught.exception))

    def test_label_targeting_an_unknown_layer_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            validate_call("build_layout", base_layout(
                labels=[{"layer": "C:/d.gdb/other", "field": "NAME"}]))
        self.assertIn("labels 里的 layer", str(caught.exception))
        self.assertIn("layers", str(caught.exception))

    def test_styling_the_same_layer_twice_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            validate_call("build_layout", base_layout(
                styles=[{"layer": "C:/d.gdb/slope", "palette": "BLUES"},
                        {"layer": "C:/d.gdb/slope", "palette": "REDS"}]))
        self.assertIn("两次", str(caught.exception))

    def test_labelling_the_same_layer_twice_is_refused(self):
        # 两条 labels 指向同一图层：字段不同所以 uniqueItems 拦不住，schema 也合法；
        # 若不手写这条检查，下游按图层建字典会静默丢弃其一，却照样报告成功。
        with self.assertRaises(ValueError) as caught:
            validate_call("build_layout", base_layout(
                labels=[{"layer": "C:/d.gdb/slope", "field": "NAME"},
                        {"layer": "C:/d.gdb/slope", "field": "POP"}]))
        self.assertIn("两次", str(caught.exception))
        self.assertIn("labels", str(caught.exception))

    def test_too_many_styles_is_refused_by_the_schema(self):
        # layers 用满 12 个（合法），styles 给 13 条（非法）：这样触发的是 styles 自己的
        # maxItems。若 layers 也给 13 个，先失败的会是 layers，这条测试就测错了约束。
        # catalog._validate 的 label 是点路径，数组超限报 "invalid number of items"。
        layers = [f"C:/d.gdb/l{i}" for i in range(12)]
        styles = [{"layer": layers[index % 12], "palette": "BASE"} for index in range(13)]
        with self.assertRaises(ValueError) as caught:
            validate_call("build_layout", base_layout(layers=layers, styles=styles))
        self.assertIn("build_layout.styles", str(caught.exception))
        self.assertIn("invalid number of items", str(caught.exception))

    def test_too_many_labels_is_refused_by_the_schema(self):
        layers = [f"C:/d.gdb/l{i}" for i in range(12)]
        labels = [{"layer": layers[index % 12], "field": "NAME"} for index in range(13)]
        with self.assertRaises(ValueError) as caught:
            validate_call("build_layout", base_layout(layers=layers, labels=labels))
        self.assertIn("build_layout.labels", str(caught.exception))

    def test_empty_styles_and_labels_are_allowed(self):
        validate_call("build_layout", base_layout(styles=[], labels=[]))

    def test_existing_scale_bar_exclusion_still_holds(self):
        with self.assertRaises(ValueError):
            validate_call("build_layout", base_layout(
                elements=["SCALE_BAR", "DUAL_SCALE_BAR"]))


# ---------------------------------------------------------------------------
# Task 5: CIM 助手与矢量 renderer。
# 桩类名一律用真机探测证明的 CIM* 大写名（probe_cim2 的 arcpy.cim 目录）：
# paint_symbol 按 type(node).__name__ 分派，桩若叫 Cim* 就永远匹配不上，
# 测试会绿得毫无意义。
# ---------------------------------------------------------------------------
from arcgis_agent.symbology import CATEGORY10
from arcgis_agent.symbology import apply_renderer, paint_symbol, plan_style, rgb_color


class CIMRGBColor:
    def __init__(self):
        self.values = None


class CIMSolidFill:
    def __init__(self):
        self.color = None


class CIMSolidStroke:
    def __init__(self):
        self.color = None
        self.width = 0.7


class CIMVectorMarker:
    def __init__(self):
        self.size = 10.0
        self.markerGraphics = []


class CIMPolygonSymbol:
    def __init__(self, layers=None):
        self.symbolLayers = layers if layers is not None else [CIMSolidStroke(), CIMSolidFill()]


class CIMSymbolReference:
    def __init__(self, symbol=None):
        self.symbol = symbol if symbol is not None else CIMPolygonSymbol()


class CIMClassBreak:
    def __init__(self):
        self.upperBound = 0
        self.label = ""
        self.symbol = None


class CIMClassBreaksRenderer:
    def __init__(self):
        self.field = ""
        self.classBreakType = ""
        self.minimumBreak = 0
        self.breaks = []


class CIMUniqueValue:
    def __init__(self):
        self.fieldValues = []


class CIMUniqueValueClass:
    def __init__(self):
        self.values = []
        self.label = ""
        self.symbol = None
        self.visible = False
        self.editable = False


class CIMUniqueValueGroup:
    def __init__(self):
        self.classes = []
        self.heading = ""


class CIMUniqueValueRenderer:
    def __init__(self):
        self.fields = []
        self.groups = []
        self.useDefaultSymbol = False
        self.defaultSymbol = None
        self.defaultLabel = ""


class StubCim:
    CIMRGBColor = CIMRGBColor
    CIMClassBreak = CIMClassBreak
    CIMClassBreaksRenderer = CIMClassBreaksRenderer
    CIMUniqueValue = CIMUniqueValue
    CIMUniqueValueClass = CIMUniqueValueClass
    CIMUniqueValueGroup = CIMUniqueValueGroup
    CIMUniqueValueRenderer = CIMUniqueValueRenderer


class StubArcpy:
    """只有 symbology 会碰的那一小截表面；真实构造形态由真机冒烟证明。"""
    cim = StubCim()


class StubRenderer:
    def __init__(self):
        self.symbol = CIMSymbolReference()


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
        if isinstance(node, CIMSolidFill):
            fills.append(node.color.values)
        elif isinstance(node, CIMSolidStroke):
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
        reference = CIMSymbolReference()
        changed = paint_symbol(StubArcpy(), reference, fill="#08519c", stroke="#FFFFFF", width=0.2)
        fills, strokes = prototype_colors(reference)
        self.assertEqual(fills, [[8, 81, 156, 100]])
        self.assertEqual(strokes, [([255, 255, 255, 100], 0.2)])
        self.assertEqual(changed, {"fill": 1, "stroke": 1, "width": 1, "size": 0})

    def test_fill_only_leaves_the_stroke_alone(self):
        reference = CIMSymbolReference()
        original = reference.symbol.symbolLayers[0].color
        paint_symbol(StubArcpy(), reference, fill="#3182bd")
        self.assertIs(reference.symbol.symbolLayers[0].color, original)

    def test_marker_size_is_set_for_points(self):
        marker = CIMVectorMarker()
        graphic_symbol = CIMPolygonSymbol()
        marker.markerGraphics = [type("G", (), {"symbol": graphic_symbol})()]
        reference = CIMSymbolReference(marker)
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
        self.assertIsInstance(renderer, CIMClassBreaksRenderer)
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
        self.assertIsInstance(renderer, CIMUniqueValueRenderer)
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
        definition.renderer.symbol = CIMSymbolReference(
            type("L", (), {"symbolLayers": [CIMSolidStroke()]})())
        apply_renderer(StubArcpy(), definition, {
            "renderer": "SIMPLE", "palette": "BASE", "geometry": "Polyline",
            "fill": None, "outline": "#4D4D4D", "outline_width": 1.2})
        _, strokes = prototype_colors(definition.renderer.symbol)
        self.assertEqual(strokes, [([77, 77, 77, 100], 1.2)])

    def test_point_sets_marker_size(self):
        marker = CIMVectorMarker()
        marker.markerGraphics = [type("G", (), {"symbol": CIMPolygonSymbol()})()]
        definition = StubDefinition()
        definition.renderer.symbol = CIMSymbolReference(marker)
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
        # total 必须真的超过 VALUE_CAP 才会走抽样分支：桩只给 50 行但谎报总量，
        # collect_values 会按 stride 抽取，这正是要覆盖的路径。
        # 若这里写成 total=50，测试会绿但抽样代码一行都没跑到。
        fake = FakeArcpy([(float(i),) for i in range(50)], total=VALUE_CAP * 2 + 7)
        fake.cim = StubCim()
        plan = plan_style(fake, {"layer": "L", "palette": "BLUES", "field": "VALUE",
                                 "classes": 3}, "L", polygon_desc())
        self.assertTrue(plan["sampled"])
        self.assertEqual(plan["value_count"], VALUE_CAP * 2 + 7)
        self.assertEqual(len(plan["bounds"]), 3)


# ---------------------------------------------------------------------------
# Task 6: 栅格 colorizer 多段色带。
# 铁律 3：CIMRasterLayer.renderer 恒为 None，arcpy.cim 里没有任何 CIMRaster*Renderer，
# 栅格上色只能改 definition.colorizer.colorRamp（CIMMultipartColorRamp，
# 7 档色 → 6 段 CIMLinearContinuousColorRamp，相邻档用 fromColor/toColor 相接）。
# ---------------------------------------------------------------------------
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


if __name__ == "__main__":
    unittest.main()
