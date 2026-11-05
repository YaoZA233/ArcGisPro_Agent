"""Batch 4: query/selection expansion, structured verification, field calculation,
exports and raster/hydrology tools. Enums and call forms below were verified against
the local ArcGIS Pro 3.5.2 ArcPy runtime (see docs/validation.md)."""
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from arcgis_agent.catalog import TOOLS, approval_details, validate_call, validate_expression
from arcgis_agent.gis_tools import _eval_expression, _expression_tree, _referenced_fields, where_clause
from arcgis_agent.hints import hint_for


def fake_arcpy(field_type="String", length=100):
    field = SimpleNamespace(name="name", type=field_type, length=length, isNullable=True)
    return SimpleNamespace(ListFields=lambda dataset: [field],
                           AddFieldDelimiters=lambda dataset, name: '"' + name + '"')


class CompositeFilterTests(unittest.TestCase):
    def test_single_condition_still_validates(self):
        validate_call("select_features", {"dataset": "x", "filter": {"field": "a", "operator": "EQ", "value": 1},
                                          "output_name": "out"})
        validate_call("read_rows", {"dataset": "x", "filter": {"field": "a", "operator": "IS_NULL", "value": None}})

    def test_compound_all_and_any(self):
        leaf_a = {"field": "a", "operator": "GE", "value": 10}
        leaf_b = {"field": "b", "operator": "EQ", "value": "A"}
        for key in ("all", "any"):
            validate_call("select_features", {"dataset": "x", "filter": {key: [leaf_a, leaf_b]}, "output_name": "out"})
        for bad in ({"all": [leaf_a], "any": [leaf_b]},
                    {"all": [leaf_a], "field": "a", "operator": "EQ", "value": 1},
                    {"field": "a", "operator": "EQ", "value": 1, "any": [leaf_b]},
                    {"all": []},
                    {"all": [leaf_a] * 9}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_call("select_features", {"dataset": "x", "filter": bad, "output_name": "out"})

    def test_in_and_text_operators(self):
        validate_call("select_features", {"dataset": "x", "filter": {"field": "a", "operator": "IN", "values": [1, 2]},
                                          "output_name": "out"})
        validate_call("read_rows", {"dataset": "x", "filter": {"field": "name", "operator": "CONTAINS", "value": "学校"}})
        for bad in ({"field": "a", "operator": "IN", "value": 1},
                    {"field": "a", "operator": "IN", "values": [1, None]},
                    {"field": "a", "operator": "IN"},
                    {"field": "a", "operator": "CONTAINS", "value": 5},
                    {"field": "a", "operator": "STARTS_WITH", "values": ["a"]},
                    {"field": "a", "operator": "IS_NULL", "values": [1]},
                    {"field": "a", "operator": "EQ", "values": [1]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_call("read_rows", {"dataset": "x", "filter": bad})

    def test_where_clause_builds_compound_sql(self):
        arcpy = fake_arcpy()
        clause = where_clause(arcpy, "x", {"all": [{"field": "name", "operator": "EQ", "value": "A"},
                                                  {"field": "name", "operator": "GT", "value": "B"}]})
        self.assertEqual(clause, '("name" = \'A\') AND ("name" > \'B\')')
        clause = where_clause(arcpy, "x", {"any": [{"field": "name", "operator": "EQ", "value": "A"},
                                                   {"field": "name", "operator": "IN", "values": ["C", "D"]}]})
        self.assertEqual(clause, '("name" = \'A\') OR ("name" IN (\'C\',\'D\'))')

    def test_text_operators_use_like_with_escaped_quotes(self):
        arcpy = fake_arcpy()
        clause = where_clause(arcpy, "x", {"field": "name", "operator": "CONTAINS", "value": "O'Brien"})
        self.assertEqual(clause, "\"name\" LIKE '%O''Brien%'")
        self.assertEqual(where_clause(arcpy, "x", {"field": "name", "operator": "STARTS_WITH", "value": "路"}),
                         "\"name\" LIKE '路%'")
        self.assertEqual(where_clause(arcpy, "x", {"field": "name", "operator": "NOT_CONTAINS", "value": "x"}),
                         "\"name\" NOT LIKE '%x%'")

    def test_in_rejects_nulls_and_non_matching_types(self):
        arcpy = SimpleNamespace(ListFields=lambda dataset: [SimpleNamespace(name="num", type="Integer", length=4, isNullable=True)],
                                AddFieldDelimiters=lambda dataset, name: '"' + name + '"')
        with self.assertRaises(ValueError):
            where_clause(arcpy, "x", {"field": "num", "operator": "IN", "values": [1, "a"]})
        with self.assertRaises(ValueError):
            where_clause(arcpy, "x", {"field": "num", "operator": "NOT_IN", "values": [None, 1]})


class SelectionAndProximityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_new_tools_registered_non_host_and_writes(self):
        expected_writes = {"select_by_location": True, "near_analysis": True, "create_thiessen_polygons": True,
                          "calculate_field": True, "define_projection": True, "export_table_csv": True,
                          "export_features": True, "verify_dataset": False, "reclassify": True, "hillshade": True,
                          "resample": True, "project_raster": True, "fill": True, "flow_direction": True,
                          "flow_accumulation": True}
        for name, writes in expected_writes.items():
            self.assertIn(name, TOOLS)
            self.assertEqual(TOOLS[name].writes, writes, name)
            self.assertFalse(TOOLS[name].host, name)

    def test_select_by_location_distance_rules(self):
        validate_call("select_by_location", {"dataset": "a", "select_dataset": "b", "overlap_type": "INTERSECT",
                                            "output_name": "out"})
        validate_call("select_by_location", {"dataset": "a", "select_dataset": "b",
                                             "overlap_type": "WITHIN_A_DISTANCE", "search_distance": "500 Meters",
                                             "output_name": "out"})
        for bad in ({"overlap_type": "WITHIN_A_DISTANCE", "output_name": "out"},
                    {"overlap_type": "INTERSECT", "search_distance": "500 Meters", "output_name": "out"},
                    {"overlap_type": "WITHIN_A_DISTANCE", "search_distance": "500", "output_name": "out"},
                    {"overlap_type": "WITHIN_A_DISTANCE", "search_distance": "-2 Meters", "output_name": "out"},
                    {"overlap_type": "SHARES_A_LINE_SEGMENT_WITH", "output_name": "out"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_call("select_by_location", {"dataset": "a", "select_dataset": "b", "output_name": "out", **bad})

    def test_near_and_thiessen_schemas(self):
        validate_call("near_analysis", {"dataset": "a", "near_dataset": "b", "output_name": "out"})
        validate_call("near_analysis", {"dataset": "a", "near_dataset": "b", "search_radius": "10 Kilometers",
                                        "method": "GEODESIC", "output_name": "out"})
        with self.assertRaises(ValueError):
            validate_call("near_analysis", {"dataset": "a", "near_dataset": "b", "search_radius": "10", "output_name": "out"})
        validate_call("create_thiessen_polygons", {"dataset": "pts", "output_name": "out"})
        validate_call("create_thiessen_polygons", {"dataset": "pts", "fields_to_copy": "ALL", "output_name": "out"})
        with self.assertRaises(ValueError):
            validate_call("create_thiessen_polygons", {"dataset": "pts", "fields_to_copy": "SOME", "output_name": "out"})


class FieldCalculationTests(unittest.TestCase):
    def test_expression_grammar(self):
        for good in ("!price! * 1.05 + 2", "(!a! + !b!) / 2", "-!x! ** 2", "!a! // 3 % 2"):
            validate_expression(good)
        for bad in ("__import__('os')", "exec('x')", "!a!.upper()", "len(!a!)", "!a! if !a! else 1", "'text'",
                    "!a!!b!", "!a!; pass", "!a! and !b!"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_expression(bad)

    def test_expression_references_and_self_reference_rejected(self):
        self.assertEqual(_referenced_fields("!a! + !b! * 2"), ["a", "b"])
        with self.assertRaises(ValueError):
            validate_call("calculate_field", {"dataset": "x", "field": "a", "expression": "!a! + 1", "output_name": "out"})
        validate_call("calculate_field", {"dataset": "x", "field": "c", "expression": "!a! + !b!", "output_name": "out"})

    def test_evaluator_computes_arithmetic_and_rejects_bad_values(self):
        tree = _expression_tree("!a! * 2 + !b!")
        self.assertEqual(_eval_expression(tree, {"a": 3, "b": 0.5}), 6.5)
        self.assertEqual(_eval_expression(_expression_tree("-!x! ** 2"), {"x": 3}), -9)
        with self.assertRaises(ValueError):
            _eval_expression(tree, {"a": None, "b": 1})
        with self.assertRaises(ValueError):
            _eval_expression(tree, {"a": "text", "b": 1})


class ExportAndVerificationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_export_schemas_and_approval_paths(self):
        validate_call("export_table_csv", {"dataset": "x", "output_name": "t"})
        validate_call("export_table_csv", {"dataset": "x", "fields": ["a"], "filter": {"field": "a", "operator": "EQ", "value": 1},
                                           "output_name": "t"})
        validate_call("export_features", {"dataset": "x", "format": "GEOJSON", "output_name": "f"})
        validate_call("define_projection", {"dataset": "x", "wkid": 4326, "output_name": "d"})
        details = approval_details("export_table_csv", {"dataset": "x", "output_name": "t"}, self.root)
        self.assertEqual(details["expected_outputs"], [str(self.root.resolve() / "t.csv")])
        details = approval_details("export_features", {"dataset": "x", "format": "GEOJSON", "output_name": "f"}, self.root)
        self.assertEqual(details["expected_outputs"], [str(self.root.resolve() / "f.geojson")])
        details = approval_details("export_features", {"dataset": "x", "format": "SHAPEFILE", "output_name": "f"}, self.root)
        self.assertEqual(details["expected_outputs"], [str(self.root.resolve() / "f.shp")])
        with self.assertRaises(ValueError):
            validate_call("export_features", {"dataset": "x", "format": "GPKG", "output_name": "f"})

    def test_verify_dataset_schema(self):
        validate_call("verify_dataset", {"dataset": "x"})
        validate_call("verify_dataset", {"dataset": "x", "min_count": 1, "max_count": 100, "fields": ["a", "b"],
                                         "non_null_fields": ["a"], "unique_fields": ["a"], "wkid": 4326,
                                         "extent_within": {"xmin": 0, "ymin": 0, "xmax": 10, "ymax": 10}})
        with self.assertRaises(ValueError):
            validate_call("verify_dataset", {"dataset": "x", "extent_within": {"xmin": 0, "ymin": 0, "xmax": 0, "ymax": 10}})
        with self.assertRaises(ValueError):
            validate_call("verify_dataset", {"dataset": "x", "min_count": -1})

    def test_new_raster_tools_in_approval_preview(self):
        cases = [("reclassify", {"dataset": "r", "remap": [{"start": 0, "end": 10, "new_value": 1}], "output_name": "rc"}),
                 ("hillshade", {"dataset": "r", "output_name": "hs"}),
                 ("resample", {"dataset": "r", "cell_size": 30, "output_name": "rs"}),
                 ("project_raster", {"dataset": "r", "target_wkid": 3857, "output_name": "pr"}),
                 ("fill", {"dataset": "r", "output_name": "fl"}),
                 ("flow_direction", {"dataset": "r", "output_name": "fd"}),
                 ("flow_accumulation", {"dataset": "r", "output_name": "fa"})]
        for name, arguments in cases:
            details = approval_details(name, arguments, self.root)
            self.assertEqual(details["expected_outputs"], [str(self.root.resolve() / (arguments["output_name"] + ".tif"))], name)


class RasterHydrologyTests(unittest.TestCase):
    def test_reclassify_schema(self):
        validate_call("reclassify", {"dataset": "r", "remap": [{"start": 0, "end": 50, "new_value": 1},
                                                               {"start": 50, "end": 100, "new_value": 2}],
                                     "output_name": "out"})
        for bad in ([{"start": 10, "end": 0, "new_value": 1}],
                    [{"start": 0, "end": 10}],
                    [{"start": 0, "end": 10, "new_value": 1}] * 33,
                    [{"start": 0, "end": 10, "new_value": "1"}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_call("reclassify", {"dataset": "r", "remap": bad, "output_name": "out"})
        validate_call("reclassify", {"dataset": "r", "remap": [{"start": 0, "end": 10, "new_value": 1}],
                                     "map_nodata_to": 0, "output_name": "out"})

    def test_hillshade_ranges_and_enums(self):
        validate_call("hillshade", {"dataset": "r", "azimuth": 315, "altitude": 45, "shadows": "SHADOWS",
                                    "z_factor": 1, "output_name": "hs"})
        for bad in ({"azimuth": 400}, {"altitude": 91}, {"shadows": "SHADOW"}, {"z_factor": 0}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_call("hillshade", {"dataset": "r", "output_name": "hs", **bad})

    def test_resample_and_project_raster(self):
        validate_call("resample", {"dataset": "r", "cell_size": 30, "output_name": "rs"})
        validate_call("resample", {"dataset": "r", "cell_size": 0.0005, "resampling": "BILINEAR", "output_name": "rs"})
        with self.assertRaises(ValueError):
            validate_call("resample", {"dataset": "r", "cell_size": 0, "output_name": "rs"})
        with self.assertRaises(ValueError):
            validate_call("resample", {"dataset": "r", "cell_size": 30, "resampling": "CUBIC_SPLINE", "output_name": "rs"})
        validate_call("project_raster", {"dataset": "r", "target_wkid": 3857, "transformation": "WGS_1984_(ITRF00)_To_NAD_1983",
                                         "resampling": "BILINEAR", "cell_size": 30, "output_name": "pr"})
        with self.assertRaises(ValueError):
            validate_call("project_raster", {"dataset": "r", "target_wkid": 0, "output_name": "pr"})

    def test_hydrology_enums(self):
        validate_call("fill", {"dataset": "r", "z_limit": 3.0, "output_name": "out"})
        with self.assertRaises(ValueError):
            validate_call("fill", {"dataset": "r", "z_limit": 0, "output_name": "out"})
        for tool in ("flow_direction", "flow_accumulation"):
            validate_call(tool, {"dataset": "r", "flow_direction_type": "DINF", "output_name": "out"})
            for bad in ("MFD8", "D9"):
                with self.subTest(tool=tool, bad=bad), self.assertRaises(ValueError):
                    validate_call(tool, {"dataset": "r", "flow_direction_type": bad, "output_name": "out"})
        with self.assertRaises(ValueError):
            validate_call("flow_accumulation", {"dataset": "r", "data_type": "DOUBLE", "output_name": "out"})


class ModelSchemaAndHintTests(unittest.TestCase):
    def test_model_schema_lists_new_tools_without_host_tools(self):
        from arcgis_agent.deepseek import tool_schemas

        names = {entry["function"]["name"] for entry in tool_schemas()}
        for name in ("select_by_location", "near_analysis", "create_thiessen_polygons", "calculate_field",
                     "define_projection", "export_table_csv", "export_features", "verify_dataset", "reclassify",
                     "hillshade", "resample", "project_raster", "fill", "flow_direction", "flow_accumulation"):
            self.assertIn(name, names)
        self.assertNotIn("run_python", names)

    def test_new_failure_hints(self):
        self.assertIn("字段", hint_for("ERROR 000728: Input field does not exist"))
        self.assertIn("除零", hint_for("ZeroDivisionError: division by zero"))
        self.assertIn("schema lock", hint_for("cannot acquire a schema lock"))
        self.assertIsNone(hint_for("something unrelated"))


if __name__ == "__main__":
    unittest.main()
