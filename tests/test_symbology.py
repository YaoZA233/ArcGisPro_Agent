"""符号化模块的离线测试。绝不 import arcpy：桩对象按参数传入。"""
from __future__ import annotations

import unittest

from arcgis_agent.symbology import (CLASSIFY_METHODS, KINDS, MAX_CLASSES, MAX_UNIQUE_VALUES,
                                    MIN_CLASSES, OUTLINES, PALETTE_NAMES, PALETTES, VALUE_CAP,
                                    build_classes, collect_categories, collect_values, hex_rgb,
                                    land_use_color, palette_colors, ramp_colors)

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


if __name__ == "__main__":
    unittest.main()
