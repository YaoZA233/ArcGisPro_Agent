# 本机验证记录

## 批次 10 符号化与标注（2026-11-06）

本批次给 `build_layout` 增加两个参数（`styles` 色板符号化、`labels` 图层标注），不新增 LLM 可见工具、不新增写路径，只读不变量不变。符号化只走**受控预设色板**（BLUES/GREENS/RD_BU/REDS/CATEGORY10/LAND_USE/BASE/ELEVATION/HILLSHADE_GRAY），不给模型自由调色参数；分级只支持**等距分位（EQUAL_INTERVAL）与分位数（QUANTILE）**，无 Jenks、无手动断点。

- 系统 Python 3.14.6：`PYTHONPATH=src python -m unittest discover -s tests` **452 项离线测试全部通过**（6.87s，`test.ps1` 同批）。本批次新增 `tests/test_symbology.py` 114 项（色板完整性契约、断点/唯一值计算、样式与标注校验、矢量/栅格 CIM 生成、标注写回、拒绝路径）与 `tests/test_cartography.py` 的 `SymbologyWiringTests` 10 项（`build_layout` 接线：前置校验先于复制模板、返回 `symbology`/`labels` 段、definition 落到图层、无样式图层不碰 CIM、图名颜色走 `values` 而非关键字的回归）。既有 `test_approvals.py` 62 断言与批次 3 的 cartography 契约测试均未回归。
- Pro Python 3.11 / ArcGIS Pro 3.5.2 真实运行 `scripts/smoke_symbology.py`（两次独立运行）：**41 项检查全部通过**（`ok: true`，`version: "3.5.2"`，`hillshade_available: true`，耗时约 240s，退出码 0），9 条拒绝路径各返回预期中文错误且零残留，合成 fixture 仅写入 `workspace/smoke/symbology_<uuid>`。
- **色板真的落到图上（颜色直方图，比 `ink()` 强一个量级——既证配色又证分级归属）**：
  - 分级色彩 BLUES 5 档全部命中（容差 ≤4/通道），导出图各档像素数 `[222441, 221446, 110556, 221445, 220780]`，与要素实际分布 `[2,2,1,2,2]` 吻合——每档"像素/要素"近似恒定（约 11.05 万，最大偏差 0.45%），证明第 3 档只有 1 个要素、其余各 2 个。断点 `[25.8,46.6,67.4,88.2,109.0]`（min 5.0 / max 109.0）。
  - 唯一值 CATEGORY10 前 5 色全部命中，类别 `[建成区,林地,水体,耕地,草地]` 分布 `[2,2,2,2,1]`，"像素/类别"最大偏差 0.15%。
  - 土地利用 LAND_USE 语义色：水体命中 `#3B7DBF`；注入的"盐田"类未命中预设，返回 `symbology[0]["unmatched"] == ["盐田"]` 并回退灰 `#9E9E9E`。
  - 简单符号 BASE：面 `#DCE6F1`、线 `#4D4D4D`、点 `#3B6E8F` 各自命中。
  - 栅格 ELEVATION 7 档色带全部命中（连续拉伸，`distinct_colors 455`）。
- **标注实测**：带标注 vs 不带标注两次导出，深色像素 `229156 → 232818`、纯黑像素 `7308 → 10155`（净增 2847）证明字形真的渲染；白底对照图同向增长。字色由常量驱动：默认 `LABEL_COLOR #000000` → 回读 glyph `[0,0,0,100]`；把常量临时换成 `#C00000` 后回读 `[192,0,0,100]`，证明不是硬编码。
- **图名颜色修正（离线有确定性证据，真机不可判定来源）**：`_add_title` 改用 `CIMRGBColor().values = [0,0,0,100]`（0–100 量纲）——旧的 `red=/green=/blue=` 关键字会被 CIM 静默忽略、`.values` 从未写入。这条"写入真的落到 `values`"由离线 `test_title_color_goes_through_values_not_keywords` 钉住（构造期拦截，退回关键字即 TypeError）。但真机导出只证明"图名是黑字"，**无法**区分这黑色来自我们的 `values` 还是 Pro 默认黑：同一 `symbol` 上请求的 20pt/加粗/居中在导出里完全没生效（`title_band` 实测字形约 9.3pt、左对齐、reload 后 `symbol` 回读 `None`），说明该 CIMGraphic 文本元素的 symbol 未被渲染路径采纳（详见下方已知缺陷）。
- **只读与源数据不变量**：导出前后 `.aprx` SHA256 一致；`input.gdb` 的 `changed`/`deleted` 均空、要素计数前后一致（仅 `.sr.lock`/`.aux.xml`/金字塔等渲染缓存副作用）。9 条拒绝逐目录扫描确认零半成品残留。抽样开关实测：把 `VALUE_CAP` 调小后返回 `sampled: true`（正常路径 `sampled: false`）。
- **本批次把设计里四个"离线无法判定、需真机确认"的假设逐条钉死**：
  1. `runtime_fill_class_name_is_cimsolidfill`——矢量填充节点在跨进程 save/reload 后 `type(obj).__name__` 确为 `CIMSolidFill`；CIM 对象虽是 dict，但**无** `__type__` 判别字段（`definition_has_json_attribute: false`），探测 dump 里看到的 `__type__` 就是 Python 类名。分派依据只能是 Python 类名。
  2. `raster_ramp_property_spellings_live` + `raster_ramp_needs_no_weights`——栅格走 `definition.colorizer`（`renderer` 为 `None`），`colorizer.colorRamp` 是 `CIMMultipartColorRamp`，7 色 = 6 段 `CIMLinearContinuousColorRamp`（`fromColor`/`toColor`），`weights` 为空即生效。
  3. `label_container_round_trip_renders`——改现有 `labelClasses[0]`（非整表替换）真的渲染；回读用 `.height=18`，`fontSize` 属性在回读时 `absent`（故写回时 fontSize 与 height 都设，渲染以 height 为准）；`expression "$feature.NAME"`、`expressionEngine "Arcade"`、`labelVisibility true`。
  4. `title_color_still_black_after_the_values_fix`——图名导出为黑字（黑色占比 0.5035、主字形模态 `#000000` 1266 像素）；但如"图名颜色修正"条所述，真机不能区分黑字来自 `values` 写入还是 Pro 默认，确定性证据只在离线层。
- **实测得出的 API 结论**：`arcpy.cim` 的 RGB 用 0–100 量纲 `values`（非 0–255，非 `red=/green=/blue=`）；分级容器 `CIMClassBreaksRenderer.breaks`、唯一值 `CIMUniqueValueRenderer.groups→classes`、简单符号 `CIMSimpleRenderer.symbol`；标注字号真正生效属性是 `CIMTextSymbol.height`（`fontSize` 读回缺失）；栅格色彩映射挂 `colorizer.colorRamp` 而非 `renderer`；`ApplySymbol` 类高层 API 未用，全程 `getDefinition("V3")/setDefinition` 手改 CIM。
- **一个记录在案的已知缺陷**（`title_symbol_formatting_is_a_recorded_defect`）：文本图名走 `CIMParagraphTextGraphic`/`CIMTextSymbol`/`CIMGraphicElement` + `setDefinition`，`.aprx` save/reload 后回读该 graphic 的 `symbol` 为 `None`（`text_survived` 仍在），且**首次导出也未采纳 symbol**——`title_band` 实测请求 20pt 但字形约 9.3pt、左对齐（我们设的是 Center），即字体族/字号/加粗/对齐在真机导出里没生效，图名以 Pro 默认样式渲染。**这是批次 3 起就存在的文本元素路径限制，与本批符号化改动无关，本批只改了颜色写法，无法顺带修好格式未落地的问题**；`title_symbol_formatting_is_a_recorded_defect` 以反向断言把当前"symbol 回读 None"钉住，将来若有人修好格式该测试会转红提醒同步。审阅 `review/` 图时请知悉图名观感反映的是默认渲染、非请求样式。

**本轮未验证**：出图成果的**业务级制图人眼验收尚未完成**——4 张代表性图（`workspace/smoke/<uuid>/review/` 的专题分级、土地利用唯一值、地形晕渲叠分层设色、带标注规范图）已导出待用户目测，美观、图例密度、字号与配色观感无法机器判定，冒烟的像素直方图只能证明"色板落色且分级分布正确"，**不能**证明"某个具体要素被刷成了它应得的颜色"（脚本不做逐要素/空间位置核验）：分级色彩靠每像素计数比例对分布负责，但唯一值/LAND_USE 的合成 fixture 各类要素等面积且各 ≥1 要素，置换类别仍会全绿——即色板↔类别的**逐要素落位正确性**是全局直方图的固有盲区，需人眼验收。色板取值来自通用 GIS 惯例，未经制图专业评审。标注避让只做了最小实现（单 labelClass、Arcade 表达式、字号字色），多标注要素的冲突消解、标注权重与 halo/背景框未做。栅格分级未做（新发现：`colorizer` 自带 `stretchStats.histogram`，将来做栅格分级无需自己读像元）。多层栅格叠放存疑：晕渲灰叠在分层设色之上时导出出现非任何色板的饱和色（约 52.9 万像素，`#ff2138`/`#00fc22` 等），单幅 ELEVATION 图（03b）正常，疑为 ArcGIS 半透明叠放/拉伸交互而非本项目代码缺陷，但未人眼验收。C# 面板授权卡片对 `styles`/`labels` 新参数只有离线契约覆盖，未在 Pro 进程内实测。

## 批次 9 面板工具调用挂起根因与修复（2026-11-05）

- 系统 Python 3.14.6：`./scripts/test.ps1` **226 项离线测试全部通过**（新增 `test_reliability.py` 3 项：启动停滞看门狗在 `stall_seconds` 后终止并随错误返回 stderr 与 `Stalled` 提示、worker 进度行逐条流式转发到 `on_progress`、worker 环境剔除 `ESRI_SOFTWARE_CLASS` 且 `PYTHONIOENCODING=utf-8`；`test_foundation.py` 1 项：运行时进度转成带 `step` 的 `tool_progress` 事件）。
- 现场取证（三次卡死运行的 events.jsonl）：07:53 一次为旧后端 180s Timeout；08:35 与 08:40 两次在新后端下 stderr 尾部均停在 `[worker] tool=... starting`，`import arcpy` 之后的任何标记都未出现，运行约 300s 后由用户取消；连只读 `environment` 也挂起，说明卡点在 ArcPy 冷启动与运行中 Pro 的许可握手，而非具体工具。Pro 关闭时 doctor 与探针均 2–4s 成功，佐证握手停滞与 Pro 忙状态（在线底图持续加载）相关。
- 真实 Pro Python 3.11 / ArcGIS Pro 3.5.2（Pro 关闭时）流式路径探针：面板同款调用链（cancel_event 置位 + `on_progress` 打印）逐行输出 `[worker] tool=environment starting`、环境指纹（`ESRI_SOFTWARE_CLASS=None PYTHONHOME=None CONDA_PREFIX=None`）、`[tools] importing arcpy...`、`[tools] arcpy ready in 2.4s`，最终 `ok: True | version: 3.5.2`。
- `scripts/build-addin.ps1` 编译通过（0 警告 0 错误），生成 `addin/ArcGISAgent/bin/Release/net8.0-windows/ArcGISAgent.esriAddinX`。
- 未验证项：Pro 运行中（尤其在线底图加载时）看门狗与实时进度在面板内的端到端表现待用户重装插件并重启 Pro 后实测；看门狗仅把无限等待变为 180s 明确的 `Stalled` 失败，Pro 持续繁忙时任务仍会失败。

## 批次 8 实时思考链与工具步骤时间线（2026-11-05）

- 系统 Python 3.14.6：`./scripts/test.ps1` **222 项离线测试全部通过**（新增 `test_foundation.py` 1 项：两步行计划的 `tool_requested` / `tool_executing` / `tool_result` 事件序列断言 `step == [1,1,1,2,2,2]`）。
- `scripts/build-addin.ps1` 编译通过（0 警告 0 错误），生成 `addin/ArcGISAgent/bin/Release/net8.0-windows/ArcGISAgent.esriAddinX`。
- 事件流协议未变：`step` 为新增可选字段，CLI 直接消费；面板通过既有 rpc 事件通道接收，无需改动 `interfaces.rpc`。
- 未验证项：Pro 内的最终视觉效果需用户重装插件后确认（本机无法在 Pro 进程内渲染 WPF 面板）；推理面板内容依赖模型 API 实际返回 reasoning 流，未产生 API 用量验证。

## 批次 7 project_info 超时根因确认与防御性诊断加固（2026-11-05）

- 系统 Python 3.14.6：`./scripts/test.ps1` **221 项离线测试全部通过**（新增 `test_reliability.py` 1 项：worker 执行前向 stderr 宣告 `[worker] tool=... starting`）。
- 现场取证：Pro 运行状态下用 Pro Python 只读探针跑同一失败工程 `work_1.aprx` 的 `project_info` 等价流程——工程打开 3.1s、6 个本地图层 `isBroken` 各 ≤0.01s、`World Topographic Map`（cdn.arcgis.com 瓦片服务）按 2026-11-04 修复跳过，全流程约 4s；当日实测该 CDN 可达但小 JSON 下载 3.3s，延迟波动大。
- 结论：截图中的 180s 超时来自修复前启动的常驻后端进程（源码不热更新），或故障时段该 CDN 完全不可达使"打开工程"阶段停滞；当前代码对同一工程无超时。加固项：worker 阶段进度（stderr 尾部随超时错误返回，下次故障可直接定位卡点）、`TIMEOUT_HINT` 文案增补离线底图与耗时操作建议、新增 `config/local.toml`（超时 600s）。
- 未验证项：故障时段的网络状态无法复现；worker 进度输出在真实超时场景下的端到端表现待下次故障验证；面板后端是否已重载新代码需用户重启面板确认。

## 批次 5 声明式技能系统（2026-11-05）

- 系统 Python 3.14.6：`./scripts/test.ps1` **216 项离线测试全部通过**（新增 `tests/test_skills.py` 26 项与 `test_foundation.py` 1 项 skills_dir 配置解析）。
- 内置 7 个技能包全量加载校验通过；每个步骤的工具名、参数键、必填项、枚举字面量与 `output_name` 命名模式均对照当前工具注册表核验。
- 拒绝路径离线实测：未知工具、宿主工具入技能、未知参数键、缺失必填参数、非法枚举、非法输出名、未声明参数占位符、前向步骤引用、畸形占位符（如 `{{import os}}`）、损坏 JSON、用户目录重名，全部 fail fast 并给出定位信息。
- CLI 实测：`agent.ps1 skills` 返回 7 个技能（含来源与步骤数）；`agent.ps1 tools` 返回 60 个工具，新增 `list_skills`/`read_skill` 均为只读非宿主。
- 运行时路径实测：`ArcPyRuntime(Path("nonexistent-python.exe"))` 成功读取 `hydrology_watershed`，证实技能读取在进程内完成、未启动 ArcPy worker；未知技能名返回 `{"ok": false, "error": {"type": "ValueError", ...}}` 结构化错误。`MockRuntime` 返回真实技能数据且不带 `simulated` 标记；`InProcessRuntime` 在无 ArcPy 环境下可读取技能。
- 技能系统不含代码执行：技能文件仅为 JSON 数据，加载器不做 eval/exec，占位符替换由模型在发起普通工具调用时完成，每次调用仍走既有逐次授权。
- 未验证项：真实聊天会话中模型按技能模板逐步执行的端到端质量（产生 API 用量，未在离线记录中执行）；Pro 面板后台会话仅使用内置技能，未接线 `skills_dir`。

## 批次 4 查询扩展、验收、字段计算与栅格水文（2026-11-05）

- 系统 Python 3.14.6：**189 项离线测试全部通过**（新增 `tests/test_advanced_tools.py` 21 项：复合过滤构造与 SQL 生成、IN/文本操作符拒绝路径、select_by_location 距离规则、表达式 AST 白名单与求值、导出与验收 schema、栅格水文枚举、授权预览路径、模型 schema 收录、新 hint）。`./scripts/test.ps1` 同批通过。
- Pro Python 3.11.11 / ArcGIS Pro 3.5.2 真实运行 `scripts/smoke_batch4.py`：**32 项检查全部通过**（`ok: true`），全部经逐次授权的 Agent 计划执行，合成数据仅写入 `workspace/smoke/<uuid>`。
- 签名与枚举先在本机 3.5.2 ArcPy 上探针实测再编码：`Near`（`method=PLANAR/GEODESIC`）、`SelectLayerByLocation`（13 个 overlap type，`SHARES_A_LINE_SEGMENT_WITH` 不在本机取值表内故未开放）、`CreateThiessenPolygons`（`fields_to_copy=ONLY_FID/ALL`）、`FeaturesToJSON`（`geoJSON="GEOJSON"`）、`Resample`、`ProjectRaster`（`resampling_type`、`geographic_transform`）、`sa.Reclassify`（`RemapRange`）、`sa.Hillshade`（`model_shadows`）、`sa.Fill`、`sa.FlowDirection`/`sa.FlowAccumulation`（`flow_direction_type=D8/MFD/DINF`）。
- 实测通过的工具级结果：`select_by_location` INTERSECT 2 要素、WITHIN_A_DISTANCE 950 米 3 要素；`near_analysis` `matched_count=3`；泰森多边形 3 块；复合 `all` 过滤 1 要素、`any`+IN 列表 3 要素、STARTS_WITH 1 行；`calculate_field` `!score!*2+1` 得 `[3,5,7]`、带 filter 更新 2 行；`define_projection` 副本 wkid=3857；CSV 表头+3 行、带筛选 CSV 1 行；GeoJSON 3 个 feature、Shapefile 含 .dbf；`verify_dataset` 全通过分支与 `min_count` 失败分支均返回结构化 `checks`/`failures`；`reclassify` 输出值域 ⊆ {0,1,2}；`hillshade` 输出全为非负；`resample` 像元 20 米且宽 50 列；`project_raster` 到 4326；`fill`→`flow_direction`→`flow_accumulation`（INTEGER，最大值 ≥99）水文链跑通。
- 安全不变量实测：源数据几何与属性前后一致；`verify_dataset` 返回 `source_modified: false`；无授权处理器/越界路径均被测试覆盖。
- 首跑暴露一个冒烟脚本自身错误（CONTAINS "A" 预期行数写错为 1，实际 2，工具行为正确），修正断言为 STARTS_WITH "B" 后 32 项全绿；工具实现未改动。

**本轮未验证**：以上为合成数据的工具级核验，不等于业务级 GIS 验收；`calculate_field` 未覆盖字段长度溢出与除零在真实数据上的表现（离线已覆盖除零 hint）；`export_features` 的 SHAPEFILE 分支未覆盖字段名超长截断（Shapefile 字段名限 10 字符，长字段名会被 ArcGIS 静默截断）；C# 面板授权卡片对新工具仅有离线契约覆盖；`resample`/`project_raster` 的 `MAJORITY` 重采样未实测。

## 批次 3 制图出图与只读导出（2026-11-04，含审计修复复验）

- 系统 Python 3.14 与 Pro Python 3.11.11：**168 项离线测试通过**（批次 3 的 `tests/test_cartography.py` 契约、辅助函数、桩对象流程与清理测试，`project_info` 布局清单 `_layout_record` 契约，以及坐标系单位与一致性拒绝测试）。
- Pro Python 真实运行 `scripts/smoke_cartography.py`：**29 项检查全部通过**（`ok: true`，`version: "3.5.2"`），全部经独立 ArcPy 子进程与逐次授权执行，合成数据仅写入 `workspace/smoke/cartography_<uuid>`。
- 实测通过的建图与导出：`build_layout` 生成 `.aprx` 并保存于任务目录；布局元素实际名称为 `["图例","指北针","比例尺"]`；未给 `extent` 时相机范围与数据并集一致；A4 横向与 A3 纵向（`page_mm [297.0, 420.0]`、`["图例","双比例尺"]`、`extent_source=requested`）；`export_layout` 导出 PNG / JPEG / PDF（默认 dpi）/ TIFF（指定布局名）/ SVG 且返回字节数非零；栅格图层同样能建图与导出；`elements: []` 建出的图面导出不含任何图廓元素名。
- 图面确有内容：对导出的 PNG 统计非白像素（亮度 <250）占比，地理坐标系图 `0.36`、墨卡托图 `0.48`、栅格图 `0.37`，阈值取 0.05。早期用亮度 <210 时，同一份代码两次运行得到 `0.36` 与 `0.0085`，原因是 Pro 给新图层随机选择默认填色、浅色填充的亮度高于 210，故判据改为 <250 后稳定。
- 坐标系实测：`Blank.aprx` 自带地图为 3857（CIM 中 `{"wkid": 102100, "latestWkid": 3857}`）；`Map.addDataFromPath()` 会让空地图采纳第一个图层的坐标系（加入 4326 图层后地图变为 4326），因此 `map_adopts_geographic_reference`、`projected_layer_build`（返回 `spatial_reference.wkid == 3857`、`extent.xmin == 12400000`）与加图层后的地图/图层坐标系一致性核验同时实测通过。`SpatialReference(4326).type` 为 `Geographic`，`metre_box_refused_for_geographic_layer` 与 `metre_box_accepted_for_projected_layer` 分别验证拒绝与放行。
- 只读性质实测：导出前后 `.aprx` 的 SHA256 一致（`export_is_read_only=true`），`project_info` 能列出该布局（1 个布局、`MILLIMETER`、`page_width 297`、`element_count>=4`）。
- 拒绝路径实测且不留残留：同名输出禁止覆盖、无几何的表被拒、坐标系混合被拒、地理坐标系配米制 `extent` 被拒、未知布局名被拒、工程文件不存在被拒、导出失败后无半截文件；被拒的 `build_layout` 不残留半成品 `.aprx`（`no_project_for_refused_build`、`no_project_for_refused_extent`）；`only_published_files_remain` 逐目录扫描确认只剩已发布成果、没有暂存文件。源数据未变。
- 本机 3.5.2 的 `Layout.export(mp.CreateExportFormat(fmt, path))` 实测返回实际落盘路径字符串且与传入路径一致，因此"导出必须写在声明路径"的核验可以真正生效；`arcpy.mp` 未公开 `Layout` 类（实际类型为 `arcpy._mp.Layout`）。
- 曾出现一次三项同红的运行（`metre_box_accepted_for_projected_layer`、`unknown_layout_refused`、`missing_project_refused`），当时代码与通过版本完全一致，且整机正在并行跑离线套件与像素统计；为此把冒烟脚本改为记录所有被拒调用的错误文本（`refusals`），独占复跑 29 项全绿。
- API 取值范围以本机实测为准：`createLayout(宽, 高, "MILLIMETER", name=)`、`createMapFrame(polygon, map, name=)`、`createMapSurroundElement(point|polygon, "NORTH_ARROW"|"SCALE_BAR"|"LEGEND"|"DUAL_SCALE_BAR", mapframe, style_item, name)`（样式类名须用下划线形式，本机 `ArcGIS 2D` 库有 39/34/6 个候选）、`MapFrame.camera` + `camera.setExtent(arcpy.Extent(...))`、`Layout.export(mp.CreateExportFormat(fmt, path))` 返回输出路径、`resolution` 实际生效（同图 72/300/600 dpi 分别 5,750 / 43,729 / 171,311 字节）。文本元素只能走 `getDefinition("V3")` + `CIMParagraphTextGraphic`/`CIMTextSymbol`/`CIMGraphicElement` + `setDefinition`，保存重载后为 TextElement。`listElements` 的类型 token 是 `MAPFRAME_ELEMENT`（`MAP_FRAME_ELEMENT` 被拒）；`Camera` 只有 `setExtent`，无 `extent` 与 `getCenter`；`Geometry` 只有 `projectAs` 且构造时必须带坐标系，否则投影是空操作；`arcpy.cim` 不导出空间参考类，但 CIM 对象在 arcpy 中就是 dict，可直接赋值。
- 运行时明确拒绝、因此未提供的能力：`ArcGISProject(None)`（缺参数）、`Map.getCamera`（不存在）、`georeferenceMapFrame` 与 `worldFile`（任意取值报 ValueError）、按格式设置色彩模式（PNG 无该属性；`CMYK`/`RGB` 被拒）。

**本轮未验证**：出图成果的**业务级人眼验收**（美观、注记密度、比例合适与否）未做，冒烟以像素占比、文件字节数与元素清单为代理证据；图层符号沿用 Pro 默认随机填色，符号化与标注能力尚未实现；未在用户真实工程与真实数据上跑完整出图；空白模板路径依赖本机安装自带的 `Blank.aprx`，其他 Pro 版本或精简安装需重新核验（缺失时工具报中文错误且不执行）；工具箱进程内仍无强制超时；C# 面板的授权卡片对新工具的展示只有离线覆盖。

## 批次 2 空间分析与可靠性修复（2026-11-04）

- 系统 Python 3.14：**127 项离线测试通过**（批次 2 新增 19 项契约与扩展签出测试，本轮可靠性新增 8 项，模型流总时长限制新增 1 项）。
- Pro Python 3.11.11 / ArcGIS Pro 3.5.2 / ArcInfo 真实运行 `scripts/smoke_arcpy.py`：**24 项检查全部通过**，全部经独立 ArcPy 子进程与逐次授权执行，合成数据仅写入 `workspace/smoke/af6a62c7f19a4a1587c873f78c71f3a7`。
- 批次 2 实测通过的工具与分析结果：`interpolate_surface`（IDW 与 Kriging，9×9 F32，WKID 4326）、`polygon_to_raster`（60×50 U2）、`kernel_density`、`zonal_statistics`（两个分区各一行，MEAN 非空）、`extract_by_mask`（范围裁剪到掩膜内）、`sample_raster_at_points`（9 行 × 两个栅格值列）、`terrain_derivatives`（坡度与坡向）、`contour_lines`（3 条折线）、`summarize_statistics`（按 `zone_name` 分组 2 行，含 `SUM_zone_code`）、`join_table`（`label_text` 连接为 north/south）、`union`（3 块面）、`erase`（2 块）、`reshape_geometry`（中心点 2、简化 2、最小外包 1）。
- 拒绝路径同样实测：文本分区字段被拒绝且不创建成果；连接不存在的列报 `Field unavailable`；点要素参与叠合在调用 ArcGIS 前被拒；源数据几何与属性未变。
- 空间分析扩展签出守卫按许可证实测（本机 ArcInfo + Spatial Analyst 可用）；不可用分支只有离线覆盖。
- 可靠性修复的验证方式：新增 `tests/test_reliability.py` 覆盖“工作进程写出结果后被终止仍返回结果并标记 `worker_terminated`”“超时保留 worker stderr 并优先给出具体故障提示”“取消返回 `Cancelled` 而不是 WorkerFailure”“协议目录被占用不会吞掉成功结果”“worker 结果无法序列化时降级为显式失败”“会话文件历史形状异常时拒绝恢复”。
- 一次真实失败复盘驱动了其中三项：`project_info` 的 180 秒超时诊断曾因父进程丢弃被杀进程的 stderr 而难以定位，现在超时与取消都会返回 worker 的 stderr 尾部。

**本轮未验证**：以上均为人工合成数据的工具级核验，**不等于用户业务成果验收**；未在真实工程数据上重跑面板与工具箱路径，未修改 Pro 插件 C# 代码（面板宿主超时、工具箱进程内无强制超时、面板 4 秒终止仍属已知限制），制图与地图导出布局（批次 3）尚未实现。

## 工具接入与逐次授权（2026-11-03）

- 系统 Python 3.14 和 Pro Python 3.11：**79 项离线测试均通过**。新增覆盖每步独立授权、读取也需确认、全局写入不跳过确认、缺少处理器不执行、单次许可不扩散、参数冻结、拒绝后停止、取消/断线/超时、过期/重复答复和宿主请求顺序。
- 新版 Pro 插件与 WPF 预览构建成功，均为 **0 警告、0 错误**。使用 `RegisterAddIn.exe /s` 注册成功，构建包与用户插件目录的安装包 SHA256 一致：`02844F96C42AA32040C90539C308A6F1331F4E31B732CDD81A97C8913355F0A9`。需要重启 Pro 加载；没有关闭或保存用户工程。
- 340 与 500 像素预览分别检查窄幅浅色、宽幅深色确认卡片，均自动退出。`approval_visible=true`、`composer_visible=false`、`deny_has_focus=true`。截图为 UI 样例，未执行 GIS：`workspace/tool-approval-340.png`、`workspace/tool-approval-500-dark.png`。
- Pro 3.5.2 真实 ArcPy 测试 **20 个工具通过**：environment、list_datasets、describe、count、read_rows、copy_features、clip、intersect、merge、dissolve、project、spatial_join、select_features、add_field、set_field_value、delete_fields、buffer、calculate_geometry、repair_geometry、clip_raster。另核验原始几何与属性未变、已有输出拒绝覆盖。fixture 内裁剪与筛选为 2 条、合并 6 条、融合 2 条、空间连接 Join_Count 为 [0,1,1]、投影 WKID 4326、属性副本值 [3,10,10]、栅格尺寸缩小、测地面积符合人工输入预期。
- 最后增加的 describe 字段 length/nullable/required/editable 与 extent 已单独用相同人工数据只读核验，返回 3 条记录、4 个字段，结果在 `describe-validation.json`。
- 使用已配置的 DeepSeek / deepseek-flash，真实运行 **describe → select_features(group_name=A) → buffer(50 Meters) → count**。四次独立授权记录，使用上一工具的真实成果路径，状态 completed、simulated=false。ArcPy 独立核验最终 2 个缓冲区，测地面积各 **7833.329596568631 m²**，原始数据仍为 3 条；API 输入 30867、输出 845 token，其中缓存输入 24192。

人工数据及记录均在：

```text
workspace/smoke/5f7ebf0115d1490693bc341c6a85627e/
  tool-validation.json
  describe-validation.json
  live-agent-validation.json
  runs/20261103T165147Z_e8d1fbfaac7d/
```

ArcPy suite 和真实模型脚本使用仅限该人工数据根目录的测试授权回调；没有读取或修改用户 GIS 数据。上述验证属于人工输入的集成核验，**不等于用户业务成果验收**。

**本轮未逐一实测**：真实 Pro 当前地图的 map_context / add_to_map / set_layer_visibility 交互，仅完成 SDK 编译和 RPC 授权/宿主响应模拟；project_info 未在本轮重新实测。其他模型供应商继续以模拟协议测试为主。CLI/面板中的实际用户授权操作仍应在具体任务中确认。

## 多供应商增量验证（2026-11-03）

- 系统 Python 3.14 与 Pro Python 3.11：**63 项离线测试均通过**；覆盖七家预设和自定义接口的配置、独立密钥、URL/重定向规则、流式文本和工具往返、Claude 原生内容块/思考签名/工具结果/用量、取消与截断、跨连接及型号的会话转换，以及 CLI/RPC 契约。
- 新版 Pro 插件与独立 WPF 预览均构建成功，**0 错误、0 警告**。
- 独立预览自动加载实际 RPC，检查 340 和 500 像素窗口的连接管理与滚动设置，均正常退出；截图在 `workspace/provider-settings-340.png`、`workspace/provider-settings-500.png`。未使用无超时 UI Automation 等待。
- 使用本机既有 DeepSeek 凭据实测新适配器：图片回答“蓝色”，推理任务回答 42 并返回 26 个推理 token；只读 environment 工具返回 ArcGIS Pro **3.5.2 / Build 57366 / ArcInfo / Python 3.11.11**。记录见 `workspace/chat-validation.json`。
- 新 `.esriAddinX` 已通过 `RegisterAddIn.exe /s` 注册，退出码 0，安装包与构建包 SHA256 一致。已打开的 Pro 需要重启加载新 DLL；本轮未关闭用户工程。

其他供应商使用模拟 HTTP/SSE 响应验证，**尚未用 Qwen、Kimi、GLM、GPT、Claude、Grok 真实账户或第三方中转站逐一验证**。本轮未新增或重新核验 GIS 分析成果；只读环境工具成功不代表 GIS 业务核验通过。

以下为 2026-11-02 的初期验证记录。

## 已通过

- 系统 Python 3.14.6：39 项离线测试通过，涵盖流式工具调用、推理片段、取消、图片和文本附件、会话恢复、真实用量聚合、密钥读取，以及原有 GIS 调度测试。
- `doctor`：ArcGIS Pro **3.5.2**，build **57366**；ArcPy Python **3.11.11**，产品级别 **ArcInfo**。
- 系统 Python 调度 Pro Python 的跨进程环境诊断成功，包含中文目录的仓库路径可用。
- 真实缓冲区集成检查：创建两个 WGS84 人工点，经 ArcPy 子进程读取元数据并生成 500 米测地线缓冲区，独立读取结果验证。
- 输出要素数 **2**；两个缓冲区测地面积分别为 **785191.1658 m²**、**785191.1559 m²**，符合 500 米圆形缓冲区的面积预期。
- 源码、测试与 Python 脚本编译检查通过。
- ArcPy `ImportToolbox` 成功加载 `arcgis/ArcGISAgent.pyt`，发现 `ExecutePlan_arcgis_agent`、`InspectCurrentProject_arcgis_agent` 两个工具；通过真实工具箱入口执行环境检查计划，任务完成。
- .NET 8 编译 Pro 插件成功（0 错误、0 警告），`RegisterAddIn.exe` 注册到当前用户插件目录。ArcGIS Pro 3.5.2 空白工程中，功能区的 GIS Agent 页面和打开按钮可用；停靠面板成功显示输入框、发送、新对话和允许创建成果控件。未配置密钥时，发送消息会显示本地配置提示。
- 使用本机已配置的 DeepSeek 凭据在线验证 `deepseek-flash`：识别人工绘制图片中的蓝色圆形；思考模式返回推理内容与推理 token；模型调用 ArcPy 环境工具后正确报告 Pro 3.5.2。真实响应包含输入、输出和缓存 token 数据。
- 在复用相同 WPF 视图的独立预览中，真实对话和多轮追问返回正确结果，模型名、用量和耗时正常显示。新版插件已重新注册；重启 Pro 后加载更新。

缓冲区验证结果：

```text
workspace/smoke/781fba458c274f339614fe5ae0b08e0d/
  runs/20261102T123346Z_17a0968cf796/
    outputs/results.gdb/buffer_500m
    summary.json
    events.jsonl
```

## 执行环境限制

在受限沙箱中首次导入 ArcPy 时发生原生进程退出 `3221225477`，未能返回 Python 异常。同一诊断命令在沙箱外正常完成，缓冲区验证也在正常本机环境通过。日志保留了失败记录，执行器按失败处理，没有将其解释为无数据或成功。

## 尚未验证

- Pro GUI 内 `CURRENT` 对当前打开工程的读取，需要用户实际打开工程后运行工具箱检查。
- 云端 GitHub Actions 尚未运行；只配置了 Windows Python 3.11 / 3.14 的离线测试矩阵。
- 将生成的成果添加到实际 Pro 地图的交互尚需在用户打开的地图中核验。
