"""Typed allowlist shared by planning, validation and approval previews."""
from __future__ import annotations

import ast
import copy
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path


def text(description, maximum=4096, **extra):
    return {"type": "string", "description": description, "minLength": 1, "maxLength": maximum, **extra}


# Enums verified against the local ArcGIS Pro 3.5.2 runtime (ArcGIS help and older builds differ).
STATISTIC_TYPES = ["SUM", "MEAN", "MIN", "MAX", "RANGE", "STD", "COUNT", "FIRST", "LAST", "MEDIAN", "VARIANCE", "UNIQUE", "CONCATENATE", "MODE"]
ZONAL_STATISTIC_TYPES = ["ALL", "MEAN", "MAXIMUM", "MEDIAN", "MINIMUM", "RANGE", "STD", "SUM", "MIN_MAX", "MEAN_STD", "MIN_MAX_MEAN"]
DENSITY_AREA_UNITS = ["SQUARE_MAP_UNITS", "SQUARE_MILES", "SQUARE_KILOMETERS", "ACRES", "HECTARES", "SQUARE_YARDS",
                      "SQUARE_FEET", "SQUARE_INCHES", "SQUARE_METERS", "SQUARE_CENTIMETERS", "SQUARE_MILLIMETERS"]
VARIOGRAM_MODELS = ["SPHERICAL", "EXPONENTIAL", "GAUSSIAN", "LINEAR", "POWER"]
Z_UNITS = ["INCH", "FOOT", "YARD", "MILE_US", "NAUTICAL_MILE", "MILLIMETER", "CENTIMETER", "METER", "KILOMETER", "DECIMETER"]
BOUNDING_GEOMETRIES = ["RECTANGLE_BY_AREA", "RECTANGLE_BY_WIDTH", "CONVEX_HULL", "CIRCLE", "ENVELOPE"]
# Layout page sizes in millimetres and export formats accepted by mp.CreateExportFormat on 3.5.2.
PAGE_SIZES = {"A4": [210.0, 297.0], "A3": [297.0, 420.0], "A2": [420.0, 594.0], "A1": [594.0, 841.0],
              "LETTER": [215.9, 279.4], "TABLOID": [279.4, 431.8]}
PAGE_SIZE_NAMES = sorted(PAGE_SIZES)
ORIENTATIONS = ["LANDSCAPE", "PORTRAIT"]
LAYOUT_ELEMENTS = ["LEGEND", "NORTH_ARROW", "SCALE_BAR", "DUAL_SCALE_BAR"]
EXPORT_FORMATS = ["PNG", "JPEG", "PDF", "TIFF", "SVG"]
EXPORT_EXTENSIONS = {"PNG": "png", "JPEG": "jpg", "PDF": "pdf", "TIFF": "tif", "SVG": "svg"}


DATASET = text("Absolute GIS dataset path; members inside .gdb or .gpkg use the full catalog path")
OUTPUT = text("New output name within this task; existing datasets cannot be overwritten", 64, pattern=r"[A-Za-z][A-Za-z0-9_]{0,63}")
COLUMN = text("Exact column name present in the source table", 64)
FIELD = text("An existing field name, exactly as returned by describe; no cursor tokens or expressions", 128)
NEW_FIELD = text("New ASCII field name", 64, pattern=r"[A-Za-z][A-Za-z0-9_]{0,63}")
FIELDS = {"type": "array", "items": FIELD, "minItems": 1, "maxItems": 32, "uniqueItems": True}
DATASETS = {"type": "array", "items": DATASET, "minItems": 2, "maxItems": 8, "uniqueItems": True}
RASTERS = {"type": "array", "items": DATASET, "minItems": 1, "maxItems": 8, "uniqueItems": True}
SCALAR = {"type": ["string", "number", "boolean", "null"], "maxLength": 4096}
DISTANCE = text("Positive distance plus Meters/Kilometers/Feet/Miles, e.g. 500 Meters", 32)
DISTANCE_RE = re.compile(r"(?:\d+(?:\.\d+)?|\.\d+)\s+(?:Meters|Kilometers|Feet|Miles)")
# Overlap types verified against the local ArcGIS Pro 3.5.2 SelectLayerByLocation parameter list.
OVERLAP_TYPES = ["INTERSECT", "WITHIN_A_DISTANCE", "WITHIN_A_DISTANCE_GEODESIC", "CONTAINS", "COMPLETELY_CONTAINS",
                 "CONTAINS_CLEMENTINI", "WITHIN", "COMPLETELY_WITHIN", "WITHIN_CLEMENTINI", "ARE_IDENTICAL_TO",
                 "BOUNDARY_TOUCHES", "CROSSED_BY_OUTLINE_OF", "HAVE_THEIR_CENTER_IN"]
FLOW_TYPES = ["D8", "MFD", "DINF"]
# Single condition {field, operator, value}; compound {all: [...]} or {any: [...]}.
FILTER_OPERATORS = ["EQ", "NE", "GT", "GE", "LT", "LE", "IS_NULL", "IS_NOT_NULL", "IN", "NOT_IN",
                    "CONTAINS", "NOT_CONTAINS", "STARTS_WITH"]
_FILTER_OPERATOR = {"type": "string", "enum": FILTER_OPERATORS,
                    "description": "IN/NOT_IN pair with values list; CONTAINS/STARTS_WITH need text; IS_NULL* needs null"}
_FILTER_VALUES = {"type": "array", "items": SCALAR, "minItems": 1, "maxItems": 100, "uniqueItems": True}
FILTER_LEAF = {"type": "object", "properties": {"field": FIELD, "operator": _FILTER_OPERATOR, "value": SCALAR,
                                               "values": _FILTER_VALUES},
               "required": ["field", "operator"], "additionalProperties": False}
FILTER = {"type": "object",
          "properties": {"field": FIELD, "operator": _FILTER_OPERATOR, "value": SCALAR, "values": _FILTER_VALUES,
                         "all": {"type": "array", "items": FILTER_LEAF, "minItems": 1, "maxItems": 8,
                                 "description": "Every condition must hold (AND)"},
                         "any": {"type": "array", "items": FILTER_LEAF, "minItems": 1, "maxItems": 8,
                                 "description": "At least one condition must hold (OR)"}},
          "description": "Single condition {field, operator, value}, or compound {all: [...]} / {any: [...]}",
          "additionalProperties": False}
FILTER_TOOLS = frozenset({"read_rows", "select_features", "set_field_value", "calculate_field",
                           "export_table_csv"})
CELL_SIZE = {"type": "number", "exclusiveMinimum": 0, "maximum": 10000000, "description": "Raster cell size as a bare number in the dataset's linear units; a geographic (degree) dataset needs a small degree value such as 0.0005"}
RADIUS = {"type": "number", "exclusiveMinimum": 0, "maximum": 10000000, "description": "Distance as a bare number in the dataset's linear units"}
LINEAR_TOLERANCE = text("Positive tolerance plus Meters/Kilometers/Feet/Miles, e.g. 50 Meters", 32, pattern=r"\d+(?:\.\d+)?\s+(?:Meters|Kilometers|Feet|Miles)")
STAT_ITEMS = {"type": "object", "properties": {"field": FIELD, "statistic": {"type": "string", "enum": STATISTIC_TYPES}},
              "required": ["field", "statistic"], "additionalProperties": False}
COORDINATE = {"type": "number", "minimum": -1e12, "maximum": 1e12}
MAP_EXTENT = {"type": "object", "properties": {key: COORDINATE for key in ("xmin", "ymin", "xmax", "ymax")},
              "required": ["xmin", "ymin", "xmax", "ymax"], "additionalProperties": False}
LAYERS = {"type": "array", "items": DATASET, "minItems": 1, "maxItems": 12, "uniqueItems": True}


@dataclass(frozen=True)
class Tool:
    description: str
    required: tuple[str, ...]
    writes: bool
    properties: dict
    title: str
    arcgis_tool: str
    host: bool = False
    effect: str = "读取数据；可能将结果提供给当前模型服务"
    raster_output: bool = False

    def schema(self):
        return {"type": "object", "properties": copy.deepcopy(self.properties), "required": list(self.required), "additionalProperties": False}


def tool(title, arcgis_tool, properties=None, *, optional=(), writes=False, host=False, effect=None, raster=False):
    properties = properties or {}
    effect = effect or ("在任务目录创建新成果，保留原始数据" if writes else "读取数据；可能将结果提供给当前模型服务")
    description = f"{title}. ArcGIS: {arcgis_tool}. {effect}. Every call needs user approval."
    return Tool(description, tuple(k for k in properties if k not in optional), writes, properties, title, arcgis_tool, host, effect, raster)


TOOLS = {
    "environment": tool("检查 ArcGIS 环境与许可", "GetInstallInfo / ProductInfo"),
    "project_info": tool("查看已保存工程的地图、图层与布局", "mp.ArcGISProject", {"project": text("Absolute .aprx path; CURRENT only in the Pro Python toolbox")}),
    "describe": tool("查看数据字段、坐标系和数量", "Describe / GetCount", {"dataset": DATASET}),
    "buffer": tool("创建测地线缓冲区", "analysis.Buffer", {"dataset": DATASET, "distance": text("Positive distance plus Meters/Kilometers/Feet/Miles"), "output_name": OUTPUT}, writes=True),
    "list_datasets": tool("浏览工作空间中的数据", "da.Walk", {"workspace": DATASET, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}, optional=("limit",)),
    "count": tool("统计记录数量", "management.GetCount", {"dataset": DATASET}),
    "read_rows": tool("预览属性记录", "da.SearchCursor", {"dataset": DATASET, "fields": FIELDS, "filter": FILTER, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}, optional=("fields", "filter", "limit")),
    "copy_features": tool("复制要素", "management.CopyFeatures", {"dataset": DATASET, "output_name": OUTPUT}, writes=True),
    "clip": tool("裁剪要素", "analysis.Clip", {"dataset": DATASET, "clip_dataset": DATASET, "output_name": OUTPUT}, writes=True),
    "intersect": tool("相交分析", "analysis.Intersect", {"datasets": DATASETS, "output_name": OUTPUT}, writes=True),
    "merge": tool("合并要素数据", "management.Merge", {"datasets": DATASETS, "output_name": OUTPUT}, writes=True),
    "dissolve": tool("按字段融合要素", "management.Dissolve", {"dataset": DATASET, "fields": FIELDS, "output_name": OUTPUT}, optional=("fields",), writes=True),
    "project": tool("投影转换", "management.Project", {"dataset": DATASET, "target_wkid": {"type": "integer", "minimum": 1, "maximum": 999999}, "transformation": text("Exact geographic transformation returned by ArcGIS", 256), "output_name": OUTPUT}, optional=("transformation",), writes=True),
    "spatial_join": tool("空间连接", "analysis.SpatialJoin", {"dataset": DATASET, "join_dataset": DATASET, "match_option": {"type": "string", "enum": ["INTERSECT", "WITHIN", "CONTAINS"]}, "output_name": OUTPUT}, optional=("match_option",), writes=True),
    "select_features": tool("按属性筛选并导出要素", "analysis.Select", {"dataset": DATASET, "filter": FILTER, "output_name": OUTPUT}, writes=True),
    "select_by_location": tool("按位置关系筛选并导出要素", "management.SelectLayerByLocation + CopyFeatures", {"dataset": DATASET, "select_dataset": DATASET, "overlap_type": {"type": "string", "enum": OVERLAP_TYPES}, "search_distance": DISTANCE, "output_name": OUTPUT}, optional=("search_distance",), writes=True, effect="按与另一图层的空间关系筛选输入要素并导出到任务目录地理数据库；WITHIN_A_DISTANCE 类关系必须给 search_distance；源数据不变"),
    "near_analysis": tool("邻近分析：计算最近要素距离", "analysis.Near", {"dataset": DATASET, "near_dataset": DATASET, "output_name": OUTPUT, "search_radius": DISTANCE, "method": {"type": "string", "enum": ["PLANAR", "GEODESIC"]}}, optional=("search_radius", "method"), writes=True, effect="复制源数据后在副本上计算到邻近数据最近要素的 NEAR_FID/NEAR_DIST；源数据不变"),
    "create_thiessen_polygons": tool("由点生成泰森多边形", "analysis.CreateThiessenPolygons", {"dataset": DATASET, "fields_to_copy": {"type": "string", "enum": ["ONLY_FID", "ALL"]}, "output_name": OUTPUT}, optional=("fields_to_copy",), writes=True, effect="由点数据生成泰森多边形新要素类；默认只保留 FID 字段；源数据不变"),
    "add_field": tool("在副本中添加字段", "CopyFeatures/CopyRows + management.AddField", {"dataset": DATASET, "field_name": NEW_FIELD, "field_type": {"type": "string", "enum": ["TEXT", "SHORT", "LONG", "BIGINTEGER", "FLOAT", "DOUBLE"]}, "length": {"type": "integer", "minimum": 1, "maximum": 4000}, "output_name": OUTPUT}, optional=("length",), writes=True),
    "set_field_value": tool("在副本中设置属性值", "CopyFeatures/CopyRows + da.UpdateCursor", {"dataset": DATASET, "field": FIELD, "value": SCALAR, "filter": FILTER, "output_name": OUTPUT}, optional=("filter",), writes=True, effect="复制源数据后更新副本；无 filter 时更新副本的全部记录，保留原始数据"),
    "delete_fields": tool("在副本中删除普通字段", "CopyFeatures/CopyRows + management.DeleteField", {"dataset": DATASET, "fields": FIELDS, "output_name": OUTPUT}, writes=True),
    "calculate_geometry": tool("在副本中计算测地面积或长度", "management.CalculateGeometryAttributes", {"dataset": DATASET, "field_name": NEW_FIELD, "measurement": {"type": "string", "enum": ["AREA_GEODESIC", "LENGTH_GEODESIC"]}, "unit": {"type": "string", "enum": ["SQUARE_METERS", "HECTARES", "SQUARE_KILOMETERS", "METERS", "KILOMETERS", "FEET", "MILES"]}, "output_name": OUTPUT}, writes=True),
    "repair_geometry": tool("在副本中修复几何", "management.RepairGeometry", {"dataset": DATASET, "output_name": OUTPUT}, writes=True),
    "calculate_field": tool("在副本中按算术表达式计算字段", "CopyFeatures + da.UpdateCursor + bounded expression evaluator", {"dataset": DATASET, "field": FIELD, "expression": text("Arithmetic expression over !field! references and numbers, e.g. !price! * 1.05 + 2; only + - * / // % ** are allowed", 512), "filter": FILTER, "output_name": OUTPUT}, optional=("filter",), writes=True, effect="复制源数据后按算术表达式更新副本数值字段，不执行任意代码；源数据不变"),
    "define_projection": tool("在副本上定义坐标系", "CopyFeatures + management.DefineProjection", {"dataset": DATASET, "wkid": {"type": "integer", "minimum": 1, "maximum": 999999}, "output_name": OUTPUT}, writes=True, effect="复制源数据后为副本定义坐标系；源数据不变"),
    "clip_raster": tool("用面要素裁剪栅格", "management.Clip", {"dataset": DATASET, "clip_dataset": DATASET, "output_name": OUTPUT}, writes=True, raster=True),
    "kernel_density": tool("核密度分析", "sa.KernelDensity", {"dataset": DATASET, "cell_size": CELL_SIZE, "output_name": OUTPUT, "value_field": FIELD, "search_radius": RADIUS, "area_unit": {"type": "string", "enum": DENSITY_AREA_UNITS}, "output_values": {"type": "string", "enum": ["DENSITIES", "EXPECTED_COUNTS"]}, "method": {"type": "string", "enum": ["PLANAR", "GEODESIC"]}}, optional=("value_field", "search_radius", "area_unit", "output_values", "method"), writes=True, raster=True, effect="在任务目录生成新的密度栅格（TIFF）；需要 Spatial Analyst，点或线源数据不变"),
    "interpolate_surface": tool("点数据插值表面", "sa.Idw / sa.Kriging", {"dataset": DATASET, "value_field": FIELD, "method": {"type": "string", "enum": ["IDW", "KRIGING"]}, "cell_size": CELL_SIZE, "output_name": OUTPUT, "power": {"type": "number", "exclusiveMinimum": 0, "maximum": 10}, "search_radius": RADIUS, "variogram": {"type": "string", "enum": VARIOGRAM_MODELS}}, optional=("power", "search_radius", "variogram"), writes=True, raster=True, effect="在任务目录生成新的连续表面栅格（TIFF）；需要 Spatial Analyst，点源数据不变"),
    "zonal_statistics": tool("分区统计", "sa.ZonalStatisticsAsTable", {"zone_dataset": DATASET, "zone_field": FIELD, "value_raster": DATASET, "statistic": {"type": "string", "enum": ZONAL_STATISTIC_TYPES}, "output_name": OUTPUT}, writes=True, effect="按分区统计栅格值并输出新表到任务目录地理数据库；需要 Spatial Analyst，忽略分区外的 NoData 像元；源数据不变"),
    "extract_by_mask": tool("按掩膜提取栅格", "sa.ExtractByMask", {"dataset": DATASET, "mask_dataset": DATASET, "extraction_area": {"type": "string", "enum": ["INSIDE", "OUTSIDE"]}, "output_name": OUTPUT}, optional=("extraction_area",), writes=True, raster=True, effect="在任务目录生成新的提取结果栅格（TIFF）；需要 Spatial Analyst，输入栅格与掩膜不变"),
    "sample_raster_at_points": tool("读取栅格值到点位表", "sa.Sample", {"dataset": DATASET, "rasters": RASTERS, "resampling": {"type": "string", "enum": ["NEAREST", "BILINEAR", "CUBIC"]}, "output_name": OUTPUT}, optional=("resampling",), writes=True, effect="将 1-8 个栅格在指定位置的像元值写入任务目录新表；需要 Spatial Analyst，源数据不变"),
    "polygon_to_raster": tool("面转栅格", "conversion.FeatureToRaster", {"dataset": DATASET, "value_field": FIELD, "cell_size": CELL_SIZE, "output_name": OUTPUT}, writes=True, raster=True, effect="按指定字段值在任务目录生成新的整型栅格（TIFF）；面数据不变"),
    "terrain_derivatives": tool("地形派生计算（坡度/坡向）", "sa.Slope / sa.Aspect", {"dataset": DATASET, "derivative": {"type": "string", "enum": ["SLOPE", "ASPECT"]}, "slope_measurement": {"type": "string", "enum": ["DEGREE", "PERCENT_RISE"]}, "z_unit": {"type": "string", "enum": Z_UNITS}, "z_factor": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000000}, "method": {"type": "string", "enum": ["PLANAR", "GEODESIC"]}, "output_name": OUTPUT}, optional=("slope_measurement", "z_unit", "z_factor", "method"), writes=True, raster=True, effect="在任务目录生成新的坡度或坡向栅格（TIFF）；需要 Spatial Analyst，输入 DEM 不变"),
    "contour_lines": tool("生成等高线", "sa.Contour", {"dataset": DATASET, "interval": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000000}, "base_value": {"type": "number", "maximum": 10000000, "minimum": -10000000}, "output_name": OUTPUT}, optional=("base_value",), writes=True, effect="按固定等间距在任务目录地理数据库生成新的等高线要素类；需要 Spatial Analyst，输入 DEM 不变"),
    "reclassify": tool("栅格重分类", "sa.Reclassify", {"dataset": DATASET, "remap": {"type": "array", "items": {"type": "object", "properties": {"start": {"type": "number", "description": "Range start (inclusive)"}, "end": {"type": "number", "description": "Range end (inclusive)"}, "new_value": {"type": "number", "description": "New integer value for the range"}}, "required": ["start", "end", "new_value"], "additionalProperties": False}, "minItems": 1, "maxItems": 32}, "map_nodata_to": {"type": "number", "description": "New value assigned to cells that are currently NoData"}, "output_name": OUTPUT}, optional=("map_nodata_to",), writes=True, raster=True, effect="按区间映射生成新的重分类栅格（TIFF），未列出的区间变为 NoData；需要 Spatial Analyst，源数据不变"),
    "hillshade": tool("山体阴影", "sa.Hillshade", {"dataset": DATASET, "azimuth": {"type": "number", "minimum": 0, "maximum": 360}, "altitude": {"type": "number", "minimum": 0, "maximum": 90}, "shadows": {"type": "string", "enum": ["NO_SHADOWS", "SHADOWS"]}, "z_factor": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000000}, "output_name": OUTPUT}, optional=("azimuth", "altitude", "shadows", "z_factor"), writes=True, raster=True, effect="生成山体阴影栅格（TIFF）；需要 Spatial Analyst，输入 DEM 不变"),
    "resample": tool("栅格重采样", "management.Resample", {"dataset": DATASET, "cell_size": CELL_SIZE, "resampling": {"type": "string", "enum": ["NEAREST", "BILINEAR", "CUBIC", "MAJORITY"]}, "output_name": OUTPUT}, optional=("resampling",), writes=True, raster=True, effect="按新像元大小重采样为新的栅格（TIFF）；源数据不变"),
    "project_raster": tool("栅格投影转换", "management.ProjectRaster", {"dataset": DATASET, "target_wkid": {"type": "integer", "minimum": 1, "maximum": 999999}, "transformation": text("Exact geographic transformation returned by ArcGIS", 256), "resampling": {"type": "string", "enum": ["NEAREST", "BILINEAR", "CUBIC", "MAJORITY"]}, "cell_size": CELL_SIZE, "output_name": OUTPUT}, optional=("transformation", "resampling", "cell_size"), writes=True, raster=True, effect="把栅格转换到目标坐标系生成新栅格（TIFF），datum 不同时须显式指定地理变换；源数据不变"),
    "fill": tool("填洼（水文预处理）", "sa.Fill", {"dataset": DATASET, "z_limit": {"type": "number", "exclusiveMinimum": 0, "maximum": 10000000}, "output_name": OUTPUT}, optional=("z_limit",), writes=True, raster=True, effect="填平 DEM 汇水洼地生成新栅格（TIFF）；需要 Spatial Analyst，源数据不变"),
    "flow_direction": tool("流向计算（水文）", "sa.FlowDirection", {"dataset": DATASET, "flow_direction_type": {"type": "string", "enum": FLOW_TYPES}, "output_name": OUTPUT}, optional=("flow_direction_type",), writes=True, raster=True, effect="由 DEM 生成流向栅格（TIFF），默认 D8；需要 Spatial Analyst，源数据不变"),
    "flow_accumulation": tool("汇流累积量（水文）", "sa.FlowAccumulation", {"dataset": DATASET, "data_type": {"type": "string", "enum": ["FLOAT", "INTEGER"]}, "flow_direction_type": {"type": "string", "enum": FLOW_TYPES}, "output_name": OUTPUT}, optional=("data_type", "flow_direction_type"), writes=True, raster=True, effect="由流向栅格生成汇流累积量栅格（TIFF），输入须为 flow_direction 的输出；需要 Spatial Analyst，源数据不变"),
    "summarize_statistics": tool("属性汇总统计", "analysis.Statistics", {"dataset": DATASET, "stats": {"type": "array", "items": STAT_ITEMS, "minItems": 1, "maxItems": 8}, "case_field": FIELD, "output_name": OUTPUT}, optional=("case_field",), writes=True, effect="按 1-8 个字段/统计组合汇总并输出新表到任务目录地理数据库；源数据不变，不含任意 SQL"),
    "verify_dataset": tool("业务级数据验收（结构化断言）", "Describe / GetCount / da.SearchCursor", {"dataset": DATASET, "min_count": {"type": "integer", "minimum": 0}, "max_count": {"type": "integer", "minimum": 0}, "fields": FIELDS, "non_null_fields": FIELDS, "unique_fields": FIELDS, "wkid": {"type": "integer", "minimum": 1, "maximum": 999999}, "extent_within": MAP_EXTENT}, optional=("min_count", "max_count", "fields", "non_null_fields", "unique_fields", "wkid", "extent_within"), writes=False, effect="只读核验数据的数量、字段存在性、空值、唯一性、坐标系与范围，返回逐项断言结果；全部通过时 passed=true"),
    "export_table_csv": tool("把表或属性导出为 CSV", "da.SearchCursor + csv writer", {"dataset": DATASET, "fields": FIELDS, "filter": FILTER, "output_name": OUTPUT}, optional=("fields", "filter"), writes=True, effect="把数据导出为任务目录下的 <output_name>.csv；源数据不变"),
    "export_features": tool("导出要素为 GeoJSON 或 Shapefile", "conversion.FeaturesToJSON / CopyFeatures", {"dataset": DATASET, "format": {"type": "string", "enum": ["GEOJSON", "SHAPEFILE"]}, "output_name": OUTPUT}, writes=True, effect="在任务目录生成 <output_name>.geojson 或 <output_name>.shp；源数据不变"),
    "join_table": tool("按字段连接属性", "CopyFeatures/CopyRows + management.JoinField", {"dataset": DATASET, "join_dataset": DATASET, "target_field": FIELD, "join_field": FIELD, "fields": FIELDS, "output_name": OUTPUT}, writes=True, effect="复制源数据后在副本上永久连接指定列；先核验字段类型与名称，ArcGIS 静默忽略的列会被判定失败；两份源数据不变"),
    "union": tool("叠合分析（Union）", "analysis.Union", {"datasets": DATASETS, "keep_attributes": {"type": "string", "enum": ["ALL", "ONLY_FID", "NO_FID"]}, "output_name": OUTPUT}, optional=("keep_attributes",), writes=True, effect="2-8 个面要素叠合，输出新要素类到任务目录地理数据库；全部输入必须为面且坐标系一致；源数据不变"),
    "erase": tool("擦除分析（Erase）", "analysis.Erase", {"dataset": DATASET, "erase_dataset": DATASET, "output_name": OUTPUT}, writes=True, effect="用擦除要素切掉输入要素的重叠部分，输出新要素类到任务目录地理数据库；源数据不变"),
    "reshape_geometry": tool("几何整形", "management.FeatureToPoint / cartography.SimplifyPolygon / cartography.SimplifyLine / management.MinimumBoundingGeometry", {"dataset": DATASET, "operation": {"type": "string", "enum": ["CENTROID", "SIMPLIFY", "MIN_BOUNDING"]}, "tolerance": LINEAR_TOLERANCE, "geometry_type": {"type": "string", "enum": BOUNDING_GEOMETRIES}, "output_name": OUTPUT}, optional=("tolerance", "geometry_type"), writes=True, effect="按操作生成中心点、简化或最小外包几何的新要素类；SIMPLIFY 必须给线性容差，MIN_BOUNDING 必须给几何类型；源数据不变"),
    "import_table_points": tool("CSV/Excel 表转点图层", "CreateFeatureclass + da.InsertCursor", {"dataset": text("Absolute .csv/.xls/.xlsx table path; Excel sheets append the sheet name, e.g. data.xlsx\\Sheet1$"), "output_name": OUTPUT, "lat_field": COLUMN, "lon_field": COLUMN, "wkid": {"type": "integer", "minimum": 1, "maximum": 999999}}, optional=("lat_field", "lon_field", "wkid"), writes=True, effect="按表内坐标列在任务目录创建新点要素类；坏坐标行跳过并计数；源表不变"),
    "convert_dataset": tool("转换 KML/GPKG 等矢量数据", "conversion.CopyFeatures / KMLToLayer", {"dataset": DATASET, "output_name": OUTPUT}, writes=True, effect="矢量数据复制为 results.gdb 新要素类；KML/KMZ 解包到任务目录地理数据库并返回成员清单；源数据不变"),
    "build_layout": tool("创建出图工程与布局", "mp.ArcGISProject.createLayout / createMapFrame / createMapSurroundElement", {
        "title": text("图名，同时作为布局名称", 120),
        "layers": LAYERS,
        "output_name": text("新工程文件基名（ASCII），生成 cartography/<name>.aprx", 64, pattern=r"[A-Za-z][A-Za-z0-9_]{0,63}"),
        "page_size": {"type": "string", "enum": PAGE_SIZE_NAMES},
        "orientation": {"type": "string", "enum": ORIENTATIONS},
        "elements": {"type": "array", "items": {"type": "string", "enum": LAYOUT_ELEMENTS}, "minItems": 0, "maxItems": 4, "uniqueItems": True},
        "extent": MAP_EXTENT}, optional=("page_size", "orientation", "elements", "extent"), writes=True,
        effect="在任务目录的 cartography 子目录新建独立出图工程（复制 Pro 自带空白模板，不改动任何已有工程），添加图层、图幅、图名与所选项，再用 export_layout 导出图片"),
    "export_layout": tool("导出布局为图片或 PDF", "mp.Layout.export", {
        "project": text("Absolute .aprx path; CURRENT only in the Pro Python toolbox"),
        "layout": text("Exact layout name returned by project_info or build_layout", 120),
        "format": {"type": "string", "enum": EXPORT_FORMATS},
        "output_name": text("新导出文件基名（ASCII）", 64, pattern=r"[A-Za-z][A-Za-z0-9_]{0,63}"),
        "dpi": {"type": "integer", "minimum": 72, "maximum": 600}}, optional=("layout", "dpi"), writes=True,
        effect="只读打开工程并导出指定布局到任务目录的 cartography 子目录；不保存、不修改工程文件"),
    "list_skills": tool("列出可用的多步任务技能包", "declarative skill library (no ArcPy)", writes=False, effect="读取本地技能清单（名称、用途、参数、步骤数）；不访问 GIS 数据，不执行任何步骤"),
    "read_skill": tool("读取技能包的完整步骤模板", "declarative skill library (no ArcPy)", {"name": text("Exact skill name from list_skills", 64)}, effect="读取本地技能内容（步骤、占位符、注意事项）；不访问 GIS 数据，执行仍须逐个调用工具并单独授权"),
    "map_context": tool("查看 Pro 当前地图和图层", "Pro SDK: Map / Layer", host=True),
    "get_selection": tool("读取并冻结图层当前选择集", "Pro SDK: FeatureLayer.GetSelection", {"map_uri": text("Exact map URI from map_context"), "layer_uri": text("Exact feature layer URI from map_context")}, host=True, effect="读取指定图层选中的要素 ID，保存在本地临时快照；不更改选择或源数据，向模型仅返回数量和快照标识"),
    "export_selection": tool("导出已确认的选择集快照", "management.MakeFeatureLayer + CopyFeatures", {"selection_token": text("Exact live selection_token from get_selection; never invent a token", 32, pattern=r"[0-9a-f]{32}"), "output_name": OUTPUT}, writes=True, host=True, effect="将本会话选择集快照对应的要素导出到任务目录；冻结所选 ID，读取已保存数据，不包含未提交编辑，保留原始数据"),
    "add_to_map": tool("将数据添加到指定地图", "Pro SDK: LayerFactory.CreateLayer", {"dataset": DATASET, "map_uri": text("Exact map URI from map_context")}, writes=True, host=True, effect="向指定的当前 Pro 工程地图添加图层；不自动保存工程"),
    "set_layer_visibility": tool("修改图层可见性", "Pro SDK: Layer.SetVisibility", {"map_uri": text("Exact map URI from map_context"), "layer_uri": text("Exact layer URI from map_context"), "visible": {"type": "boolean"}}, writes=True, host=True, effect="修改指定地图中指定图层的可见性；不自动保存工程"),
}


def _validate(value, schema, label):
    kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    checks = {"string": isinstance(value, str), "integer": type(value) is int, "number": type(value) in (int, float) and math.isfinite(value),
              "boolean": type(value) is bool, "null": value is None, "array": isinstance(value, list), "object": isinstance(value, dict)}
    if not any(checks[k] for k in kinds):
        raise ValueError(f"{label}: invalid type; expected {kinds}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{label}: unsupported choice")
    if isinstance(value, str):
        if any(ord(c) < 32 for c in value) or not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 4096) or (schema.get("minLength", 0) and not value.strip()):
            raise ValueError(f"{label}: invalid text length or control character")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ValueError(f"{label}: invalid name")
    if type(value) in (int, float) and not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
        raise ValueError(f"{label}: out of range")
    if type(value) in (int, float) and value <= schema.get("exclusiveMinimum", -math.inf):
        raise ValueError(f"{label}: must be greater than {schema['exclusiveMinimum']}")
    if isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 32):
            raise ValueError(f"{label}: invalid number of items")
        for item in value:
            _validate(item, schema["items"], label + "[]")
        if schema.get("uniqueItems"):
            keys = [json.dumps(item, sort_keys=True, ensure_ascii=False) if isinstance(item, (list, dict)) else item for item in value]
            if len(set(keys)) != len(value):
                raise ValueError(f"{label}: duplicate items")
    if isinstance(value, dict):
        props = schema["properties"]
        if set(value) - set(props) or set(schema.get("required", [])) - set(value):
            raise ValueError(f"{label}: missing or unknown arguments")
        for key, item in value.items():
            _validate(item, props[key], label + "." + key)


def _validate_filter(value):
    keys = set(value)
    if not keys:
        raise ValueError("filter 不能为空")
    if "all" in keys or "any" in keys:
        if keys - {"all", "any"} or ("all" in keys and "any" in keys):
            raise ValueError("复合 filter 只能是 all 或 any 之一，不能与 field/operator/value/values 混用")
        for leaf in next(iter(value.values())):
            _validate_filter_leaf(leaf)
    else:
        _validate_filter_leaf(value)


def _validate_filter_leaf(leaf):
    operator = leaf["operator"]
    if operator in ("IN", "NOT_IN"):
        if "value" in leaf or "values" not in leaf:
            raise ValueError("IN/NOT_IN 需要 values 列表，不能使用 value")
        if any(item is None for item in leaf["values"]):
            raise ValueError("values 不能包含 null；需要空值判断时改用 any 组合 IS_NULL/IS_NOT_NULL 条件")
    elif operator in ("IS_NULL", "IS_NOT_NULL"):
        if "values" in leaf or leaf.get("value") is not None:
            raise ValueError("IS_NULL/IS_NOT_NULL 的 value 必须为 null 或省略")
    elif operator in ("CONTAINS", "NOT_CONTAINS", "STARTS_WITH"):
        if "values" in leaf or not isinstance(leaf.get("value"), str):
            raise ValueError(f"{operator} 需要文本 value")
    elif "values" in leaf or "value" not in leaf:
        raise ValueError(f"{operator} 需要单值 value")


_EXPRESSION_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load,
                     ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd)


def validate_expression(expression):
    """Bounded field arithmetic: only !field! names, numbers and arithmetic operators."""
    if "__" in expression or "!!" in expression or expression.count("!") % 2:
        raise ValueError("expression 的 ! 必须成对包裹非空字段名，如 !price_km!")
    try:
        tree = ast.parse("".join(expression.split("!")), mode="eval")
    except SyntaxError:
        raise ValueError("expression 语法错误；示例：!price! * 1.05 + 2")
    for node in ast.walk(tree):
        if not isinstance(node, _EXPRESSION_NODES) or (isinstance(node, ast.Constant) and type(node.value) not in (int, float)):
            raise ValueError(f"expression 只支持字段与数字的算术运算（+ - * / // % **），不支持 {type(node).__name__}")


def validate_call(name, arguments):
    if not isinstance(name, str) or name not in TOOLS:
        raise ValueError(f"Unknown tool: {name}")
    _validate(arguments, TOOLS[name].schema(), name)
    if name in FILTER_TOOLS and "filter" in arguments:
        _validate_filter(arguments["filter"])
    if name == "buffer":
        value = arguments["distance"]
        if not DISTANCE_RE.fullmatch(value) or not 0 < float(value.split()[0]) < math.inf:
            raise ValueError("distance must be a finite positive number and Meters/Kilometers/Feet/Miles")
    if name in ("select_by_location", "near_analysis"):
        key = "search_distance" if name == "select_by_location" else "search_radius"
        if key in arguments and not DISTANCE_RE.fullmatch(arguments[key]):
            raise ValueError(f"{key} must be a finite positive number and Meters/Kilometers/Feet/Miles")
        if name == "select_by_location":
            distance_required = arguments["overlap_type"].startswith("WITHIN_A_DISTANCE")
            if distance_required and "search_distance" not in arguments:
                raise ValueError("WITHIN_A_DISTANCE 类空间关系必须提供 search_distance")
            if not distance_required and "search_distance" in arguments:
                raise ValueError("search_distance 仅适用于 WITHIN_A_DISTANCE / WITHIN_A_DISTANCE_GEODESIC 关系")
    if name == "calculate_field":
        validate_expression(arguments["expression"])
        if arguments["field"] in re.findall(r"!([^!]+)!", arguments["expression"]):
            raise ValueError("目标字段不能在表达式中引用自身；先计算到新字段")
    if name == "reclassify":
        for item in arguments["remap"]:
            if item["end"] < item["start"]:
                raise ValueError("remap 区间的 end 不能小于 start")
    if name == "verify_dataset" and "extent_within" in arguments:
        box = arguments["extent_within"]
        if box["xmax"] <= box["xmin"] or box["ymax"] <= box["ymin"]:
            raise ValueError("extent_within 需要满足 xmax>xmin 且 ymax>ymin")
    if name == "buffer":
        value = arguments["distance"]
        if not re.fullmatch(r"(?:\d+(?:\.\d+)?|\.\d+)\s+(?:Meters|Kilometers|Feet|Miles)", value) or not 0 < float(value.split()[0]) < math.inf:
            raise ValueError("distance must be a finite positive number and Meters/Kilometers/Feet/Miles")
    if name == "calculate_geometry":
        area = arguments["measurement"] == "AREA_GEODESIC"
        if (arguments["unit"] in ("SQUARE_METERS", "HECTARES", "SQUARE_KILOMETERS")) != area:
            raise ValueError("Geometry measurement and units do not match")
    if name == "import_table_points":
        source = arguments["dataset"].lower()
        if not source.endswith((".csv", ".xls", ".xlsx")) and not arguments["dataset"].endswith("$"):
            raise ValueError("import_table_points needs a .csv/.xls/.xlsx table; Excel sheets append a Sheet1$ suffix")
    if name == "interpolate_surface":
        method = arguments["method"]
        if method == "IDW" and "variogram" in arguments:
            raise ValueError("variogram applies to KRIGING only; IDW uses power")
        if method == "KRIGING" and "power" in arguments:
            raise ValueError("power applies to IDW only; KRIGING uses variogram")
    if name == "terrain_derivatives" and arguments["derivative"] != "SLOPE" and "slope_measurement" in arguments:
        raise ValueError("slope_measurement applies to SLOPE only")
    if name == "build_layout":
        elements = arguments.get("elements") or []
        if "SCALE_BAR" in elements and "DUAL_SCALE_BAR" in elements:
            raise ValueError("SCALE_BAR 与 DUAL_SCALE_BAR 只能二选一，两者会重叠在同一位置")
        if "extent" in arguments:
            box = arguments["extent"]
            if box["xmax"] <= box["xmin"] or box["ymax"] <= box["ymin"]:
                raise ValueError("extent 需要满足 xmax>xmin 且 ymax>ymin")
    if name == "reshape_geometry":
        operation = arguments["operation"]
        if operation == "SIMPLIFY":
            if "tolerance" not in arguments:
                raise ValueError("SIMPLIFY requires a tolerance such as 50 Meters")
            if not 0 < float(arguments["tolerance"].split()[0]) < math.inf:
                raise ValueError("tolerance must be a finite positive number")
        elif "tolerance" in arguments:
            raise ValueError(f"tolerance applies to SIMPLIFY only, not {operation}")
        if operation == "MIN_BOUNDING":
            if "geometry_type" not in arguments:
                raise ValueError("MIN_BOUNDING requires a geometry_type")
        elif "geometry_type" in arguments:
            raise ValueError(f"geometry_type applies to MIN_BOUNDING only, not {operation}")


def approval_details(name, arguments, output_dir: Path):
    item = TOOLS[name]
    outputs = []
    if item.writes and (not item.host or name == "export_selection"):
        if name == "convert_dataset" and arguments["dataset"].lower().endswith((".kml", ".kmz")):
            outputs = [str(output_dir.resolve() / ("kml_" + arguments["output_name"]) / (arguments["output_name"] + ".gdb"))]
        elif name == "build_layout":
            outputs = [str(output_dir.resolve() / "cartography" / (arguments["output_name"] + ".aprx"))]
        elif name == "export_layout":
            outputs = [str(output_dir.resolve() / "cartography" / (arguments["output_name"] + "." + EXPORT_EXTENSIONS[arguments["format"]]))]
        elif name == "export_table_csv":
            outputs = [str(output_dir.resolve() / (arguments["output_name"] + ".csv"))]
        elif name == "export_features":
            outputs = [str(output_dir.resolve() / (arguments["output_name"] + (".geojson" if arguments["format"] == "GEOJSON" else ".shp")))]
        elif item.raster_output:
            outputs = [str(output_dir.resolve() / (arguments["output_name"] + ".tif"))]
        else:
            outputs = [str(output_dir.resolve() / "results.gdb" / arguments["output_name"])]
    return {"title": item.title, "arcgis_tool": item.arcgis_tool, "writes": item.writes, "host": item.host,
            "effect": item.effect, "expected_outputs": outputs}
