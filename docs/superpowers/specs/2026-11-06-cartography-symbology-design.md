# 出图能力增强：符号化与标注 · 设计规格

- 日期：2026-11-06
- 状态：设计已确认，未实现。§8.1 的 CIM 真机探测门槛**已于同日执行完毕**，四类 renderer 与标注全部验证可交付，规格已按实测结论修订（见 §8.1 的偏差表）。
- 范围：给 `build_layout` 增加 `styles` 与 `labels` 两个参数，用预设色板驱动 CIM renderer 与 labelClass，使 agent 产出的图不再是 Pro 默认随机填色。**不新增 LLM 可见工具**，不改动 `export_layout` 的只读语义，不改动"绝不修改用户工程"不变量。

## 1. 背景与根因

`src/arcgis_agent/cartography.py` 的 `_fill_layout` 在 `addDataFromPath` 拿到图层后不再触碰符号（第 158-162 行），图层沿用 Pro 默认随机填色。`docs/validation.md:60` 与 `CHANGELOG.md:280` 都已把这点记为已知限制："图层符号沿用 Pro 默认随机填色，符号化与标注能力尚未实现"。

根因是能力缺失，不是缺陷：`arcpy.mp.Layer` 不暴露符号化 API，唯一通路是 CIM（`layer.getDefinition("V3")` → 改 renderer → `setDefinition`）。这条通路在本仓库已被 `cartography.py:132-149` 的 `_add_title` 走通一半（CIM 文本元素），`docs/validation.md:57` 记录了关键实测结论："CIM 对象在 arcpy 中就是 dict，可直接赋值"。但 renderer 与 labelClass 的 CIM 结构远比文本元素复杂，且同一份记录里也有 `georeferenceMapFrame`、`worldFile`、按格式颜色模式被 3.5.2 运行时直接拒绝的先例。**因此 renderer 结构能否走通必须实测，不能凭文档推断。**

出图是 ArcGIS Pro 最核心的功能之一，而"默认随机填色"意味着 agent 产出的图在业务上基本不可用。这是当前出图能力最大的短板，优先级高于版面参数化、多图幅、比例尺数值控制等其他缺口。

## 2. 目标与非目标

### 目标

1. agent 一次 `build_layout` 调用就能出符号正确的四类图：专题分级色彩图、唯一值分类图、地形分层设色/晕渲图、中性基础示意图。
2. 支持字段标注，使规范成果图能带地名注记。
3. 配色由**预设色板**决定，不依赖模型自行组色。理由：模型看不见自己导出的图，没有视觉反馈闭环，无法靠迭代收敛审美，因此把设计判断固化进代码而非交给模型。
4. 提供机器化的配色正确性验证（颜色直方图断言），部分弥补"agent 看不见图"的缺口。

### 非目标（明确不做）

- **不新增 LLM 可见工具**：工具总数不变（当前 62）。符号化与标注只做 `build_layout` 参数，因此不提供"对已有工程里的图层单独改符号"的能力 —— 换配色需重建工程（`.aprx` 仅 9KB 模板加图层引用，重建成本很低）。这是用户明确选择的结果，换来的是完全不新增写入路径，`export_layout` 只读与"绝不修改用户工程"两条不变量一字不改。
- **不做自然断点（Jenks）与手工断点数组**：只做等间距与分位数。AGENTS.md 禁止往 Esri base Python 环境装包，Jenks 需纯 Python 实现 O(n²) 算法，本期不做。
- **不做栅格分级 renderer**：需读直方图算断点，禁用 numpy 后代价过高。坡度分级这类需求走既有路径：先用已存在的 `reclassify` 工具把栅格离散化，再上色。
- **不做标注避让的精细控制**：只做最小标注（字段 + 字号）。标注类（多 labelClass）、比例尺可见范围、Maplex 避让规则本期不做。
- **不做自定义色板**：色板硬编码为常量表。将来若需用户扩展，再抽成 JSON 加载器（参照 `skills.py` 机制，迁移成本低）。
- **不做版面参数化与多图幅**：元素位置尺寸、副图名、图注、数据来源、制图日期、比例尺数值、插图与索引图，均属独立后续子项目。

## 3. 架构

### 3.1 模块边界

新增 `src/arcgis_agent/symbology.py`。该模块**不碰工程文件、不做 IO、不决定输出路径**，只接受一个 CIM definition 并返回改好的 definition；工程文件的打开与保存仍完全归 `cartography.py`。

五个函数，按是否接触 arcpy 严格分层 —— 这是断点算法与兼容矩阵能在无 arcpy 环境下单测的前提：

| 函数 | 碰 arcpy | 职责 |
| --- | --- | --- |
| `validate_style_spec(style, desc)` | 否 | 纯函数。从 `desc` 取 `dataType`/`shapeType`/`fields`，判定色板 kind × 数据类型/几何类型/字段类型的兼容性 |
| `collect_values(arcpy, dataset, field, cap)` | 是 | 读字段值；超上限则系统抽样，回报 `sampled` 与实际条数 |
| `build_classes(values, classes, method)` | 否 | 纯函数。等间距/分位数断点，或唯一值分组 |
| `apply_renderer(arcpy, definition, spec, classes)` | 是 | 以 `definition` 里运行时生成的默认符号为原型 `deepcopy` 并改色，构造 CIM renderer 挂回 layer definition（矢量）或改 `colorizer.colorRamp`（栅格） |
| `apply_label(arcpy, definition, label_spec)` | 是 | 改**已存在的** `labelClasses[0]`（Arcade 表达式 + 字号 + 字色），并置 `labelVisibility = True` |

### 3.2 `cartography.py` 接入点

只在 `_fill_layout` 的图层循环内插入三步：`getDefinition("V3")` → 交 `symbology` 变换 → `setDefinition`。`_export_layout` 一字不改，保持只读。

### 3.3 前置校验不变量

`_build_layout` 现有意图（`cartography.py:207` 注释）是把所有可能被拒绝的检查排在 `shutil.copy2(template, staged)` 之前，使被拒调用不残留半成品 `.aprx`、连 `cartography/` 目录都不创建。**样式与标注的全部校验必须并入这个前置阶段**，不能等图层加进地图之后才发现字段不存在。`collect_values` 需读源数据，但为只读操作，放前置阶段无副作用。

## 4. 色板表

9 个色板，每个带 `kind` 属性。SEQUENTIAL / TERRAIN 按 3、5、7 三档**分别存色值**，不做等距抽样 —— ColorBrewer 的分档色是专门调过的，naive 抽样会出现相邻两级几乎同色。QUALITATIVE 为固定色序，档位数由数据的唯一值个数决定。

### SEQUENTIAL（分级色彩 / 栅格拉伸）

| 色板 | 3 档 | 5 档 | 7 档 | 典型用途 |
| --- | --- | --- | --- | --- |
| `BLUES` | `#deebf7 #9ecae1 #3182bd` | `#eff3ff #bdd7e7 #6baed6 #3182bd #08519c` | `#eff3ff #c6dbef #9ecae1 #6baed6 #4292c6 #2171b5 #08306b` | 人口密度、降水量、距离衰减、可达性 |
| `GREENS` | `#e5f5e0 #a1d99b #31a354` | `#edf8e9 #bae4b3 #74c476 #31a354 #006d2c` | `#edf8e9 #c7e9c0 #a1d99b #74c476 #41ab5d #238b45 #00441b` | 植被覆盖、NDVI、绿化率、适宜性 |
| `REDS` | `#fee0d2 #fc9272 #de2d26` | `#fee5d9 #fcae91 #fb6a4a #de2d26 #a50f15` | `#fee5d9 #fcbba1 #fc9272 #fb6a4a #ef3b2c #cb181d #67000d` | 高温、风险、事故密度、污染 |
| `RD_BU` | `#67a9cf #f7f7f7 #ef8a62` | `#0571b0 #92c5de #f7f7f7 #f4a582 #ca0020` | `#2166ac #67a9cf #d1e5f0 #f7f7f7 #fddbc7 #ef8a62 #b2182b` | 距均值偏差、增减变化、盈亏（中间为中性白，低=蓝 高=红） |

### QUALITATIVE（唯一值）

| 色板 | 色序 | 说明 |
| --- | --- | --- |
| `CATEGORY10` | `#1f78b4 #e31a1c #33a02c #ff7f00 #6a3d9a #a6cee3 #fb9a99 #b2df8a #fdbf6f #cab2d6` | ColorBrewer Paired 重排为饱和色优先，保证少类时也高度可辨；不含语义 |
| `LAND_USE` | 水体 `#3B7DBF`、林地 `#2E7D32`、草地 `#9CC47A`、耕地 `#E3C777`、建成区 `#B85C4E`、裸地 `#A6927B`、湿地 `#6E9BA8`、园地 `#7FA85C`；未匹配兜底 `#9E9E9E` | 语义色，需关键词匹配表（见下） |

`LAND_USE` 的关键词匹配表（对字段值做不区分大小写的子串匹配，按表内顺序取首个命中）：

| 语义 | 关键词 |
| --- | --- |
| 水体 | 水体、水域、水、河、湖、库、water、river、lake、reservoir |
| 林地 | 林、乔、forest、wood |
| 草地 | 草、grass、meadow |
| 耕地 | 耕、农田、旱地、水田、crop、farmland |
| 建成区 | 建成、城镇、城市、居民点、工矿、urban、built |
| 裸地 | 裸、bare |
| 湿地 | 湿、wetland、沼泽 |
| 园地 | 园、果园、orchard、garden |

匹配不上的值统一给兜底灰 `#9E9E9E`，并在返回结果里列出全部未匹配值，使模型能如实回报用户而非静默错配。

### TERRAIN（栅格拉伸）

栅格色带恒为 7 个色标拼成的 6 段线性色带，所以 TERRAIN 只存 7 档，不需要 3/5 档：

| 色板 | 7 档（低 → 高） | 说明 |
| --- | --- | --- |
| `ELEVATION` | `#1a6b3c #5a9c4f #a3b18a #e3d49a #d19a63 #a4674a #d9d6cf` | 分层设色：低地绿 → 黄 → 橙褐 → 高山灰白 |
| `HILLSHADE_GRAY` | `#ffffff #e0e0e0 #c2c2c2 #a6a6a6 #8a8a8a #6e6e6e #4d4d4d` | 晕渲灰，通常叠在 `ELEVATION` 之上做透明混合 |

SEQUENTIAL 色板用于栅格拉伸时同样取其 7 档。

### BASE（简单符号，不分色阶）

按几何类型取固定符号，`styles` 只需给 `layer` 与 `palette`：

- 面：填充 `#DCE6F1`，轮廓 `#5B7A99` 0.7pt
- 线：`#4D4D4D` 1.2pt
- 点：`#3B6E8F` 6pt 圆，0.5pt 白描边

### kind × 数据类型 → renderer 推断矩阵

renderer 类型不作为参数暴露，由下表推断（少一个参数就少一种模型填错的方式）：

| kind | 色板 | 矢量 | 栅格 |
| --- | --- | --- | --- |
| `SEQUENTIAL` | BLUES / GREENS / REDS / RD_BU | 分级色彩（需 `field`） | 拉伸到色带（min–max） |
| `QUALITATIVE` | CATEGORY10 / LAND_USE | 唯一值（需 `field`） | 拒绝 |
| `TERRAIN` | ELEVATION / HILLSHADE_GRAY | 拒绝 | 拉伸到色带 |
| `BASE` | BASE | 简单符号（按几何取面/线/点） | 拒绝 |

栅格符号化走 `definition.colorizer`（默认已是 `CIMRasterStretchColorizer`），**不是** `renderer` —— `CIMRasterLayer.renderer` 恒为 `None`，且 `arcpy.cim` 中不存在任何 `CIMRaster*Renderer` 类。色带用该色板的 7 档色构造 `CIMMultipartColorRamp`（内含 6 段 `CIMLinearContinuousColorRamp`），拉伸区间沿用 colorizer 自带的统计（`stretchStats`），不需自己读像元。

### 固定不开放的样式细节

- 分级色彩轮廓：0.2pt 白（色块干净分离）
- 唯一值轮廓：0.4pt `#666666`（强调边界）
- `classes` 范围 3–7，默认 5（7 级以上制图不可读）
- 唯一值基数上限 10，超出直接拒绝并回报实际基数
- `method` 仅 `EQUAL_INTERVAL`（默认）与 `QUANTILE`

## 5. 参数 schema 与组件改动清单

### 5.1 `build_layout` 新增参数

```
styles: [{                       # optional，maxItems 12（与 LAYERS 上限一致）
  layer:   <绝对路径，必须出现在 layers 里>
  palette: BLUES|GREENS|REDS|RD_BU|CATEGORY10|LAND_USE|ELEVATION|HILLSHADE_GRAY|BASE
  field:   <字段名>              # 分级色彩与唯一值必填
  classes: 3–7，默认 5            # 仅分级色彩
  method:  EQUAL_INTERVAL|QUANTILE，默认 EQUAL_INTERVAL
}]
labels: [{                       # optional，maxItems 12
  layer: <同上>
  field: <标注字段>
  size:  6–24 pt，默认 9
}]
```

### 5.2 组件改动清单

| 文件 | 改动 |
| --- | --- |
| `src/arcgis_agent/symbology.py` | 新建。色板常量表（含 kind、3/5/7 档色值、LAND_USE 关键词表）、`validate_style_spec`、`collect_values`、`build_classes`、`apply_renderer`、`apply_label` |
| `src/arcgis_agent/catalog.py` | 新增 `PALETTE_NAMES`、`CLASSIFY_METHODS`、`MIN_CLASSES`/`MAX_CLASSES`、`MAX_UNIQUE_VALUES`、`LABEL_SIZES` 常量；`build_layout` 的 `properties` 加 `styles` 与 `labels`（均入 `optional`）；`validate_call` 的 `build_layout` 分支加 §7 九条跨参数校验。`approval_details` 不改（输出路径仍是同一个 `.aprx`） |
| `src/arcgis_agent/cartography.py` | `_fill_layout` 图层循环内插入 `getDefinition` → symbology → `setDefinition`；`_build_layout` 前置阶段并入样式校验与断点计算；返回结果增加 `symbology` 段。**另修一处既有缺陷**：`_add_title`（第 142 行）的 `CIMRGBColor(red=0, green=0, blue=0)` 改为 `values` 写法 —— 现在的黑色是 `values` 未设置时的默认值碰巧撞对的，改颜色不会生效 |
| `src/arcgis_agent/prompts/system.md` | `## 出图` 章节补符号化与标注能力、色板选择纪律、"换配色需重建工程" |
| `src/arcgis_agent/skills/interpolation_surface_map.json` | 出图步骤补 `styles`（栅格用 `ELEVATION`，等高线用 `BASE`） |
| `src/arcgis_agent/skills/csv_points_to_density_map.json` | 出图步骤补 `styles`（密度栅格用 `REDS`，采样点用 `BASE`） |
| `scripts/smoke_symbology.py` | 新建真机冒烟，见 §8.3 |
| `tests/test_symbology.py` | 新建离线测试，见 §8.2 |
| `docs/tools.md` | "制图与出图约定"节补 `styles`/`labels`、色板表、兼容矩阵、已知限制 |
| `docs/architecture.md` | symbology 模块边界说明 |
| `docs/validation.md` | 新增批次：CIM 探测结论 + 冒烟结果 |
| `CHANGELOG.md`、`README.md`、`README.en.md` | 行为变更、影响模块、验证方式、已知限制 |

`deepseek.tool_schemas`、审批卡片机制、`skills.py` 的加载校验均无需改动 —— 它们都从 `TOOLS` 自动派生。

## 6. 数据流

一次带符号化的 `build_layout`：

1. `validate_call` 做 schema 校验 + §7 的跨参数校验。
2. `_build_layout` 前置阶段：`_map_layer` 取 `desc` → `validate_style_spec` → `collect_values` → `build_classes`。任一失败即抛 `ValueError`，**此时模板尚未复制**。
3. `shutil.copy2(Blank.aprx, staged)`。
4. `_fill_layout`：`addDataFromPath` → `getDefinition("V3")` → `apply_renderer` / `apply_label` → `setDefinition`。
5. 建布局、图幅、相机、图廓元素、图名（现状不变）。
6. `project.save()` → `os.replace` 原子发布；失败则删暂存文件（现状不变）。
7. 返回结果新增 `symbology` 段：每图层实际用的 renderer 类型、色板名、断点数组或唯一值→色映射、`classes`、`method`、`sampled` 与实际取值条数、`LAND_USE` 的未匹配值列表。

**返回里必须带断点与实际色值**：模型看不见图，只有拿到这些才能把"实际怎么分的级"如实回报给用户，而不是含糊地说"已出图"。

## 7. 校验与错误处理

九条前置校验，全部在复制模板之前执行，错误消息为中文、指明具体参数、给出可用替代值（沿用 `_require_degree_box` / `_sr_advice` 的写法）：

1. `styles` / `labels` 的 `layer` 必须出现在 `layers` 里 → 报错时列出 `layers` 实际内容。
2. 同一 `layer` 不能有两条 `styles`（JSON Schema 的 `uniqueItems` 管不到对象内部，需手写）。
3. 色板 kind 与数据类型不兼容 → 例："ELEVATION 只适用于栅格，而 `<路径>` 是 Polygon 要素类"。
4. 分级色彩：`field` 必填且字段类型必须是 Integer / SmallInteger / Float / Double → 否则列出该数据集的全部数值字段。
5. 唯一值：`field` 必填且基数 ≤ 10 → 超出时报实际基数并建议改用分级或先归并类别。
6. `field` 不存在 → 列出全部字段名。
7. 字段全 NULL、无有效值、或取值全部相同 → 拒绝。这是**静默失败防护**的重点：断点算不出来时若继续，会渲出一张看起来正常但语义错误的空图且不报任何错，正是仓库最忌讳的失败模式。
8. 凡不走分级色彩路径的组合（`BASE`、`TERRAIN`，以及 `SEQUENTIAL` 作用于栅格的拉伸）传了 `field`、`classes` 或 `method` → 拒绝，不静默忽略（避免模型以为参数生效了）。
9. `labels.field` 必须存在且为文本或数值类型。

`collect_values` 读字段值上限 20 万条，超出则系统抽样，并在返回里标 `sampled: true` 与实际用到的条数，让模型与用户都能判断断点是否为近似值。

## 8. 测试与验证

### 8.1 门槛：CIM 真机探测（已于 2026-11-06 执行完毕）

用 Pro Python（`E:\programs\ArcGIS\bin\Python\envs\arcgispro-py3\python.exe`）跑了六轮一次性探测脚本（`workspace/probe-cim/`，本地忽略目录），逐项验证 §4 矩阵所需的 CIM 结构。**结论：四类 renderer 与标注全部可交付，但四处与原设计的假设不同，规格已据此修订。**

#### 实测确认的构造形态

| 目标 | 真实形态 | 与原假设的偏差 |
| --- | --- | --- |
| 颜色 | `CIMRGBColor().values = [r, g, b, alpha]`，alpha 取 0–100 | **`red=`/`green=`/`blue=` 关键字被静默忽略**，渲成灰色 |
| 符号 | `copy.deepcopy(默认 renderer.symbol)` 后只改其中 `CIMSolidFill`/`CIMSolidStroke` 的颜色与宽度 | **从零构造 `CIMPolygonSymbol` 会被接受并落盘，但渲染时完全不画** |
| 分级色彩 | `CIMClassBreaksRenderer`：`.field`、`.classBreakType="GraduatedColor"`、`.minimumBreak`、**`.breaks`**（`CIMClassBreak.upperBound/.label/.symbol`） | 容器属性是 `breaks`，**不是 `classBreaks`** |
| 唯一值 | `CIMUniqueValueRenderer`：`.fields=[f]`、`.useDefaultSymbol`、`.defaultSymbol`、**`.groups`** → `CIMUniqueValueGroup.classes` → `CIMUniqueValueClass.values` → `CIMUniqueValue.fieldValues` | 容器是 `groups`，**不是 `classes`**，中间多一层 group |
| 简单符号 | 直接改默认 `renderer.symbol` 内的填充色/轮廓色/轮廓宽；点大小改 `CIMVectorMarker.size` | 一致 |
| 栅格 | **`definition.colorizer`**（默认即 `CIMRasterStretchColorizer`），改其 `.colorRamp`；多段色带用 `CIMMultipartColorRamp.colorRamps = [CIMLinearContinuousColorRamp(fromColor,toColor)…]` | **`CIMRasterLayer.renderer` 恒为 `None`；`arcpy.cim` 里没有任何 `CIMRaster*Renderer` 类**，栅格符号化全部叫 `*Colorizer` |
| 标注 | 改**已存在的** `definition.labelClasses[0]`：`.expression="$feature.<字段>"`、`.expressionEngine="Arcade"`、`.visibility=True`；字号改 `labelClass.textSymbol.symbol.fontSize/.height`；字色改 `textSymbol.symbol.symbol`（内层 `CIMPolygonSymbol`）里的 `CIMSolidFill`；最后 `definition.labelVisibility = True` | **整个替换 `labelClasses` 列表为新建的 `CIMLabelClass` 会落盘成功但不渲染**；默认引擎是 Arcade 而非 VBScript |

#### 实测的验证强度

颜色直方图断言不仅能证明色板落地，还能证明**分级归属正确**：9 个面按 `VALUE` 分成 2/2/2/2/1，导出图各档像素数恰为 `72200 × 4 + 36100`（单面 36100 px）。唯一值图同样吻合。分级与标注叠加在同一图层时两者同时生效（标注会遮挡少量填充，命中数从 72200 降到 71183，属预期）。

#### 探测发现的既有缺陷（本期一并修）

`cartography.py:142` 的 `CIMRGBColor(red=0, green=0, blue=0)` **是碰巧正确的**：`red/green/blue` 关键字被忽略，而 `values` 未设置时默认就是黑色，所以图名一直是黑的。一旦有人想把图名改成别的颜色就不会生效。本期改为 `values` 写法。

#### 新发现但本期不做

默认栅格 colorizer 自带 `stretchStats.histogram`（完整直方图数组），意味着**栅格分级其实无需自己读像元就能算断点**，`CIMRasterClassifyColorizer` 也确实存在。这推翻了 §2 非目标里"栅格分级代价过高"的理由，但为守住本期范围仍不做，留作后续子项目。

探测脚本是一次性的，不进 `scripts/` 长期保留；结论需写进 `docs/validation.md` 新批次。

### 8.2 离线测试 `tests/test_symbology.py`

不 import arcpy，沿用仓库桩对象惯例（`arcpy` 作为参数传入）：

- 色板表完整性：`SEQUENTIAL` 的 3/5/7 三档齐全且各档色值数量与档名一致；`TERRAIN` 仅有 7 档（栅格色带恒为 6 段线性色带拼成的多段色带，不需要 3/5 档）；`QUALITATIVE` 为固定色序（`CATEGORY10` 恰好 10 色，`LAND_USE` 恰好 8 个语义色加 1 个兜底灰）；全部色值为合法 `#RRGGBB`；`kind` 合法；色板名唯一；`PALETTE_NAMES` 与 `PALETTES` 键互锁。
- 兼容矩阵正反例：每种 kind × 矢量三种几何 × 栅格，共覆盖 §4 矩阵的每一格。
- `build_classes` 纯函数精确断言：等间距与分位数在已知输入上的结果；边界含全相同值、单一值、负数、极端离群值、`classes` 取 3/5/7。
- 唯一值基数上限拒绝、抽样标记、全 NULL 拒绝。
- `LAND_USE` 关键词匹配：每个语义至少一例、大小写混合、未匹配走兜底灰并被列出。
- `validate_call` 正反例：§7 九条各至少一例。
- `tests/test_approvals.py` 的 `len(TOOLS)` 断言保持 62（无新工具）。
- `tests/test_cartography.py` 中"不传 styles/labels 时行为不变"的向后兼容断言。

### 8.3 真机冒烟 `scripts/smoke_symbology.py`

沿用 `scripts/smoke_cartography.py` 的惯例：`Traced` 记录全部 refusals、`checks` dict + `all()` + 退出码、`ink()` 像素占比、`digest()` SHA256；fixture 写在 `workspace/smoke/<uuid>`，只用合成数据（AGENTS.md 要求，绝不用用户数据集造 fixture）。

- **颜色直方图断言**（本期新增的核心验证手段）：导出 PNG 后统计实际出现的 RGB，断言该色板对应档位的色值全部在容差内命中，且 Pro 默认随机色未出现。这证明的是"色板真的落到了图上"，强于 `ink()` 的"图上有墨"，是在 agent 看不见图的前提下唯一能机器化验证配色正确性的办法。
- 每个色板 × 每种几何（面 / 线 / 点 / 栅格）建图导出，断言文件非空且色值命中。
- 标注：同数据"带标注"与"不带标注"两次导出的 `ink()` 存在显著差异。
- 只读性：源数据 SHA256 前后一致。
- 拒绝路径零残留：沿用 `only_published_files_remain` 的逐目录扫描，确认无暂存文件、无半成品 `.aprx`。
- **人眼验收**：导出 4 张代表性图（专题分级 / 土地利用唯一值 / 地形晕渲叠分层设色 / 带标注规范图）交用户确认。冒烟不替代这一步 —— 避让效果与整体观感无法机器判定。

### 8.4 文档与技能同步

见 §5.2 清单。两个内置技能包必须补 `styles`，因为它们当前正是"默认随机填色"的重灾区。

## 9. 验收标准

1. 四类目标图（专题分级、土地利用唯一值、地形分层设色叠晕渲、带标注规范图）都能由单次 `build_layout` + `export_layout` 产出，且导出图的实际 RGB 命中指定色板。
2. §7 九条前置校验全部生效，被拒调用零残留（无半成品 `.aprx`、不创建 `cartography/` 目录）。
3. 不传 `styles` / `labels` 时行为与改动前完全一致，既有测试全绿。
4. `export_layout` 仍只读：导出前后工程文件 SHA256 一致。
5. 工具总数不变，无新增 LLM 可见工具，`len(TOOLS)` 断言仍为 62。
6. 全量离线测试通过；真机冒烟全绿且结论写入 `docs/validation.md` 新批次，含"本轮未验证"段。
7. 用户人眼确认 4 张代表性图可接受。
