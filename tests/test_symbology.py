"""符号化模块的离线测试。绝不 import arcpy：桩对象按参数传入。"""
from __future__ import annotations

import unittest

from arcgis_agent.symbology import (CLASSIFY_METHODS, KINDS, MAX_CLASSES, MAX_UNIQUE_VALUES,
                                    MIN_CLASSES, OUTLINES, PALETTE_NAMES, PALETTES, hex_rgb,
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


if __name__ == "__main__":
    unittest.main()
