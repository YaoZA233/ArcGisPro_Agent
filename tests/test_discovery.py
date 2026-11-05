"""Offline tests for autonomous data discovery: find_datasets, data_roots config and payload wiring."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arcgis_agent.config import Settings
from arcgis_agent.gis_tools import MAX_SEARCH_DEPTH, find_datasets
from arcgis_agent.runtime import ArcPyRuntime, InProcessRuntime
from arcgis_agent.tools import validate_call


class FakeDescribe:
    def __init__(self, data_type):
        self.dataType = data_type


class FakeArcpy:
    """Mimics the da.Walk/Describe behaviour find_datasets relies on, over a real directory tree."""

    TYPE_BY_SUFFIX = {".shp": "FeatureClass", ".tif": "RasterDataset", ".img": "RasterDataset", ".dbf": "Table"}

    class da:
        @staticmethod
        def Walk(top, datatype=None, **ignored):
            stack = [Path(top)]
            while stack:
                current = stack.pop(0)
                try:
                    entries = sorted(current.iterdir(), key=lambda item: item.name)
                except OSError:
                    continue
                dirnames = [item.name for item in entries if item.is_dir()]
                filenames = [item.name for item in entries if item.is_file()]
                yield str(current), dirnames, filenames
                stack = [current / name for name in dirnames] + stack

    @classmethod
    def Describe(cls, path):
        suffix = Path(path).suffix.lower()
        if suffix in cls.TYPE_BY_SUFFIX:
            return FakeDescribe(cls.TYPE_BY_SUFFIX[suffix])
        if any(part.lower().endswith(".gdb") for part in Path(path).parts):
            return FakeDescribe("FeatureClass")
        raise RuntimeError(f"cannot describe {path}")


def touch(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")


class FindDatasetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        touch(self.root / "北京数据.gdb" / "Parcels")
        touch(self.root / "北京数据.gdb" / "Roads")
        touch(self.root / "roads.shp")
        touch(self.root / "sub" / "lakes.tif")
        self.arcpy = FakeArcpy

    def test_keyword_matches_dataset_name_case_insensitively(self):
        result = find_datasets(self.arcpy, {"keyword": "parcel"}, [self.root])
        self.assertEqual(result["count"], 1)
        self.assertEqual(Path(result["matches"][0]["path"]).name, "Parcels")
        self.assertEqual(result["matches"][0]["data_type"], "FeatureClass")
        self.assertFalse(result["truncated"])

    def test_keyword_matches_containing_workspace_name(self):
        result = find_datasets(self.arcpy, {"keyword": "北京"}, [self.root])
        names = {Path(item["path"]).name for item in result["matches"]}
        self.assertEqual(names, {"Parcels", "Roads"})

    def test_no_keyword_returns_everything_with_workspace_and_type(self):
        result = find_datasets(self.arcpy, {}, [self.root])
        names = {Path(item["path"]).name for item in result["matches"]}
        self.assertEqual(names, {"Parcels", "Roads", "roads.shp", "lakes.tif"})
        by_name = {Path(item["path"]).name: item for item in result["matches"]}
        self.assertEqual(by_name["lakes.tif"]["data_type"], "RasterDataset")
        self.assertEqual(Path(by_name["Parcels"]["workspace"]).name, "北京数据.gdb")
        self.assertEqual(result["searched_roots"], [str(self.root)])
        self.assertIsNone(result["keyword"])

    def test_limit_truncates_and_reports(self):
        result = find_datasets(self.arcpy, {"limit": 2}, [self.root])
        self.assertEqual(result["count"], 2)
        self.assertTrue(result["truncated"])

    def test_empty_result_is_a_legal_outcome(self):
        result = find_datasets(self.arcpy, {"keyword": "不存在的名字"}, [self.root])
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["count"], 0)
        self.assertFalse(result["truncated"])

    def test_root_must_stay_inside_configured_data_roots(self):
        outside = self.root.parent / "elsewhere"
        with self.assertRaises(ValueError):
            find_datasets(self.arcpy, {"root": str(outside)}, [self.root])

    def test_root_subdirectory_narrows_the_search(self):
        result = find_datasets(self.arcpy, {"root": str(self.root / "sub")}, [self.root])
        names = {Path(item["path"]).name for item in result["matches"]}
        self.assertEqual(names, {"lakes.tif"})
        self.assertEqual(result["searched_roots"], [str(self.root / "sub")])

    def test_missing_data_roots_configuration_raises_with_guidance(self):
        for roots in (None, []):
            with self.assertRaises(ValueError) as context:
                find_datasets(self.arcpy, {}, roots)
            self.assertIn("data_roots", str(context.exception))

    def test_all_configured_roots_missing_on_disk_raises(self):
        with self.assertRaises(ValueError):
            find_datasets(self.arcpy, {}, [self.root / "not-here"])

    def test_search_is_depth_bounded(self):
        deep = self.root / "deep"
        touch(deep.joinpath(*[f"level{i}" for i in range(MAX_SEARCH_DEPTH + 3)]) / "hidden.shp")
        touch(deep / "visible.shp")
        result = find_datasets(self.arcpy, {"keyword": "hidden"}, [self.root])
        self.assertEqual(result["matches"], [])
        result = find_datasets(self.arcpy, {"keyword": "visible"}, [self.root])
        self.assertEqual(result["count"], 1)

    def test_undescribable_file_is_reported_without_type(self):
        touch(self.root / "notes.txt")
        with patch.object(FakeArcpy, "Describe", side_effect=RuntimeError("locked")):
            result = find_datasets(FakeArcpy, {"keyword": "notes"}, [self.root])
        self.assertEqual(result["count"], 1)
        self.assertIsNone(result["matches"][0]["data_type"])


class FindDatasetsContractTests(unittest.TestCase):
    def test_schema_accepts_empty_and_full_arguments(self):
        validate_call("find_datasets", {})
        validate_call("find_datasets", {"keyword": "宗地", "root": "D:/data", "limit": 500})

    def test_schema_rejects_invalid_arguments(self):
        for arguments in ({"limit": 0}, {"limit": 501}, {"keyword": ""}, {"unknown": 1}):
            with self.assertRaises(ValueError):
                validate_call("find_datasets", arguments)

    def test_tool_is_readonly_and_not_host_bound(self):
        from arcgis_agent.catalog import TOOLS
        item = TOOLS["find_datasets"]
        self.assertFalse(item.writes)
        self.assertFalse(item.host)


class DataRootsConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_dir = Path(self.temp.name).resolve()

    def write_config(self, body: str) -> Path:
        path = self.config_dir / "agent.toml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_relative_and_absolute_roots_resolve_from_config_file(self):
        settings = Settings.load(self.write_config(
            '[agent]\nworkspace = "ws"\ndata_roots = ["data", "D:/gis"]\n'))
        self.assertEqual(settings.data_roots, (self.config_dir / "data", Path("D:/gis")))

    def test_default_is_empty(self):
        self.assertEqual(Settings.load(self.write_config("")).data_roots, ())

    def test_invalid_data_roots_are_rejected(self):
        for body in ('[agent]\ndata_roots = "data"\n',
                     '[agent]\ndata_roots = [""]\n',
                     '[agent]\ndata_roots = [1]\n',
                     "[agent]\ndata_roots = [" + ",".join(f'"r{i}"' for i in range(17)) + "]\n"):
            with self.assertRaises(ValueError):
                Settings.load(self.write_config(body))


class DataRootsWiringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_worker_request_payload_carries_data_roots(self):
        runtime = ArcPyRuntime(Path("python.exe"), data_roots=[Path("D:/gis")])
        captured = {}

        def fake_spawn(command, environment):
            captured.update(json.loads(Path(command[2]).read_text(encoding="utf-8")))
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(runtime, "_spawn", fake_spawn):
            result = runtime.call("find_datasets", {"keyword": "x"}, self.root)
        self.assertEqual(captured["data_roots"], [str(Path("D:/gis"))])
        self.assertFalse(result["ok"])  # no result.json written; payload check is the point

    def test_worker_passes_data_roots_to_execute(self):
        import arcgis_agent.worker as worker
        request = self.root / "request.json"
        result_file = self.root / "result.json"
        request.write_text(json.dumps({"tool": "find_datasets", "arguments": {}, "output_dir": str(self.root),
                                       "data_roots": ["D:/gis"]}, ensure_ascii=False), encoding="utf-8")
        captured = {}

        def fake_execute(tool, arguments, output_dir, selection_snapshot=None, data_roots=None):
            captured["data_roots"] = data_roots
            return {"matches": []}

        with patch.object(worker, "execute", fake_execute), patch.object(sys, "argv", ["worker", str(request), str(result_file)]):
            exit_code = worker.main()
        self.assertEqual(exit_code, 0)
        self.assertEqual(captured["data_roots"], ["D:/gis"])

    def test_in_process_runtime_forwards_data_roots(self):
        captured = {}

        def fake_execute(name, arguments, output_dir, allow_current=False, selection_snapshot=None,
                         skills_dir=None, data_roots=None, memory=None):
            captured["data_roots"] = data_roots
            return {"matches": []}

        runtime = InProcessRuntime(data_roots=[Path("D:/gis")])
        with patch("arcgis_agent.runtime.execute", fake_execute):
            result = runtime.call("find_datasets", {}, self.root)
        self.assertTrue(result["ok"])
        self.assertEqual(captured["data_roots"], (str(Path("D:/gis")),))


if __name__ == "__main__":
    unittest.main()
