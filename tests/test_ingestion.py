import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from arcgis_agent.catalog import approval_details, validate_call
from arcgis_agent.hints import hint_for


class IngestionContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_import_table_points_schema(self):
        validate_call("import_table_points", {"dataset": "C:/data/stations.csv", "output_name": "stations"})
        validate_call("import_table_points", {"dataset": "C:/data/stations.xlsx\\Sheet1$", "output_name": "stations",
                                              "lat_field": "lat", "lon_field": "lon", "wkid": 4490})
        bad = [{"dataset": "C:/data/stations.txt", "output_name": "stations"},
               {"dataset": "C:/data/stations.csv"},
               {"dataset": "C:/data/stations.csv", "output_name": "stations", "wkid": 0},
               {"dataset": "C:/data/stations.csv", "output_name": "stations", "lat_field": ""}]
        for arguments in bad:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                validate_call("import_table_points", arguments)

    def test_convert_dataset_schema(self):
        validate_call("convert_dataset", {"dataset": "C:/data/area.gpkg\\layer", "output_name": "area"})
        validate_call("convert_dataset", {"dataset": "C:/data/placemarks.kml", "output_name": "places"})
        with self.assertRaises(ValueError):
            validate_call("convert_dataset", {"dataset": "C:/data/area.gpkg\\layer"})

    def test_approval_previews_declare_outputs(self):
        table = approval_details("import_table_points", {"dataset": "C:/d/s.csv", "output_name": "stations"}, self.root)
        self.assertTrue(table["writes"])
        self.assertEqual(table["expected_outputs"], [str(self.root.resolve() / "results.gdb" / "stations")])
        kml = approval_details("convert_dataset", {"dataset": "C:/d/p.kml", "output_name": "places"}, self.root)
        self.assertEqual(kml["expected_outputs"], [str(self.root.resolve() / "kml_places" / "places.gdb")])
        gpkg = approval_details("convert_dataset", {"dataset": "C:/d/a.gpkg\\l", "output_name": "area"}, self.root)
        self.assertEqual(gpkg["expected_outputs"], [str(self.root.resolve() / "results.gdb" / "area")])

    def test_hints_for_common_failures(self):
        self.assertIn("地理数据库", hint_for("UnicodeDecodeError: 'utf-8' codec can't decode byte 0x90"))
        self.assertIsNotNone(hint_for("ERROR 000732: Dataset does not exist"))
        self.assertIsNotNone(hint_for("ERROR 000800: The value is not a member of the list"))
        self.assertIsNotNone(hint_for("A geographic transformation is required"))
        self.assertIsNone(hint_for("everything completed normally"))


class WorkerHintTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def runtime(self):
        from arcgis_agent.runtime import ArcPyRuntime
        return ArcPyRuntime(Path(sys.executable))

    def test_worker_failure_carries_hint(self):
        failure = subprocess.CompletedProcess(["worker"], 1, "", "ERROR 000732: Dataset does not exist")
        with patch("arcgis_agent.runtime.subprocess.run", return_value=failure):
            result = self.runtime().call("describe", {"dataset": "C:/d/missing.shp"}, self.root)
        self.assertEqual(result["error"]["type"], "WorkerFailure")
        self.assertIn("hint", result["error"])

    def test_timeout_carries_hint(self):
        expired = subprocess.TimeoutExpired(["worker"], 180)
        with patch("arcgis_agent.runtime.subprocess.run", side_effect=expired):
            result = self.runtime().call("describe", {"dataset": "C:/d/x.shp"}, self.root)
        self.assertEqual(result["error"]["type"], "Timeout")
        self.assertIn("hint", result["error"])


if __name__ == "__main__":
    unittest.main()
