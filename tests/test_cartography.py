"""Batch 3: cartography contract and control-flow tests.

The ArcPy call shapes in arcgis_agent.cartography were verified against the real Pro 3.5.2 runtime
(scripts/smoke_cartography.py); these offline tests pin the typed contract, the no-overwrite guards
and the two invariants the user relies on: build_layout only ever writes inside the task directory,
and export_layout never saves the project it opens.
"""
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from arcgis_agent import cartography
from arcgis_agent.cartography import (CARTOGRAPHY_TOOLS, DEFAULT_ELEMENTS, _camera_extent, _map_layer, _page,
                                       _reserve, _staged, _target)
from arcgis_agent.catalog import EXPORT_EXTENSIONS, PAGE_SIZES, TOOLS, approval_details, validate_call

LAYERS = ["C:/data/regions.shp", "C:/data/roads.shp"]


def base(**extra):
    return {"title": "湖南县市地图", "layers": LAYERS, "output_name": "hunan_map", **extra}


class CartographyContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_tools_are_typed_writes_and_not_host_only(self):
        for name in CARTOGRAPHY_TOOLS:
            self.assertIn(name, TOOLS)
            self.assertTrue(TOOLS[name].writes, name)
            self.assertFalse(TOOLS[name].host, name)

    def test_both_tools_are_offered_to_the_model(self):
        from arcgis_agent.deepseek import tool_schemas

        names = {entry["function"]["name"] for entry in tool_schemas()}
        self.assertIn("build_layout", names)
        self.assertIn("export_layout", names)

    def test_valid_layout_arguments(self):
        validate_call("build_layout", base())
        validate_call("build_layout", base(page_size="A3", orientation="PORTRAIT",
                                           elements=["LEGEND", "DUAL_SCALE_BAR"],
                                           extent={"xmin": 108.0, "ymin": 24.0, "xmax": 114.0, "ymax": 30.0}))
        validate_call("build_layout", base(elements=[]))

    def test_layout_arguments_reject_unverified_choices(self):
        cases = ({"page_size": "A5"}, {"orientation": "DIAGONAL"}, {"elements": ["GRATICULE"]},
                 {"elements": ["LEGEND", "LEGEND"]}, {"elements": [1]}, {"title": ""},
                 {"output_name": "图层"}, {"output_name": "1abc"}, {"layers": []},
                 {"layers": [f"C:/d{i}.shp" for i in range(13)]},
                 {"extent": {"xmin": 1, "ymin": 2}},
                 {"extent": {"xmin": 1, "ymin": 2, "xmax": 1, "ymax": 3}},
                 {"extent": {"xmin": 1, "ymin": 2, "xmax": 0, "ymax": 3, "extra": 1}},
                 {"save_project": True}, {"map_name": "Map"})
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_call("build_layout", {**base(), **bad})

    def test_two_scale_bars_are_refused(self):
        with self.assertRaises(ValueError):
            validate_call("build_layout", base(elements=["SCALE_BAR", "DUAL_SCALE_BAR"]))
        validate_call("build_layout", base(elements=["DUAL_SCALE_BAR", "LEGEND"]))

    def test_export_contract(self):
        validate_call("export_layout", {"project": "C:/p/p.aprx", "output_name": "sheet", "format": "PNG"})
        validate_call("export_layout", {"project": "CURRENT", "layout": "湖南县市地图", "output_name": "sheet",
                                        "format": "PDF", "dpi": 300})
        for bad in ({"format": "WEBP"}, {"format": "GIF"}, {"dpi": 71}, {"dpi": 601}, {"dpi": "300"},
                    {"output_name": "my sheet"}, {"layout": ""}, {"project": ""}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_call("export_layout", {"project": "C:/p/p.aprx", "output_name": "sheet", "format": "PNG", **bad})
        with self.assertRaises(ValueError):
            validate_call("export_layout", {"project": "C:/p/p.aprx", "output_name": "sheet"})
        with self.assertRaises(ValueError):
            validate_call("export_layout", {"project": "C:/p/p.aprx", "output_name": "sheet", "format": "PNG", "save_project": True})

    def test_every_export_format_has_a_verified_extension(self):
        self.assertEqual(sorted(EXPORT_EXTENSIONS), sorted(TOOLS["export_layout"].properties["format"]["enum"]))
        self.assertEqual(sorted(PAGE_SIZES), TOOLS["build_layout"].properties["page_size"]["enum"])

    def test_layout_element_choices_match_the_verified_surround_tokens(self):
        choices = TOOLS["build_layout"].properties["elements"]["items"]["enum"]
        self.assertEqual(sorted(choices), sorted(cartography.SURROUND_TYPES))
        self.assertEqual(cartography.SURROUND_TYPES, {name: name for name in choices},
                         "createMapSurroundElement takes the token verbatim, so a rename must reach both sides")
        self.assertEqual(sorted(choices), sorted(cartography.ELEMENT_LABELS))
        self.assertTrue(set(cartography.DEFAULT_ELEMENTS) <= set(choices))

    def test_approval_preview_lists_task_directory_outputs(self):
        project = approval_details("build_layout", base(), self.root)
        self.assertEqual(project["expected_outputs"], [str(self.root.resolve() / "cartography" / "hunan_map.aprx")])
        self.assertTrue(project["writes"])
        for name, extension in EXPORT_EXTENSIONS.items():
            arguments = {"project": "C:/p/p.aprx", "output_name": "sheet", "format": name}
            details = approval_details("export_layout", arguments, self.root)
            self.assertEqual(details["expected_outputs"],
                             [str(self.root.resolve() / "cartography" / f"sheet.{extension}")], name)
            self.assertIn("不保存", details["effect"])


class FakeField:
    def __init__(self, name):
        self.name, self.type, self.length = name, "String", 32
        self.isNullable, self.required, self.editable = True, False, True


class FakeReference:
    def __init__(self, wkid, name=None):
        self.factoryCode, self.name = wkid, name or f"EPSG:{wkid}"
        self.type = "Geographic" if wkid == 4326 else "Projected"


class FakeBox:
    def __init__(self, x0, y0, x1, y1):
        self.XMin, self.YMin, self.XMax, self.YMax = x0, y0, x1, y1


class FakeDesc:
    def __init__(self, data_type="FeatureClass", shape_type="Polygon", wkid=4326, box=(0.0, 0.0, 10.0, 10.0),
                 sr_name=None):
        self.dataType, self.shapeType = data_type, shape_type
        self.spatialReference = FakeReference(wkid, sr_name)
        self.extent = FakeBox(*box)
        self.catalogPath = "C:/data/x"
        self.fields = [FakeField("zone_name")]


class FakeExtent:
    def __init__(self, *values):
        self.bounds = [float(v) for v in values]
        self.XMin, self.YMin, self.XMax, self.YMax = self.bounds


class HelperTests(unittest.TestCase):
    def test_page_size_and_orientation(self):
        self.assertEqual(_page({"page_size": "A4"}), (297.0, 210.0))
        self.assertEqual(_page({"page_size": "A4", "orientation": "PORTRAIT"}), (210.0, 297.0))
        self.assertEqual(_page({"page_size": "TABLOID", "orientation": "LANDSCAPE"}), (431.8, 279.4))

    def test_target_refuses_overwrite(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = _target(root, "sheet", "png")
            self.assertEqual(first.parent, (root.resolve() / "cartography"))
            self.assertFalse(first.parent.exists(), "the refusal check must not create the directory")
            _reserve(first).write_bytes(b"x")
            self.assertTrue(first.parent.exists())
            with self.assertRaisesRegex(ValueError, "禁止覆盖"):
                _target(root, "sheet", "png")

    def test_staged_writes_are_published_by_one_rename(self):
        with TemporaryDirectory() as directory:
            target = _reserve(Path(directory) / "sheet.png")
            staged = _staged(target, "deadbeef")
            self.assertEqual(staged.name, "sheet.deadbeef.png")
            staged.write_bytes(b"partial")
            os.replace(staged, target)
            self.assertEqual(target.read_bytes(), b"partial")
            self.assertFalse(staged.exists())

    def test_table_layers_are_refused_before_any_output(self):
        class Arcpy:
            Exists = staticmethod(lambda path: True)
            Describe = staticmethod(lambda path: FakeDesc(data_type="Table", shape_type=None))

        with self.assertRaisesRegex(ValueError, "没有几何"):
            _map_layer(Arcpy(), "C:/data/rows")

    def test_raster_layers_are_accepted(self):
        class Arcpy:
            Exists = staticmethod(lambda path: True)
            Describe = staticmethod(lambda path: FakeDesc(data_type="RasterDataset", shape_type=None))

        self.assertEqual(_map_layer(Arcpy(), "C:/data/dem.tif").dataType, "RasterDataset")

    def test_auto_extent_unions_layers_and_refuses_mixed_reference(self):
        class Arcpy:
            Extent = FakeExtent

        prepared = [("a", FakeDesc(box=(0.0, 5.0, 20.0, 30.0))), ("b", FakeDesc(box=(10.0, -2.0, 15.0, 40.0)))]
        extent, reference, source = _camera_extent(Arcpy, {"layers": ["a", "b"]}, prepared)
        self.assertEqual((extent.XMin, extent.YMin, extent.XMax, extent.YMax), (0.0, -2.0, 20.0, 40.0))
        self.assertEqual(reference.factoryCode, 4326)
        self.assertEqual(source, "data_extent")
        mixed = [("a", FakeDesc(wkid=4326)), ("b", FakeDesc(wkid=4547))]
        with self.assertRaisesRegex(ValueError, "坐标系不一致"):
            _camera_extent(Arcpy, {}, mixed)

    def test_requested_extent_is_used_verbatim(self):
        class Arcpy:
            Extent = FakeExtent

        extent, reference, source = _camera_extent(Arcpy, {"extent": {"xmin": 1.5, "ymin": 2.5, "xmax": 3.5,
                                                                      "ymax": 4.5}},
                                                   [("a", FakeDesc(wkid=4547))])
        self.assertEqual(extent.bounds, [1.5, 2.5, 3.5, 4.5])
        self.assertEqual(reference.factoryCode, 4547)
        self.assertEqual(source, "requested")

    def test_projected_numbers_refused_when_the_layer_is_geographic(self):
        """camera.setExtent reads its box in the map's own units, so metre values on a GCS layer
        would silently produce an empty sheet."""
        class Arcpy:
            Extent = FakeExtent

        with self.assertRaisesRegex(ValueError, "地理坐标系"):
            _camera_extent(Arcpy, {"extent": {"xmin": 12400000.0, "ymin": 2900000.0, "xmax": 12700000.0,
                                              "ymax": 3100000.0}}, [("a", FakeDesc(wkid=4326))])
        with self.assertRaisesRegex(ValueError, "地理坐标系"):
            _camera_extent(Arcpy, {"extent": {"xmin": 111.0, "ymin": -95.0, "xmax": 114.0, "ymax": 30.0}},
                           [("a", FakeDesc(wkid=4326))])

    def test_projected_extent_of_any_size_is_accepted_for_projected_layers(self):
        class Arcpy:
            Extent = FakeExtent

        for wkid in (3857, 4547, 4528):
            with self.subTest(wkid=wkid):
                extent, _, source = _camera_extent(Arcpy, {"extent": {"xmin": 12400000.0, "ymin": 2900000.0,
                                                                      "xmax": 12700000.0, "ymax": 3100000.0}},
                                                   [("a", FakeDesc(wkid=wkid))])
                self.assertEqual(extent.bounds, [12400000.0, 2900000.0, 12700000.0, 3100000.0])
                self.assertEqual(source, "requested")

    def test_unknown_spatial_reference_is_refused(self):
        class Arcpy:
            Extent = FakeExtent

        # A shapefile without a .prj reports an Unknown SR; several of them are "equal" but unusable.
        for wkid in (0, -1):
            with self.subTest(wkid=wkid), self.assertRaisesRegex(ValueError, "坐标系未知"):
                _camera_extent(Arcpy, {}, [("a", FakeDesc(wkid=wkid, sr_name="Unknown"))])

    def test_spatial_reference_advice_names_a_tool_that_exists(self):
        """The project tool wraps management.Project, which is feature-class only, so a raster must
        not be told to use it."""
        class Arcpy:
            Extent = FakeExtent

        raster = FakeDesc(data_type="RasterDataset", wkid=0, sr_name="Unknown")
        with self.assertRaisesRegex(ValueError, "Project Raster"):
            _camera_extent(Arcpy, {}, [("C:/a.tif", raster)])
        with self.assertRaisesRegex(ValueError, "project 工具"):
            _camera_extent(Arcpy, {}, [("C:/a", FakeDesc(wkid=0, sr_name="Unknown"))])
        with self.assertRaisesRegex(ValueError, "Project Raster"):
            _camera_extent(Arcpy, {}, [("C:/a.tif", FakeDesc(data_type="RasterDataset", wkid=4326)),
                                       ("C:/b", FakeDesc(wkid=3857))])


class StubElement:
    def __init__(self, name):
        self.name = name


class StubLayout:
    def __init__(self, name, fail_on=None, export_error=None, empty_output=False, writes_elsewhere=False):
        self.name, self.pageWidth, self.pageHeight, self.pageUnits = name, 297.0, 210.0, "MILLIMETER"
        self.fail_on, self.export_error, self.empty_output = fail_on, export_error, empty_output
        self.writes_elsewhere = writes_elsewhere
        self.frames, self.surrounds, self.definition_sets = [], [], 0
        self.exports = []

    def createMapFrame(self, geometry, map_object, name=None):
        frame = StubElement(name)
        frame.camera = StubCamera()
        self.frames.append((frame, geometry, map_object))
        return frame

    def createMapSurroundElement(self, geometry, surround_type, frame, name=None):
        if self.fail_on and self.fail_on == surround_type:
            raise RuntimeError("ArcpyInternal: surround refused")
        element = StubElement(name)
        self.surrounds.append((element, surround_type, geometry))
        return element

    def getDefinition(self, version):
        self.version = version
        return StubDefinition()

    def setDefinition(self, definition):
        self.definition_sets += 1
        self.placed_title = definition.elements[0]

    def listElements(self):
        return [StubElement("图名")] + self.frames + self.surrounds

    def export(self, export_format):
        self.exports.append(export_format)
        if self.export_error:
            raise RuntimeError(self.export_error)
        Path(export_format.filePath).write_bytes(b"" if self.empty_output else b"%PDF-1.4 stub bytes")
        if self.writes_elsewhere:
            elsewhere = Path(export_format.filePath).with_name("elsewhere.png")
            elsewhere.write_bytes(b"unexpected")
            return str(elsewhere)
        return export_format.filePath


class StubDefinition:
    def __init__(self):
        self.elements = []


class StubCamera:
    def __init__(self):
        self.set_extents = []

    def setExtent(self, extent):
        self.set_extents.append(extent)


class StubMap:
    """Pro gives an empty map the coordinate system of the first layer added; the stub mirrors that."""

    name = "Map"

    def __init__(self, spatial_reference=None, adds=True):
        self.added = []
        self.spatialReference = spatial_reference or FakeReference(4326)
        self.adds = adds

    def addDataFromPath(self, path):
        self.added.append(path)
        return StubElement(Path(path).stem) if self.adds else None


class StubProject:
    instances = []

    def __init__(self, path, fail_on=None, map_reference=None, adds=True):
        self.filePath, self.saved, self.fail_on = path, False, fail_on
        self.maps, self.layouts = [StubMap(map_reference, adds)], []
        StubProject.instances.append(self)

    def listMaps(self):
        return self.maps

    def listLayouts(self):
        return self.layouts

    def createLayout(self, width, height, units, name=None):
        assert units == "MILLIMETER"
        layout = StubLayout(name, fail_on=self.fail_on)
        layout.pageWidth, layout.pageHeight = width, height
        self.layouts.append(layout)
        return layout

    def save(self):
        self.saved = True


class StubFormat:
    def __init__(self, fmt, file_path):
        self.format, self.filePath, self.resolution = fmt, file_path, 96


class StubCim:
    class CIMParagraphTextGraphic:
        def __init__(self):
            self.text = self.symbol = self.shape = None

    class CIMTextSymbol:
        def __init__(self):
            self.fontFamilyName, self.fontSize, self.bold, self.horizontalAlignment, self.color = None, None, False, None, None

    class CIMRGBColor:
        def __init__(self, red=0, green=0, blue=0):
            self.red, self.green, self.blue = red, green, blue

    class CIMGraphicElement:
        def __init__(self):
            self.name = self.graphic = None


class StubArcpy:
    """Only the surface cartography touches; the real call shapes are proven by the ArcPy smoke run."""

    def __init__(self, install_dir, descs=None, project_factory=None):
        self.cim = StubCim
        self.mp = type("mp", (), {})
        self.mp.ArcGISProject = project_factory or (lambda path: StubProject(path))
        self.mp.CreateExportFormat = StubFormat
        self.Point = lambda x, y: (x, y)
        self.Array = lambda items: list(items)
        self.Polygon = lambda array: ("polygon", array)
        self.Extent = FakeExtent
        self.desces = descs or {}
        self.install = Path(install_dir)

    def GetInstallInfo(self):
        return {"InstallDir": str(self.install)}

    def Exists(self, path):
        return True

    def Describe(self, path):
        return self.desces.get(path, FakeDesc())

    def ListFields(self, path):
        return self.Describe(path).fields


class FlowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        template = self.root / "pro" / cartography.BLANK_TEMPLATE
        template.parent.mkdir(parents=True)
        template.write_text("blank project template", encoding="utf-8")
        self.install = self.root / "pro"
        self.user_aprx = self.root / "user" / "work.aprx"
        self.user_aprx.parent.mkdir()
        self.user_aprx.write_text("user project", encoding="utf-8")
        StubProject.instances = []

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, arguments=None, fail_on=None, map_reference=None, adds=True):
        arcpy = StubArcpy(self.install, project_factory=lambda path: StubProject(
            path, fail_on=fail_on, map_reference=map_reference, adds=adds))
        return cartography._build_layout(arcpy, arguments or base(), self.root / "run"), arcpy

    def test_build_creates_independent_project_and_returns_names(self):
        result, _ = self.build()
        expected = (self.root / "run" / "cartography" / "hunan_map.aprx").resolve()
        self.assertEqual(Path(result["project"]), expected)
        self.assertTrue(expected.is_file(), "the copied Blank.aprx must stay inside the task directory")
        self.assertEqual(expected.read_text(encoding="utf-8"), "blank project template")
        self.assertEqual(result["layout"], "湖南县市地图")
        self.assertEqual(result["layers"], ["regions", "roads"])
        self.assertEqual(result["page_mm"], [297.0, 210.0])
        self.assertEqual(result["elements"], ["图例", "指北针", "比例尺"])
        self.assertEqual([element for _, element, _ in StubProject.instances[0].layouts[0].surrounds],
                         ["LEGEND", "NORTH_ARROW", "SCALE_BAR"])
        self.assertTrue(StubProject.instances[0].saved, "the task-directory project must be saved for export_layout")
        self.assertEqual(result["project_modified"], False)
        self.assertEqual(json.dumps(result, ensure_ascii=False)[:1], "{")

    def test_build_places_title_and_frames_the_camera(self):
        result, _ = self.build(base(extent={"xmin": 108.0, "ymin": 24.0, "xmax": 114.0, "ymax": 30.0}))
        project = StubProject.instances[0]
        layout = project.layouts[0]
        self.assertEqual(layout.version, "V3")
        self.assertEqual(layout.definition_sets, 1)
        self.assertEqual(layout.placed_title.name, "图名")
        self.assertEqual(layout.placed_title.graphic.text, "湖南县市地图")
        camera = layout.frames[0][0].camera
        self.assertEqual(camera.set_extents[0].bounds, [108.0, 24.0, 114.0, 30.0])
        self.assertEqual(result["extent_source"], "requested")

    def test_build_failure_leaves_no_half_built_project(self):
        with self.assertRaises(RuntimeError):
            self.build(base(elements=["NORTH_ARROW"]), fail_on="NORTH_ARROW")
        self.assertFalse((self.root / "run" / "cartography" / "hunan_map.aprx").exists())

    def test_layer_validation_happens_before_the_project_is_copied(self):
        # A refused call must not leave a stray .aprx in the task directory (found by the real ArcPy smoke run).
        class Arcpy(StubArcpy):
            Describe = staticmethod(lambda path: FakeDesc(data_type="Table", shape_type=None))

        arcpy = Arcpy(self.install)
        with self.assertRaisesRegex(ValueError, "没有几何"):
            cartography._build_layout(arcpy, base(layers=["C:/data/notes"]), self.root / "run")
        self.assertFalse((self.root / "run" / "cartography" / "hunan_map.aprx").exists())

    def test_mixed_reference_leaves_no_half_built_project(self):
        class Arcpy(StubArcpy):
            def Describe(self, path):
                return FakeDesc(wkid=3857) if path.endswith("roads.shp") else FakeDesc(wkid=4326)

        with self.assertRaisesRegex(ValueError, "坐标系不一致"):
            cartography._build_layout(Arcpy(self.install), base(), self.root / "run")
        self.assertFalse((self.root / "run" / "cartography" / "hunan_map.aprx").exists())

    def test_build_refuses_existing_project_name(self):
        self.build()
        with self.assertRaisesRegex(ValueError, "禁止覆盖"):
            self.build()

    def test_empty_elements_build_a_clean_sheet(self):
        # elements=[] is allowed by the contract and must mean "no surrounds", not "the defaults".
        result, _ = self.build(base(elements=[]))
        self.assertEqual(result["elements"], [])
        self.assertEqual(StubProject.instances[0].layouts[0].surrounds, [])

    def test_omitted_elements_use_the_documented_default(self):
        result, _ = self.build(base())
        self.assertEqual(result["elements"], [cartography.ELEMENT_LABELS[name] for name in DEFAULT_ELEMENTS])

    def test_side_band_is_reserved_only_for_a_legend(self):
        frame_widths = {}
        for elements in (["LEGEND", "NORTH_ARROW"], ["NORTH_ARROW"], []):
            with TemporaryDirectory() as directory:
                root = Path(directory)
                template = root / "pro" / cartography.BLANK_TEMPLATE
                template.parent.mkdir(parents=True)
                template.write_text("blank project template", encoding="utf-8")
                StubProject.instances = []
                arcpy = StubArcpy(root / "pro", project_factory=lambda path: StubProject(path))
                cartography._build_layout(arcpy, base(elements=elements), root / "run")
                geometry = StubProject.instances[0].layouts[0].frames[0][1]
                frame_widths[tuple(elements)] = round(max(point[0] for point in geometry[1]), 3)
        self.assertEqual(frame_widths[("LEGEND", "NORTH_ARROW")], 297.0 - 12.0 - 62.0)
        self.assertEqual(frame_widths[("NORTH_ARROW",)], 297.0 - 12.0)
        self.assertEqual(frame_widths[()], 297.0 - 12.0)

    def test_refused_build_leaves_no_cartography_directory(self):
        class Arcpy(StubArcpy):
            Describe = staticmethod(lambda path: FakeDesc(data_type="Table", shape_type=None))

        with self.assertRaisesRegex(ValueError, "没有几何"):
            cartography._build_layout(Arcpy(self.install), base(), self.root / "run")
        self.assertFalse((self.root / "run" / "cartography").exists(),
                         "a call that was refused before writing anything must not create directories")

    def test_successful_build_leaves_only_the_project_file(self):
        self.build()
        self.assertEqual(sorted(path.name for path in (self.root / "run" / "cartography").iterdir()),
                         ["hunan_map.aprx"])

    def test_layer_that_cannot_be_added_is_reported(self):
        # ArcPy returns None when a dataset cannot land on the map; the old code crashed on .name instead.
        with self.assertRaisesRegex(ValueError, "无法把"):
            self.build(base(), adds=False)
        self.assertFalse((self.root / "run" / "cartography" / "hunan_map.aprx").exists())

    def test_map_that_kept_a_foreign_reference_is_refused(self):
        # The camera reads its numbers in the map's coordinate system, so a mismatch must not ship a blank sheet.
        with self.assertRaisesRegex(ValueError, "不一致"):
            self.build(base(), map_reference=FakeReference(3857))
        self.assertFalse((self.root / "run" / "cartography" / "hunan_map.aprx").exists())
        self.assertEqual(sorted(path.name for path in (self.root / "run" / "cartography").iterdir()), [])

    def test_export_must_write_the_declared_path(self):
        project = StubProject(str(self.user_aprx))
        project.layouts.append(StubLayout("图A", writes_elsewhere=True))
        arcpy = StubArcpy(self.install, project_factory=lambda path: project)
        with self.assertRaisesRegex(RuntimeError, "未按声明路径"):
            cartography._export_layout(arcpy, {"project": str(self.user_aprx), "output_name": "s",
                                               "format": "PNG"}, self.root / "run")
        self.assertFalse((self.root / "run" / "cartography" / "s.png").exists())
        self.assertFalse(project.saved)

    def test_export_writes_image_and_never_saves_the_project(self):
        project = StubProject(str(self.user_aprx))
        project.layouts.append(StubLayout("湖南县市地图"))
        arcpy = StubArcpy(self.install, project_factory=lambda path: project)
        result = cartography._export_layout(arcpy, {"project": str(self.user_aprx), "output_name": "sheet",
                                                   "format": "PDF", "dpi": 300}, self.root / "run")
        output = Path(result["output"])
        self.assertEqual(output, (self.root / "run" / "cartography" / "sheet.pdf").resolve())
        self.assertGreater(result["bytes"], 0)
        self.assertEqual(result["dpi"], 300)
        self.assertEqual(result["layout"], "湖南县市地图")
        self.assertFalse(project.saved, "export must never write back to the opened project")

    def test_export_requires_current_or_existing_file(self):
        arcpy = StubArcpy(self.install)
        with self.assertRaisesRegex(ValueError, "工程文件不存在"):
            cartography._export_layout(arcpy, {"project": str(self.root / "missing.aprx"), "output_name": "s",
                                               "format": "PNG"}, self.root / "run")
        self.assertFalse((self.root / "run" / "cartography").exists(),
                         "a project that cannot be opened must not create the cartography directory")

    def test_export_reports_available_layouts(self):
        project = StubProject(str(self.user_aprx))
        project.layouts.extend([StubLayout("图A"), StubLayout("图B")])
        arcpy = StubArcpy(self.install, project_factory=lambda path: project)
        with self.assertRaisesRegex(ValueError, "必须指定 layout"):
            cartography._export_layout(arcpy, {"project": str(self.user_aprx), "output_name": "s", "format": "PNG"},
                                       self.root / "run")
        with self.assertRaisesRegex(ValueError, "没有名为"):
            cartography._export_layout(arcpy, {"project": str(self.user_aprx), "layout": "图C", "output_name": "s",
                                               "format": "PNG"}, self.root / "run")
        self.assertFalse((self.root / "run" / "cartography").exists(),
                         "an ambiguous layout name is refused before the output directory is created")

    def export_failure(self, layout, arguments=None):
        project = StubProject(str(self.user_aprx))
        project.layouts.append(layout)
        arcpy = StubArcpy(self.install, project_factory=lambda path: project)
        with self.assertRaises((RuntimeError,)):
            cartography._export_layout(arcpy, {"project": str(self.user_aprx), "output_name": "s",
                                              "format": "PNG", **(arguments or {})}, self.root / "run")
        return project

    def test_export_failure_removes_partial_file(self):
        project = self.export_failure(StubLayout("图A", export_error="boom"))
        self.assertEqual(sorted(path.name for path in (self.root / "run" / "cartography").iterdir()), [],
                         "the staged file is removed, so the same output_name can be retried")
        self.assertFalse(project.saved)

    def test_empty_export_is_reported_as_failure_and_cleaned(self):
        with self.subTest("zero-byte output"):
            self.export_failure(StubLayout("图A", empty_output=True))
        self.assertEqual(sorted(path.name for path in (self.root / "run" / "cartography").iterdir()), [])

    def test_current_project_reaches_arcpy_untouched(self):
        project = StubProject("CURRENT")
        project.layouts.append(StubLayout("图A"))
        seen = []
        arcpy = StubArcpy(self.install, project_factory=lambda path: seen.append(path) or project)
        cartography._export_layout(arcpy, {"project": "CURRENT", "output_name": "s", "format": "PNG"}, self.root / "run")
        self.assertEqual(seen, ["CURRENT"])


if __name__ == "__main__":
    unittest.main()
