"""Batch 2: spatial analysis tools. Enums, units and call forms below were verified against the
local ArcGIS Pro 3.5.2 ArcPy runtime (see docs/validation.md), not copied from documentation."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from arcgis_agent.catalog import TOOLS, approval_details, validate_call
from arcgis_agent.gis_tools import require_extension


class AnalysisContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_every_analysis_tool_writes_and_is_not_host_only(self):
        for name in ("kernel_density", "interpolate_surface", "zonal_statistics", "extract_by_mask",
                     "sample_raster_at_points", "polygon_to_raster", "terrain_derivatives", "contour_lines",
                     "summarize_statistics", "join_table", "union", "erase", "reshape_geometry"):
            self.assertIn(name, TOOLS)
            self.assertTrue(TOOLS[name].writes, name)
            self.assertFalse(TOOLS[name].host, name)

    def test_positive_numbers_reject_zero_negative_and_nan(self):
        validate_call("kernel_density", {"dataset": "pts", "output_name": "kd", "cell_size": 30, "search_radius": 1000})
        for bad in (0, -1, float("nan"), True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_call("kernel_density", {"dataset": "pts", "output_name": "kd", "cell_size": bad})
        with self.assertRaises(ValueError):
            validate_call("polygon_to_raster", {"dataset": "p", "value_field": "v", "cell_size": 0, "output_name": "r"})

    def test_cell_size_has_no_unit_suffix(self):
        # ArcPy rejects "300 Meters" for an Analysis Cell Size; only a bare number in map units works.
        for bad in ("300 Meters", "0.005"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_call("kernel_density", {"dataset": "pts", "output_name": "kd", "cell_size": bad})

    def test_interpolation_method_and_conditional_arguments(self):
        validate_call("interpolate_surface", {"dataset": "pts", "value_field": "z", "method": "IDW", "cell_size": 30, "output_name": "surf"})
        validate_call("interpolate_surface", {"dataset": "pts", "value_field": "z", "method": "KRIGING", "cell_size": 30,
                                              "variogram": "SPHERICAL", "output_name": "surf"})
        with self.assertRaises(ValueError):
            validate_call("interpolate_surface", {"dataset": "pts", "value_field": "z", "method": "SPLINE", "output_name": "surf"})
        with self.assertRaises(ValueError):
            validate_call("interpolate_surface", {"dataset": "pts", "value_field": "z", "method": "IDW", "cell_size": 30,
                                                  "variogram": "SPHERICAL", "output_name": "surf"})
        with self.assertRaises(ValueError):
            validate_call("interpolate_surface", {"dataset": "pts", "value_field": "z", "method": "KRIGING", "cell_size": 30,
                                                  "power": 2, "output_name": "surf"})

    def test_density_units_and_output_values(self):
        validate_call("kernel_density", {"dataset": "pts", "output_name": "kd", "cell_size": 30, "area_unit": "SQUARE_KILOMETERS",
                                         "output_values": "DENSITIES", "method": "GEODESIC"})
        validate_call("kernel_density", {"dataset": "pts", "output_name": "kd", "cell_size": 30, "output_values": "EXPECTED_COUNTS"})
        for bad in ("EXPECTED_COUNT", "SQUARE_MILES2", "HECTARE"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_call("kernel_density", {"dataset": "pts", "output_name": "kd", "cell_size": 30, "area_unit": bad})

    def test_zonal_statistic_enum_is_the_runtime_property_domain(self):
        for statistic in ("ALL", "MEAN", "MEDIAN", "MINIMUM", "MAXIMUM", "RANGE", "STD", "SUM", "MIN_MAX", "MEAN_STD", "MIN_MAX_MEAN"):
            with self.subTest(statistic=statistic):
                validate_call("zonal_statistics", {"zone_dataset": "zones", "zone_field": "id", "value_raster": "r.tif",
                                                   "statistic": statistic, "output_name": "zt"})
        # ZSAT has no plain COUNT and no AVERAGE; COUNT belongs to summarize_statistics.
        for bad in ("AVERAGE", "COUNT", "VARIETY"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_call("zonal_statistics", {"zone_dataset": "zones", "zone_field": "id", "value_raster": "r.tif",
                                                   "statistic": bad, "output_name": "zt"})

    def test_terrain_derivatives_are_raster_only(self):
        validate_call("terrain_derivatives", {"dataset": "dem.tif", "derivative": "SLOPE", "output_name": "slope"})
        validate_call("terrain_derivatives", {"dataset": "dem.tif", "derivative": "ASPECT", "z_unit": "METER", "output_name": "a"})
        validate_call("terrain_derivatives", {"dataset": "dem.tif", "derivative": "SLOPE", "slope_measurement": "PERCENT_RISE", "output_name": "s"})
        for arguments in ({"dataset": "dem.tif", "derivative": "CONTOUR", "output_name": "c"},
                          {"dataset": "dem.tif", "derivative": "SLOPE", "z_unit": "DEGREES", "output_name": "s"},
                          {"dataset": "dem.tif", "derivative": "ASPECT", "slope_measurement": "DEGREE", "output_name": "a"}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                validate_call("terrain_derivatives", arguments)
        validate_call("contour_lines", {"dataset": "dem.tif", "interval": 10, "output_name": "c"})
        validate_call("contour_lines", {"dataset": "dem.tif", "interval": 10, "base_value": 5, "output_name": "c"})
        for bad in ({"dataset": "dem.tif", "interval": 0, "output_name": "c"},
                    {"dataset": "dem.tif", "interval": "10 20", "output_name": "c"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_call("contour_lines", bad)

    def test_stats_array_structure(self):
        validate_call("summarize_statistics", {"dataset": "t", "stats": [{"field": "a", "statistic": "MEAN"}], "output_name": "summary"})
        validate_call("summarize_statistics", {"dataset": "t", "stats": [{"field": "a", "statistic": "COUNT"},
                                                                        {"field": "b", "statistic": "CONCATENATE"}],
                                               "case_field": "grp", "output_name": "summary"})
        bad = [{"dataset": "t", "output_name": "s", "stats": []},
               {"dataset": "t", "output_name": "s", "stats": [{"field": "a"}]},
               {"dataset": "t", "output_name": "s", "stats": [{"field": "a", "statistic": "AVG"}]},
               {"dataset": "t", "output_name": "s", "stats": [{"field": "a", "statistic": "AVERAGE"}]},
               {"dataset": "t", "output_name": "s", "stats": [{"field": "a", "statistic": "MEAN", "extra": 1}]},
               {"dataset": "t", "output_name": "s", "stats": [{"field": "a", "statistic": "MEAN"}] * 9}]
        for arguments in bad:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                validate_call("summarize_statistics", arguments)

    def test_reshape_geometry_conditional_requirements(self):
        validate_call("reshape_geometry", {"dataset": "p", "operation": "CENTROID", "output_name": "c"})
        validate_call("reshape_geometry", {"dataset": "p", "operation": "SIMPLIFY", "tolerance": "50 Meters", "output_name": "s"})
        validate_call("reshape_geometry", {"dataset": "p", "operation": "MIN_BOUNDING", "geometry_type": "CONVEX_HULL", "output_name": "m"})
        # ArcPy refuses a percent tolerance for SimplifyPolygon, so only linear units are offered.
        missing = [{"dataset": "p", "operation": "SIMPLIFY", "output_name": "s"},
                   {"dataset": "p", "operation": "SIMPLIFY", "tolerance": "10 Percent", "output_name": "s"},
                   {"dataset": "p", "operation": "SIMPLIFY", "tolerance": "50 Degrees", "output_name": "s"},
                   {"dataset": "p", "operation": "SIMPLIFY", "tolerance": "0 Meters", "output_name": "s"},
                   {"dataset": "p", "operation": "MIN_BOUNDING", "output_name": "m"},
                   {"dataset": "p", "operation": "MIN_BOUNDING", "geometry_type": "OCTAGON", "output_name": "m"},
                   {"dataset": "p", "operation": "CENTROID", "geometry_type": "CIRCLE", "output_name": "c"}]
        for arguments in missing:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                validate_call("reshape_geometry", arguments)

    def test_join_and_overlay_schemas(self):
        base = {"dataset": "parcels", "join_dataset": "owners.dbf", "target_field": "id",
                "join_field": "owner_id", "fields": ["name"]}
        validate_call("join_table", {**base, "output_name": "j"})
        # JoinField has no join_type argument at all (its 6th parameter is fm_option), so the tool must not offer one.
        with self.assertRaises(ValueError):
            validate_call("join_table", {**base, "join_type": "KEEP_ALL", "output_name": "j"})
        with self.assertRaises(ValueError):
            validate_call("join_table", {**{k: v for k, v in base.items() if k != "fields"}, "output_name": "j"})
        validate_call("union", {"datasets": ["a", "b"], "output_name": "u"})
        validate_call("union", {"datasets": ["a", "b"], "keep_attributes": "NO_FID", "output_name": "u"})
        for bad in ("ALL_ATTRIBUTES", "KEEP_ALL"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_call("union", {"datasets": ["a", "b"], "keep_attributes": bad, "output_name": "u"})
        with self.assertRaises(ValueError):
            validate_call("union", {"datasets": ["a"], "output_name": "u"})
        validate_call("erase", {"dataset": "a", "erase_dataset": "b", "output_name": "e"})
        with self.assertRaises(ValueError):
            validate_call("erase", {"dataset": "a", "output_name": "e"})

    def test_sample_raster_array(self):
        validate_call("sample_raster_at_points", {"dataset": "pts", "rasters": ["a.tif", "b.tif"], "output_name": "s"})
        validate_call("sample_raster_at_points", {"dataset": "pts", "rasters": ["a.tif"], "resampling": "NEAREST", "output_name": "s"})
        for arguments in ({"dataset": "pts", "rasters": [], "output_name": "s"},
                          {"dataset": "pts", "rasters": ["a.tif"] * 9, "output_name": "s"},
                          {"dataset": "pts", "rasters": ["a.tif", "a.tif"], "output_name": "s"},
                          {"dataset": "pts", "rasters": ["a.tif"], "resampling": "ALL_CELLS", "output_name": "s"}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                validate_call("sample_raster_at_points", arguments)

    def test_extract_by_mask_area(self):
        validate_call("extract_by_mask", {"dataset": "r", "mask_dataset": "m", "output_name": "e"})
        validate_call("extract_by_mask", {"dataset": "r", "mask_dataset": "m", "extraction_area": "OUTSIDE", "output_name": "e"})
        with self.assertRaises(ValueError):
            validate_call("extract_by_mask", {"dataset": "r", "mask_dataset": "m", "extraction_area": "INTERSECT", "output_name": "e"})

    def test_raster_and_table_outputs_in_approval_preview(self):
        raster_cases = [("kernel_density", {"dataset": "p", "output_name": "kd"}),
                        ("interpolate_surface", {"dataset": "p", "value_field": "z", "method": "IDW", "output_name": "s"}),
                        ("extract_by_mask", {"dataset": "r", "mask_dataset": "m", "output_name": "e"}),
                        ("polygon_to_raster", {"dataset": "p", "value_field": "v", "cell_size": 30, "output_name": "r"}),
                        ("terrain_derivatives", {"dataset": "d", "derivative": "SLOPE", "output_name": "sl"})]
        for name, arguments in raster_cases:
            details = approval_details(name, arguments, self.root)
            self.assertTrue(details["writes"])
            self.assertEqual(details["expected_outputs"], [str(self.root.resolve() / (arguments["output_name"] + ".tif"))])
        gdb_cases = [("zonal_statistics", {"zone_dataset": "z", "zone_field": "id", "value_raster": "r", "statistic": "MEAN", "output_name": "zt"}),
                     ("summarize_statistics", {"dataset": "t", "stats": [{"field": "a", "statistic": "MEAN"}], "output_name": "st"}),
                     ("sample_raster_at_points", {"dataset": "p", "rasters": ["r.tif"], "output_name": "sm"}),
                     ("contour_lines", {"dataset": "d", "interval": 10, "output_name": "c"}),
                     ("reshape_geometry", {"dataset": "p", "operation": "SIMPLIFY", "tolerance": "10 Meters", "output_name": "rs"})]
        for name, arguments in gdb_cases:
            details = approval_details(name, arguments, self.root)
            self.assertEqual(details["expected_outputs"], [str(self.root.resolve() / "results.gdb" / arguments["output_name"])], name)

    def test_model_schema_lists_new_tools(self):
        from arcgis_agent.deepseek import tool_schemas

        names = {d["function"]["name"] for d in tool_schemas()}
        self.assertIn("zonal_statistics", names)
        self.assertNotIn("run_python", names)


class ExtensionGateTests(unittest.TestCase):
    class FakeArcpy:
        def __init__(self, status, checkout_result="CheckedOut"):
            self.status, self.checkout_result, self.checked_out = status, checkout_result, []

        def CheckExtension(self, extension):
            return self.status

        def CheckOutExtension(self, extension):
            self.checked_out.append(extension)
            return self.checkout_result

    def gate(self, **kwargs):
        fake = self.FakeArcpy(**kwargs)
        require_extension(fake, "Spatial")
        return fake

    def test_available_checks_out(self):
        self.assertEqual(self.gate(status="Available").checked_out, ["Spatial"])

    def test_already_checked_out_is_fine(self):
        self.assertEqual(self.gate(status="AlreadyCheckedOut").checked_out, [])

    def test_refused_checkout_raises(self):
        with self.assertRaisesRegex(ValueError, "Spatial"):
            self.gate(status="Available", checkout_result="Unlicensed")

    def test_unavailable_raises_with_name_and_status(self):
        with self.assertRaisesRegex(ValueError, "Spatial"):
            self.gate(status="Unavailable")
        with self.assertRaisesRegex(ValueError, "NotAvailable"):
            self.gate(status="NotAvailable")

    def test_no_silent_fallback_when_extension_missing(self):
        # A blocked extension must fail before any geoprocessing runs.
        fake = self.FakeArcpy(status="Unavailable")
        with self.assertRaises(ValueError):
            require_extension(fake, "Spatial")
        self.assertEqual(fake.checked_out, [])


if __name__ == "__main__":
    unittest.main()
