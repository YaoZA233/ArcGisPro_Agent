# 工具与单次授权

Agent 通过类型化白名单调用 ArcPy 或 Pro SDK，不执行模型生成的任意代码。CLI、Pro 聊天面板和 Python 工具箱每次调用均先向用户询问，包括读取。一个任务包含多个工具时，各自拥有独立授权；拒绝或取消后不继续后续步骤，先前成果保留。

CLI 使用 `y / n / c`，回车默认拒绝。面板确认卡片展示工具、参数、影响和预计输出，授权有效期 10 分钟，默认焦点为拒绝；“允许本次写入”只针对该次操作。全局 `--allow-writes` 或面板复选框均不跳过确认。未提供授权处理器的 Python 调用返回 `approval_required`，不执行工具。

## 工具目录

下表用 `?` 表示可选参数。`dataset` 等数据路径应为绝对路径，GDB 内数据须使用完整目录路径。运行 `./scripts/agent.ps1 tools` 可查询从同一注册表生成的完整参数 JSON Schema。

| 工具 | 参数 | 操作 |
| --- | --- | --- |
| `environment` | 无 | 读取实际版本、Python、许可证 |
| `project_info` | `project` | 读取已保存 `.aprx` 的地图、图层、布局清单（名称、页面尺寸与单位、元素数量）和损坏标记；在线服务与分组图层不做联网损坏探测，`is_broken` 为 null 并附 `broken_check` 说明；工具箱内可用 CURRENT |
| `describe` | `dataset` | 读取类型、坐标系、范围、字段元数据（最多 200 个）、适用时的数量 |
| `list_datasets` | `workspace`, `limit?` | 浏览工作空间内要素、表、栅格，默认最多 100 项，上限 500 |
| `count` | `dataset` | 统计记录数 |
| `read_rows` | `dataset`, `fields?`, `filter?`, `limit?` | 属性预览，默认 100 行 / 12 字段，最多 500 行 / 32 字段 |
| `verify_dataset` | `dataset`, `min_count?`, `max_count?`, `fields?`, `non_null_fields?`, `unique_fields?`, `wkid?`, `extent_within?` | 只读结构化验收：逐项核验数量区间、字段存在、空值、唯一性、坐标系与范围，返回 `checks` 明细与整体 `passed`；断言失败时明确列出 `failures` |
| `buffer` | `dataset`, `distance`, `output_name` | 测地线缓冲区 |
| `copy_features` | `dataset`, `output_name` | 复制要素 |
| `clip` | `dataset`, `clip_dataset`, `output_name` | 用面要素裁剪 |
| `intersect` | `datasets`, `output_name` | 2–8 个输入相交 |
| `merge` | `datasets`, `output_name` | 2–8 个输入合并 |
| `dissolve` | `dataset`, `fields?`, `output_name` | 按字段融合；省略字段时全体融合 |
| `project` | `dataset`, `target_wkid`, `transformation?`, `output_name` | 投影转换，datum 不同时须显式指定适用的地理变换 |
| `spatial_join` | `dataset`, `join_dataset`, `match_option?`, `output_name` | 一对一、保留全部目标的空间连接，默认 INTERSECT；另支持 WITHIN / CONTAINS |
| `select_features` | `dataset`, `filter`, `output_name` | 按结构化条件（含复合 `all`/`any`、`IN` 列表与文本 `CONTAINS`/`STARTS_WITH`）筛选并导出 |
| `select_by_location` | `dataset`, `select_dataset`, `overlap_type`, `search_distance?`, `output_name` | 按空间关系（INTERSECT / WITHIN_A_DISTANCE / CONTAINS / WITHIN / CROSSED_BY_OUTLINE_OF 等）筛选并导出；WITHIN_A_DISTANCE 类必须给 `search_distance` |
| `near_analysis` | `dataset`, `near_dataset`, `search_radius?`, `method?`, `output_name` | 复制后在副本上计算最近要素的 NEAR_FID/NEAR_DIST，返回 `matched_count`；支持 PLANAR / GEODESIC |
| `create_thiessen_polygons` | `dataset`, `fields_to_copy?`, `output_name` | 由点生成泰森多边形，默认 `ONLY_FID`，可选 `ALL` 复制属性 |
| `clip_raster` | `dataset`, `clip_dataset`, `output_name` | 用面几何裁剪栅格，生成 TIFF |
| `interpolate_surface` | `dataset`, `value_field`, `method`, `cell_size`, `power?`, `variogram?`, `search_radius?`, `output_name` | IDW（`power`）或 Kriging（`variogram`）点值插值栅格 |
| `kernel_density` | `dataset`, `cell_size`, `value_field?`, `search_radius?`, `area_unit?`, `output_values?`, `method?`, `output_name` | 点、线要素核密度；省略 `value_field` 时按计数 |
| `polygon_to_raster` | `dataset`, `value_field`, `cell_size`, `output_name` | 面转栅格，`value_field` 为整型字段 |
| `terrain_derivatives` | `dataset`, `derivative`, `method?`, `slope_measurement?`, `z_factor?`, `z_unit?`, `output_name` | 坡度 `SLOPE` 或坡向 `ASPECT` |
| `contour_lines` | `dataset`, `interval`, `base_value?`, `output_name` | 栅格等高线折线要素 |
| `zonal_statistics` | `zone_dataset`, `zone_field`, `value_raster`, `statistic`, `output_name` | 分区统计汇总表；`zone_field` 必须为整数字段 |
| `extract_by_mask` | `dataset`, `mask_dataset`, `extraction_area?`, `output_name` | 按掩膜要素提取栅格，`INSIDE`（默认）/`OUTSIDE` |
| `sample_raster_at_points` | `dataset`, `rasters`, `resampling?`, `output_name` | 在点、线、面位置取值，最多 8 个栅格，输出属性表 |
| `reclassify` | `dataset`, `remap`, `map_nodata_to?`, `output_name` | 按区间映射重分类为整型栅格（TIFF），`remap` 为 `[{start, end, new_value}]`，未列出区间变 NoData，可选把 NoData 映射为给定值 |
| `hillshade` | `dataset`, `azimuth?`, `altitude?`, `shadows?`, `z_factor?`, `output_name` | 山体阴影栅格，默认方位角 315、高度角 45、无阴影 |
| `resample` | `dataset`, `cell_size`, `resampling?`, `output_name` | 按新像元大小重采样（NEAREST / BILINEAR / CUBIC / MAJORITY） |
| `project_raster` | `dataset`, `target_wkid`, `transformation?`, `resampling?`, `cell_size?`, `output_name` | 栅格投影转换，datum 不同时须显式给地理变换 |
| `fill` | `dataset`, `z_limit?`, `output_name` | 填平 DEM 洼地（水文链第一步） |
| `flow_direction` | `dataset`, `flow_direction_type?`, `output_name` | 流向栅格，D8（默认）/ MFD / DINF |
| `flow_accumulation` | `dataset`, `data_type?`, `flow_direction_type?`, `output_name` | 汇流累积量栅格，输入须为 `flow_direction` 输出 |
| `summarize_statistics` | `dataset`, `stats`, `case_field?`, `output_name` | 字段统计表；`stats` 为 `[{"field","statistic"}]`，可按 `case_field` 分组 |
| `join_table` | `dataset`, `join_dataset`, `target_field`, `join_field`, `fields`, `output_name` | 把表字段连接到数据副本；不改变匹配范围 |
| `union` | `datasets`, `keep_attributes?`, `output_name` | 2–8 个面要素叠合，输入需同一坐标系 |
| `erase` | `dataset`, `erase_dataset`, `output_name` | 用面或线擦除输入要素 |
| `reshape_geometry` | `dataset`, `operation`, `tolerance?`, `geometry_type?`, `output_name` | `CENTROID` 中心点 / `SIMPLIFY` 简化（需线性 `tolerance`）/ `MIN_BOUNDING` 最小外包（需 `geometry_type`） |
| `add_field` | `dataset`, `field_name`, `field_type`, `length?`, `output_name` | 复制后新增 TEXT / SHORT / LONG / BIGINTEGER / FLOAT / DOUBLE 字段 |
| `set_field_value` | `dataset`, `field`, `value`, `filter?`, `output_name` | 复制后设定常量；无 filter 时更新副本全部记录 |
| `delete_fields` | `dataset`, `fields`, `output_name` | 复制后删除普通字段，不允许删除系统必需字段 |
| `calculate_geometry` | `dataset`, `field_name`, `measurement`, `unit`, `output_name` | 复制后新增 DOUBLE 字段，计算测地面积或长度 |
| `repair_geometry` | `dataset`, `output_name` | 在副本上修复几何，保留空几何 |
| `calculate_field` | `dataset`, `field`, `expression`, `filter?`, `output_name` | 复制后按算术表达式更新副本数值字段：仅允许 `!字段!` 引用、数字与 `+ - * / // % **`，空值或非数值参与计算会报错；目标字段不可引用自身 |
| `define_projection` | `dataset`, `wkid`, `output_name` | 复制后为副本定义坐标系（不改源数据） |
| `export_table_csv` | `dataset`, `fields?`, `filter?`, `output_name` | 导出任务目录 `<output_name>.csv`（UTF-8 BOM，Excel 可直接打开），支持复合筛选 |
| `export_features` | `dataset`, `format`, `output_name` | 导出 `GEOJSON`（`<output_name>.geojson`，转 WGS84）或 `SHAPEFILE`（`<output_name>.shp`） |
| `import_table_points` | `dataset`, `output_name`, `lat_field?`, `lon_field?`, `wkid?` | CSV/Excel 表转点要素类；自动识别经纬度列或显式指定，坏坐标行跳过并计数；默认 WKID 4326；Excel 表需带 `Sheet1$` 之类工作表后缀 |
| `convert_dataset` | `dataset`, `output_name` | GPKG 等矢量数据复制为 results.gdb 新要素类；KML/KMZ 解包到任务目录地理数据库并返回成员清单，可再用 copy_features 选入 results.gdb |
| `build_layout` | `title`, `layers`, `output_name`, `page_size?`, `orientation?`, `elements?`, `extent?` | 复制 Pro 自带空白模板，在任务目录新建出图工程：一个地图 + 一个图幅 + 图名 + 图廓元素，保存为 `cartography/<output_name>.aprx`，返回工程路径、布局名、图层名、`page_mm`、`elements`、`spatial_reference`（图层坐标系名与 wkid）、`extent` 与 `extent_source`（`requested` / `data_extent`） |
| `export_layout` | `project`, `layout?`, `format`, `output_name`, `dpi?` | 只读打开 `.aprx` 并把布局导出为 `cartography/<output_name>.<扩展名>`；不保存、不改动工程文件 |
| `list_skills` | 无 | 列出声明式多步技能包（名称、用途、参数、步骤数、来源）；只读本地 JSON，不访问 GIS 数据 |
| `read_skill` | `name` | 读取技能包完整步骤模板与注意事项；只读本地 JSON，执行仍须逐个调用工具并单独授权 |
| `map_context` | 无 | Pro 面板专用：读取当前会话地图、图层 URI、可见性、支持的本地数据路径 |
| `get_selection` | `map_uri`, `layer_uri` | Pro 面板专用：读取并冻结指定图层的当前选择，只返回数量与快照标识 |
| `export_selection` | `selection_token`, `output_name` | Pro 面板专用：把本会话快照内的要素导出到任务目录，不含未提交编辑 |
| `add_to_map` | `dataset`, `map_uri` | Pro 面板专用：将数据添加到批准的目标地图 |
| `set_layer_visibility` | `map_uri`, `layer_uri`, `visible` | Pro 面板专用：修改指定图层可见性 |

当前地图工具最多返回 50 个地图、每图 200 个图层，并标记截断；服务地址不作为本地数据路径返回。地图修改不自动保存工程。选择集快照有效期 30 分钟、最多保留 8 份，要素 ID 不进入模型历史；空选择不会被视为整层。这五个宿主工具只出现在 Pro 面板会话，CLI 和 Python 工具箱的模型声明中不含它们。

## 空间分析与栅格约定

`interpolate_surface`、`kernel_density`、`zonal_statistics`、`extract_by_mask`、`sample_raster_at_points`、`terrain_derivatives`、`contour_lines`、`reclassify`、`hillshade`、`fill`、`flow_direction`、`flow_accumulation` 需要 Spatial Analyst。执行前先在子进程签出扩展，签出失败时整次调用直接失败并说明许可证状态，**不会退回部分结果**。`polygon_to_raster`、`join_table`、`summarize_statistics`、`union`、`erase`、`reshape_geometry`、`resample`、`project_raster` 只需核心地理处理许可（`select_by_location`、`near_analysis`、`create_thiessen_polygons` 亦为核心理由处理，但部分如 Near/泰森需要标准版或更高许可级别）。

`cell_size`、`interval`、`search_radius`、`z_factor`、`power` 为纯数字，使用输入数据的线性单位，不接受单位后缀：地理坐标系（度）数据必须填写很小的度值（如 `0.0005`），填 `300` 会生成越界范围并触发 ArcGIS ERROR 999999。分析子进程统一重置 `extent`、`outputCoordinateSystem`、`cellSize`、`mask`、`snapRaster`，不继承 Pro 会话或上一次工具的设置。

枚举取值以本机 ArcGIS Pro 3.5.2 实测为准：

- `zonal_statistics.statistic`：`ALL / MEAN / MAXIMUM / MEDIAN / MINIMUM / RANGE / STD / SUM / MIN_MAX / MEAN_STD / MIN_MAX_MEAN`（此工具没有 `COUNT`），且 `zone_field` 必须是整数字段；文本分区字段会在校验后拒绝执行。
- `summarize_statistics.stats[].statistic`：`SUM / MEAN / MIN / MAX / RANGE / STD / COUNT / FIRST / LAST / MEDIAN / VARIANCE / UNIQUE / CONCATENATE / MODE`，其中 `CONCATENATE` 只用于文本字段，其余统计只用于数值字段。
- `interpolate_surface.method`：`IDW` 配 `power`，`KRIGING` 配 `variogram`（`SPHERICAL / EXPONENTIAL / GAUSSIAN / LINEAR / POWER`），两者互斥由参数校验保证。
- `reshape_geometry`：`SIMPLIFY` 必须给 `Meters / Kilometers / Feet / Miles` 形式的线性容差（`Percent` 不接受），`MIN_BOUNDING` 必须给 `RECTANGLE_BY_AREA / RECTANGLE_BY_WIDTH / CONVEX_HULL / CIRCLE / ENVELOPE`；`MIN_BOUNDING` 不分组，一次调用返回一个整体外包几何。
- `join_table` 只提供属性连接，没有“保留全部 / 仅保留匹配”开关（本机 `JoinField` 无该参数），因此始终返回副本的全部记录；请求的远端列不存在时直接失败，不静默跳过。
- `union` 要求全部输入为面且坐标系一致，否则在调用 ArcGIS 前拒绝。
- `select_by_location.overlap_type`：`INTERSECT / WITHIN_A_DISTANCE / WITHIN_A_DISTANCE_GEODESIC / CONTAINS / COMPLETELY_CONTAINS / CONTAINS_CLEMENTINI / WITHIN / COMPLETELY_WITHIN / WITHIN_CLEMENTINI / ARE_IDENTICAL_TO / BOUNDARY_TOUCHES / CROSSED_BY_OUTLINE_OF / HAVE_THEIR_CENTER_IN`（本机 3.5.2 `SelectLayerByLocation` 实测取值）；`WITHIN_A_DISTANCE` 类必须给 `search_distance`，其余关系给了会被拒绝。
- `reclassify.remap` 区间 `end` 不得小于 `start`；`hillshade.azimuth` 限 0–360、`altitude` 限 0–90；`flow_direction`/`flow_accumulation` 的 `flow_direction_type` 为 `D8 / MFD / DINF`。
- `calculate_field` 表达式仅允许 `!field!`、数字字面量与算术运算符（`+ - * / // % **` 及一元正负），AST 白名单在编译期拒绝函数调用、下标、字符串与逻辑运算；引用字段必须为数值字段且行值非空，目标字段不可自引用。

## 制图与出图约定

出图分两步，各自独立授权：

1. `build_layout` 复制本机安装自带的 `Resources/ArcToolBox/Services/routingservices/data/Blank.aprx`，在 `outputs/cartography/<output_name>.aprx` 新建一个只含一张地图和一个布局的工程，加入 `layers`（1–12 个，须有几何；表格会被拒绝）、图幅、图名（同时作为布局名称）和 `elements` 选中的图廓元素，然后保存这个**新建的**工程。默认 `A4` 横向、`["LEGEND","NORTH_ARROW","SCALE_BAR"]`；`page_size` 另有 `A3 / A2 / A1 / LETTER / TABLOID`，`orientation` 另有 `PORTRAIT`，`elements` 可传空数组去掉图廓，`SCALE_BAR` 与 `DUAL_SCALE_BAR` 互斥。
2. `export_layout` 用上一步返回的 `project` 与 `layout` 名称（或 `project_info` 读到的布局）导出 `PNG / JPEG / PDF / TIFF / SVG`，`dpi` 限 72–600，默认 300。它**只读打开**工程，从不调用 `save()`，返回里的 `project_modified` 恒为 `false`；真实冒烟用导出前后的文件摘要核验这一点。

`extent` 省略时取全部图层范围的并集；各图层坐标系不一致会在复制模板前拒绝，需先用 `project` 统一坐标系或显式给出 `extent`（`xmax>xmin` 且 `ymax>ymin`，使用图层自身的线性单位）。图层是地理坐标系而 `extent` 给出经度 ±180／纬度 ±90 之外的数值时直接拒绝，因为 `camera.setExtent` 按地图自身的单位读数，米制数值只会导出一张空图。所有可能被拒绝的检查都排在复制模板之前，因此被拒的调用不会残留半成品 `.aprx`、也不会创建 `cartography` 目录；建图或导出中途失败会删除自己创建的文件。`project` 参数在 Pro 工具箱内可用 `CURRENT`，此时同样不保存用户工程。

本机 3.5.2 实测被运行时拒绝、因此工具不提供的能力：`georeferenceMapFrame`/`worldFile` 导出选项（任意取值都报 ValueError）、按格式设置色彩模式（PNG 无该属性，TIFF/PNG 只接受枚举真彩色值）。图名与图廓元素用 `arcpy.mp` 原生 API 与 CIM 文本图形实现，样式取 Pro 默认样式库（`ArcGIS 2D` 的 NORTH_ARROW / SCALE_BAR / LEGEND 列表项）。

## 失败守卫与提示

- 路径以 `.gdb` 结尾但并非真实地理数据库（如普通目录）时，读取工具直接报明确错误，避免难懂的 ArcPy 底层异常。
- worker 失败时按常见故障模式附加 `hint` 恢复建议（假地理数据库、数据集不存在、枚举取值越界、缺地理变换、路径含空格、栅格统计缺失或像元过大、许可证级别不足）；超时附加网络路径与数据范围排查建议。

## 筛选和编辑

筛选使用真实字段名和结构化值，不接受 SQL 字符串或 Python 表达式。单条件：

```json
{"field": "land_type", "operator": "EQ", "value": "A"}
```

支持 `EQ / NE / GT / GE / LT / LE / IS_NULL / IS_NOT_NULL`（后两者的 value 必须为 null）、`IN / NOT_IN`（配 `values` 列表，最多 100 项，不能含 null）和文本专用 `CONTAINS / NOT_CONTAINS / STARTS_WITH`（配单值）。复合条件用 `all`（全部满足）或 `any`（任一满足），每个最多 8 个单条件叶子，不支持嵌套：

```json
{"all": [{"field": "score", "operator": "GE", "value": 2},
         {"any_note": "all 与 any 不能同时出现"}]}
{"any": [{"field": "land_type", "operator": "EQ", "value": "A"},
         {"field": "score", "operator": "GT", "value": 10}]}
```

字段须与 `describe` 返回的名称一致，文本与数值条件会按字段类型核验。`read_rows` 的每格文本最多 1024 字符，总文本约 60000 字符，存在裁剪时返回标记。

`set_field_value` 支持与字段类型、长度、范围一致的文本或数值常量，以及字段允许的 null。不提供任意代码形式的字段计算；`calculate_field` 只开放受限算术表达式（`!字段!`、数字与 `+ - * / // % **`），在编译期白名单校验、运行期逐行求值，遇到空值或非数值直接失败。字段编辑支持要素类与普通表，系统字段不可修改。

`calculate_geometry` 支持 `AREA_GEODESIC`（`SQUARE_METERS / HECTARES / SQUARE_KILOMETERS`）和 `LENGTH_GEODESIC`（`METERS / KILOMETERS / FEET / MILES`）。几何类型应与测量匹配。`buffer` 距离示例为 `500 Meters`，单位另支持 Kilometers / Feet / Miles。

所有数据写工具生成 `workspace/runs/<唯一任务>/outputs/results.gdb/<output_name>` 或 `outputs/<output_name>.tif`；出图工具生成 `outputs/cartography/<output_name>.aprx` 与同目录的图片/PDF。名称为字母开头的 ASCII 字母、数字或下划线，最多 64 字符；不允许自行指定任意输出目录，已有成果拒绝覆盖。编辑会保留原数据。失败或取消不会回滚此前已创建的成果，地图变化也不自动撤销。

## 技能包

`list_skills` / `read_skill` 提供声明式多步方法模板（水文链、选址、质量检查、插值成图、坐标统一、表格转密度图、副本编辑交付共 7 个内置技能）。技能只被模型读取参考，不自动执行；占位符 `{{params.x}}` 与 `{{steps.<output_name>.<字段>}}` 由模型替换为真实值后逐个发起普通工具调用，每步仍单独授权。技能文件加载时对照工具注册表严格校验。格式、用户自定义目录与边界见 [技能说明](skills.md)。

## 自然语言示例

- “查看当前地图有哪些图层，告诉我道路数据在哪里。”
- “检查 E:/GIS/data.gdb/parcels 的字段，筛选 land_type 等于 A 的要素，再做 50 米缓冲区并统计数量。”
- “在道路数据副本里新增 reviewed 整数字段，设为 1，保留原数据。”
- “用采样点的 zval 做 IDW 插值，像元 0.0005 度，再按分区面统计各区的平均高程。”
- “把刚生成的成果添加到这张地图，再隐藏原始图层。”
- “用缓冲区结果和分区面做一个 A3 横图的出图工程，带图例、指北针和双比例尺，再导出 300 dpi 的 PNG。”

Agent 使用前一个工具返回的完整路径继续分析，每次都询问。工具成功只说明该操作完成；坐标系选择、分析方法和结果是否满足业务目的仍需核验。

ArcPy 子进程不继承 Pro 选择集或未提交数据编辑。处理选中要素前，应提供可复现的属性条件或先导出选择集。`map_context` 能读当前地图状态，不表示后台 ArcPy 能读取 CURRENT。

静态计划示例见 [筛选计划](../examples/select_features.json)，替换其中路径和真实字段名后运行 `./scripts/agent.ps1 run examples/select_features.json`。静态计划不插值前一步输出路径；需要动态串联成果时使用自然语言聊天。
