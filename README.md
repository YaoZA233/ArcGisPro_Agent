# ArcGIS Pro Agent

[**简体中文**](README.md) | [English](README.en.md)

面向 **ArcGIS Pro 3.5.2 / Windows** 的本地 GIS Agent。当前版本 `0.1.0`。

![license](https://img.shields.io/badge/license-MIT-green) ![python](https://img.shields.io/badge/python-3.11%20%7C%203.14-blue) ![ArcGIS%20Pro](https://img.shields.io/badge/ArcGIS%20Pro-3.5.2-orange)

用自然语言驱动 ArcGIS Pro 做数据检查、矢量与栅格分析、制图出图。模型只负责规划，**每一次工具调用（包括只读）都先询问用户，允许后才执行**；成果落在带唯一 ID 的任务目录里，全程可回溯。提供命令行和 ArcGIS Pro 内嵌停靠面板两条入口，共用同一个 Agent、连接配置、工具及任务记录。

- 支持 DeepSeek、Qwen、Kimi、GLM、OpenAI GPT、Anthropic Claude、xAI Grok 和自定义兼容接口 / 中转站；原有 DeepSeek 配置继续可用。
- **62 个结构化工具** + **7 个多步技能包**，参数全部类型化校验。
- 无第三方 Python 依赖，不向 ArcGIS 默认环境安装包；离线测试只需系统 Python ≥ 3.11。

**有界失败自纠**：聊天与面板使用 `DeepSeekPlanner`（`recovers=True`），所有共用该规划器的客户端（含已配置供应商）均可在实际工具调用返回 `ok=False` 后，把原始错误与已有 `hint` 反馈给模型重新决策。同一批次中尚未执行的调用会跳过，以 `NotExecuted` 闭合调用 ID，再请求模型；全成功批次的串行执行不变。静态 JSON 计划（CLI `run` / 工具箱）的 `PlanPlanner`（`recovers=False`）仍失败即停。先说明计划、用只读证据消歧、诊断后改变纠正动作只是提示词纪律，不是结构化预规划阶段，也不保证模型一定能解决问题。

| 章节 | 内容 |
| --- | --- |
| [快速开始](#快速开始) | 三条命令跑通第一次对话 |
| [在 ArcGIS Pro 里使用](#在-arcgis-pro-里使用) | 面板安装、授权卡片、附件、当前地图 |
| [多供应商与中转站](#多供应商与中转站) | 连接管理、协议范围 |
| [能力清单](#能力清单) | 62 个工具分组与参数约束 |
| [执行真实 GIS 任务](#执行真实-gis-任务) | JSON 计划与任务目录结构 |
| [配置](#配置) | 本地覆盖、解释器发现、系统提示词 |
| [仓库结构](#仓库结构) | 目录职责 |
| [验证与测试](#验证与测试) | 454 项离线测试、ArcPy 冒烟说明 |
| [安全与隐私](#安全与隐私) | 密钥存放、数据流向 |
| [当前边界与后续](#当前边界与后续) | 已知限制 |

每批功能与修复的具体改动、验证和限制记录在 [CHANGELOG.md](CHANGELOG.md)。

## 快速开始

在本仓库根目录打开 PowerShell，普通 Python 需要 **3.11 或以上**。无须安装第三方依赖，也无须向 ArcGIS 的默认环境安装包。

```powershell
# 第一次使用：在隐藏输入的提示中输入 DeepSeek API Key
.\scripts\agent.ps1 setup-key

# 在终端启动对话；/new 新对话，/chats 列出历史会话，/resume 编号 恢复，/memory 查看长期记忆，/forget 序号 删除，/quit 退出
.\scripts\agent.ps1 chat

# 自动发现 ArcGIS Python，检查 ArcPy、许可证、实际版本
.\scripts\agent.ps1 doctor
```

其他常用入口：

```powershell
.\scripts\agent.ps1 chat --allow-writes   # 预先启用写入能力；每一步仍会询问
.\scripts\agent.ps1 demo                  # 离线模拟：验证调度、日志，不生成 GIS 数据
.\scripts\agent.ps1 tools                 # 查看工具及其参数
.\scripts\agent.ps1 skills                # 查看多步任务技能包
.\scripts\test.ps1                        # 运行不依赖 ArcGIS 的测试
```

历史本机 ArcPy 核验记录（非本批自纠验证）：`E:\programs\ArcGIS\bin\Python\envs\arcgispro-py3\python.exe`，ArcGIS Pro **3.5.2**、Python **3.11.11**、产品级别 **ArcInfo**。注册表的版本字段可能是基础版本，应以 `doctor` 的 ArcPy 结果为准。

密钥保存在仓库下被忽略的 `.env.local`，是普通本机文件，不会自动加密；对应供应商的进程环境变量优先。请在隐藏输入的 `setup-key` 提示或面板专用 API Key 输入框填写，勿放入聊天消息或命令行参数。旧的 `config/local.toml` DeepSeek 配置继续生效，保存连接后以工作区连接配置为准。

如果 PowerShell 执行策略阻止脚本，可在允许执行本地脚本的 PowerShell 中运行；也可直接使用 Python：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m arcgis_agent demo
python -m unittest discover -s tests -v
```

开发时也支持 `python -m pip install -e .`，然后使用 `arcgis-agent` 命令；这一方式不是启动前提。

## 在 ArcGIS Pro 里使用

1. 先执行上面的 `setup-key`，然后重启 ArcGIS Pro。未配置密钥也可以打开面板管理连接；如需安装或重装插件，见本节末尾。
2. 在功能区打开 **GIS Agent → 打开 Agent**。面板可停靠、调整宽度，输入任务后点击 **发送**（输入框按 `Ctrl+Enter` 发送）。
3. 面板实时显示模型推理内容和逐步工具时间线：每个工具一步显示“步骤 N · 工具名 · 状态（等待授权 → 执行中 → 完成 / 失败）”与单步耗时，展开可查看参数与结果。执行中实时滚动 worker 运行日志（如 ArcPy 启动与工具阶段标记），卡住时不再静默——启动 180s 无进展会明确提示并终止。每步出现确认卡片，包含操作、参数、影响和预计输出；选择 **允许本次读取 / 允许本次写入**、**拒绝** 或 **取消任务**。默认焦点为拒绝，10 分钟未答复则停止。**新对话** 清除当前会话的上下文。

例如输入：“查看当前地图有哪些图层”“把道路生成 500 米缓冲区，再添加到当前地图”“筛选地类为 A 的要素，在副本中新增面积字段并计算平方米面积”。Agent 会根据工具结果继续规划，每一步都等待你的独立确认。输入数据可拖入面板，或提供完整本地路径。**允许写入** 复选框只预先启用写入能力，后续确认不会被跳过；未勾选时也可允许单次写入。

面板提供模型列表刷新、模型名称、思考程度（关闭 / low / high / max）、输出 token 上限和图片精度设置。模型推理内容在 API 流式返回时实时显示（“模型推理 · 实时生成”面板自动展开，手动收起后不再打扰）。顶部统计当前任务的真实输入、输出及缓存命中 token 和耗时，多次模型调用的用量会累计；API 没有返回用量时显示 `—`。

可通过附件按钮或拖放添加图片、UTF-8 文本、GIS 数据路径。图片与文本内容会发送给当前连接；`.shp`、`.gdb`、`.gpkg`、`.tif`、`.aprx`、`.lyrx` 和 `.xlsx` 只发送本地路径供工具使用。一次最多 6 个附件，图片单个最多 6 MiB，文本单个最多 256 KiB 且不超过 40000 字符，总量最多 16 MiB。目前 DeepSeek 的图片输入支持 `deepseek-flash`，切换为 `deepseek-v4-pro` 时请移除图片附件。

支持停止当前任务、历史对话恢复、导出 Markdown、复制答复、打开任务目录，以及将生成的数据添加到当前地图。会话保存在 `workspace/chats`；图片本体不会写入历史文件，恢复后如需再次查看图片请重新添加。停止时已创建的部分 GIS 成果可能保留。外观按钮可切换浅色或深色，动效可在设置中关闭，也遵循系统动画设置。

面板会把当前 Pro 工程路径作为对话上下文。授权 `map_context` 后，Pro SDK 可读取当前会话的地图、图层和未保存的可见性状态，并提供支持的本地数据路径。授权 `add_to_map` 或 `set_layer_visibility` 后可修改指定地图；**不会自动保存工程**。分析由独立 ArcPy 进程读取磁盘数据，无法访问 `CURRENT`，也不会继承 Pro 当前选择集。仅处理选中要素的任务须提供明确属性条件，或先导出选择集（`get_selection` → `export_selection`）；不能把整层处理冒充选中要素处理。

构建面板需要 .NET 8 SDK 与本机 ArcGIS Pro 安装目录（项目直接引用 `ArcGISProBin` 下的 Pro SDK DLL，默认 `E:\programs\ArcGIS\bin`，安装位置不同时用参数覆盖）：

```powershell
.\scripts\build-addin.ps1
.\scripts\build-addin.ps1 -ArcGISProBin 'D:\Program Files\ArcGIS\Pro\bin'
```

产物是 `addin\ArcGISAgent\bin\Release\net8.0-windows\ArcGISAgent.esriAddinX`，双击即可安装；插件更新后需重新安装才能看到新的面板界面。

保留的 Python 工具箱可另行在 **Catalog / 目录 → Toolboxes / 工具箱 → Add Toolbox / 添加工具箱** 中选择 `arcgis/ArcGISAgent.pyt`。其中 **Inspect current project / 查看当前工程** 在 Pro 进程内读取 `CURRENT`；**Execute agent plan / 执行任务计划** 运行 JSON 计划。

工具箱模式允许 `project_info` 的 `project` 参数为 `CURRENT`。CLI 和聊天面板的后台进程必须使用 `.aprx` 路径，不能访问 Pro 的 `CURRENT`。

## 多供应商与中转站

在 Pro 面板展开 **模型与运行设置 → 管理连接 / API 密钥**，选择供应商预设，填写实际模型 ID 与密钥，点击 **保存并使用此连接**。同一家需要多个账户或中转地址时，点击 **从预设新建独立连接**。顶部 **当前连接** 用于切换，**刷新** 获取账户模型列表，也可手动输入模型 ID。

```powershell
.\scripts\agent.ps1 providers list
.\scripts\agent.ps1 providers presets
.\scripts\agent.ps1 setup-key --provider qwen
.\scripts\agent.ps1 providers use qwen
.\scripts\agent.ps1 chat
# 对话内：/providers、/provider claude、/models、/model 实际模型ID、/status
# 自定义接口：先修改示例中的地址、模型与能力
.\scripts\agent.ps1 providers save examples\provider-relay.json
.\scripts\agent.ps1 setup-key --provider my-relay
.\scripts\agent.ps1 providers use my-relay
# 删除非当前连接：providers delete 连接ID
```

支持 OpenAI Chat Completions 兼容协议和 Claude 原生 Messages 协议。图片、工具、推理参数按具体模型设置，不能只凭厂商品牌判断能力；预设型号也不保证你的账户有权限。各连接密钥独立保存在本机，不会借用 DeepSeek 密钥。详细接口地址、推理模式、存储方式和协议范围见 [多模型接入说明](docs/providers.md)。

## 能力清单

| 分组 | 工具 |
| --- | --- |
| 检查和读取 | `environment`、`project_info`、`describe`、`list_datasets`、`find_datasets`、`count`、`read_rows`、`verify_dataset` |
| 数据接入 | `import_table_points`、`convert_dataset` |
| 要素与栅格分析 | `buffer`、`copy_features`、`clip`、`intersect`、`merge`、`dissolve`、`project`、`spatial_join`、`select_features`、`select_by_location`、`near_analysis`、`create_thiessen_polygons`、`clip_raster`、`union`、`erase`、`reshape_geometry`、`join_table`、`summarize_statistics` |
| 表面与栅格分析 | `interpolate_surface`、`kernel_density`、`polygon_to_raster`、`terrain_derivatives`、`contour_lines`、`zonal_statistics`、`extract_by_mask`、`sample_raster_at_points`、`reclassify`、`hillshade`、`resample`、`project_raster`、`fill`、`flow_direction`、`flow_accumulation` |
| 编辑副本与导出 | `add_field`、`set_field_value`、`delete_fields`、`calculate_geometry`、`repair_geometry`、`calculate_field`、`define_projection`、`export_table_csv`、`export_features` |
| 制图与出图 | `build_layout`、`export_layout` |
| 技能 | `list_skills`、`read_skill` |
| 长期记忆 | `remember` |
| Pro 面板宿主 | `map_context`、`get_selection`、`export_selection`、`add_to_map`、`set_layer_visibility` |

覆盖范围包括：数据检查、复合属性筛选、按位置选择、邻近分析、泰森多边形、缓冲区、裁剪、相交、融合、投影、空间连接、栅格裁剪、属性与几何副本编辑、受限算术字段计算、结构化数据验收、CSV/GeoJSON/Shapefile 导出、表格与 GPKG/KML 数据接入、插值与密度、分区统计、地形与等高线、重分类与山体阴影、栅格重采样与投影、水文链（填洼 / 流向 / 汇流累积）、叠合与擦除、几何重塑、属性连接、布局出图与图片/PDF 导出、声明式技能包读取，以及 Pro 当前地图读取、添加图层和可见性控制。

另内置 **7 个技能包**（水文链、适宜性选址、数据质量检查、插值成图、坐标统一叠合、表格转密度图、副本编辑交付），把已验证工具编排成多步方法模板供模型加载，见 [技能说明](docs/skills.md)。

参数、筛选条件和限制见 [工具说明](docs/tools.md)。`agent.ps1 tools` 返回完整类型化参数 schema。当前只开放上述工具，不等于开放 ArcGIS 全部工具。

`distance` 当前支持正数加 `Meters`、`Kilometers`、`Feet` 或 `Miles`，例如 `500 Meters`；`reshape_geometry` 的 `tolerance` 使用同样的线性写法。分析工具的 `cell_size`、`interval`、`search_radius`、`z_factor` 只填数字，单位随输入数据，地理坐标系须用度值（如 `0.0005`）。`output_name` 为字母开头的 ASCII 字母、数字或下划线，最长 64 字符；由系统分配输出目录，不接受任意输出路径。

## 执行真实 GIS 任务

复制并编辑 `examples/inspect_project.json` 或 `examples/buffer.json`，把示例数据路径改成你自己的**绝对路径**。计划中的路径不相对于 JSON 文件解析。

```powershell
# 读取一个已保存的工程
.\scripts\agent.ps1 run examples\inspect_project.json

# 读取数据，再创建 500 米测地线缓冲区
.\scripts\agent.ps1 run examples\buffer.json --allow-writes

# 仅检查计划与调度流程，不调用 ArcPy
.\scripts\agent.ps1 run examples\buffer.json --mock --allow-writes
```

CLI 在每一步显示 `[等待本次授权]`：输入 `y` 允许、`n` 拒绝、`c` 取消，直接回车默认拒绝。`--allow-writes` 和 `--mock` 都不跳过单步询问。拒绝后停止该任务的后续步骤，保留此前已经生成的成果。Python 工具箱使用同样语义的 Windows 确认框；无授权处理器的程序调用返回 `approval_required`，不执行工具。

每次任务自动创建：

```text
workspace/runs/<UTC时间_唯一ID>/
  events.jsonl           # 工具请求、逐次授权决定、实际执行、结果及任务结束事件
  summary.json           # 最终状态、参数、返回值、错误
  outputs/results.gdb/   # 真实写入任务的成果库
  outputs/<名称>.tif     # 栅格成果（使用栅格工具时）
```

日志时间使用 UTC。离线模拟的工具观察带 `simulated: true`，不会生成假 GDB。`completed` 仅表示规划器结束且最后一条观察不是失败（也可能未调用工具）；此前的失败仍可保留在轨迹中，不表示所有观察都成功，更不证明 GIS 任务已通过业务验收。静态计划工具失败、LLM 连续失败达到预算、规划器在最后一次工具失败后直接结束，或执行循环抛出异常，均返回 `failed`。失败观察保留原始错误与已有 `hint`；预算耗尽时，`summary.json` 还记录最后错误类型、带连续失败次数的消息及已有 `hint`，`events.jsonl` 记录 `recovery_exhausted`。

缺少写入能力返回 `blocked`；已用尽工具调用步数且规划器仍请求工具时返回 `step_limit`，不会执行超限调用。拒绝、缺少授权处理器、授权过期分别返回 `denied`、`approval_required`、`approval_expired`；用户取消、断线、取消信号或工具返回 `error.type="Cancelled"` 时返回 `cancelled`。这些安全终止与执行循环内异常（包括 runtime 抛出的异常）均不进入自纠。所有未完成状态的命令行退出码非零。

纠正动作也必须重新逐次授权，包括只读探查。写操作必须同时满足本次授权和写入能力；能力可来自调用方显式启用的全局写入能力**或**本次单次写入许可，单次许可不延续到下一调用。新数据成果仍限唯一任务目录，纠错不会自动删除或覆盖已有成果。

## 配置

可直接依赖自动发现，也可复制 `config/agent.example.toml` 为 `config/local.toml`：

```powershell
.\scripts\agent.ps1 --config config\local.toml doctor
.\scripts\agent.ps1 --config config\local.toml run examples\buffer.json --allow-writes
```

配置文件中的相对路径以配置文件所在目录为基准。CLI 和面板会自动读取仓库的 `config/local.toml`；`--config` 可为 CLI 指定其他文件。`config/local.toml` 已被 `.gitignore` 排除，需要共享默认值时改 `config/agent.example.toml`。没有配置文件时，CLI 的成果工作区默认为当前目录下的 `workspace`；面板启动后台进程时以仓库为当前目录。工具箱默认使用本仓库的 `workspace`，有 `config/local.toml` 时自动读取。

| `[agent]` 参数 | 默认值 | 配置范围与含义 |
| --- | --- | --- |
| `max_steps` | `16` | 精确整数 `1..100`；每次任务的工具调用上限，含探查与纠正调用，不是模型请求次数 |
| `failure_budget` | `3` | 精确整数 `1..20`；可纠错规划器的连续工具失败上限，不是额外重试次数 |

布尔、浮点和字符串不作为整数接受。`Agent`、`Settings`、`ChatSession` 的 `max_steps` 默认均为 16；CLI / 工具箱通过配置使用的默认上限也由 8 提高为 16，显式配置值仍生效，静态计划失败即停不变。一次工具成功会清零连续失败计数；默认预算 3 表示首次失败加至多两次未成功的纠正尝试，第三次连续失败即停（预算 1 则首次失败即停）。中间成功可重置预算，但所有实际工具调用始终受总步数限制。为获取最终答复或判定 `step_limit`，工具步数耗尽后仍可能再请求一次模型。

`[agent] data_roots` 配置 Agent 可自主搜索的数据根目录（最多 16 个）：用户没给路径时，模型会用 `find_datasets` 在这些目录内按关键词递归查找数据（含 `.gdb`/`.gpkg` 内部成员），而不是反问路径；把你常用的 GIS 数据目录加进去即可。未配置时 `find_datasets` 返回明确提示，Agent 会退回向用户询问位置。

**长期记忆**保存在 `workspace/memory.json`（不入库）：模型用 `remember` 工具记录稳定事实与用户偏好（常用数据目录、坐标系与单位习惯、称呼、成果交付偏好等），一条一个事实、精确去重、上限 200 条超限淘汰最旧；新会话第一条消息会自动注入既有记忆，与当前指令冲突时以当前指令为准。密钥、凭据与一次性任务细节绝不写入记忆。终端内用 `/memory` 查看全部记忆、`/forget 序号` 删除某条；`/chats` 列出历史会话、`/resume 编号`（可用短前缀）恢复某个会话继续对话。被拒绝、写入未授权、失败或取消的轮次也会保留在历史中（未执行的调用以结构化“未执行”结果闭合），因此下一轮模型能看到此前尝试过什么、为何没完成，而不是失忆重来。

接入模型的系统提示词是内置的 [src/arcgis_agent/prompts/system.md](src/arcgis_agent/prompts/system.md)（模型的授权、工具使用、验收与报告规范，单一事实来源），直接编辑该文件即可调整模型行为；契约测试守护关键规则标记，大幅扩写请留意每轮请求的 token 成本。

`ARCGIS_PRO_PYTHON` 可指定 ArcGIS Pro 克隆环境中的 `python.exe`，优先于配置。注册表发现只选择默认环境；使用克隆环境时请显式指定。`doctor` 对照 `expected_version` 检查精确版本；`run` 不额外执行版本探测。

## 仓库结构

```text
.
├── src/arcgis_agent/         # Agent 核心（纯 Python，无第三方依赖）
│   ├── agent.py              # 规划-执行循环、工具步数上限与连续失败自纠预算
│   ├── catalog.py            # 62 个工具的 schema、参数校验与 ArcPy 实现
│   ├── providers.py          # 供应商预设、连接配置存储与密钥读写
│   ├── deepseek.py           # OpenAI 兼容流式客户端与共用 LLM 规划器
│   ├── interfaces.py         # CLI / chat / providers / doctor 等命令入口
│   ├── approvals.py          # 逐次授权处理器
│   ├── runtime.py            # ArcPy 子进程 worker、超时与停滞看门狗
│   ├── chat.py               # 会话、历史与用量统计
│   ├── memory.py             # 长期记忆存储与 remember 工具
│   ├── cartography.py        # 布局出图与图片/PDF 导出
│   ├── selections.py         # Pro 选择集快照与 selection_token
│   ├── skills.py             # 声明式技能包读取与校验
│   ├── prompts/system.md     # 系统提示词（单一事实来源）
│   └── skills/*.json         # 7 个内置技能包
├── addin/ArcGISAgent/        # ArcGIS Pro 停靠面板（C# / WPF，.NET 8）
├── arcgis/                   # 保留的 Python 工具箱（*.pyt 与工具说明）
├── scripts/                  # agent.ps1、test.ps1、build-addin.ps1、smoke_*.py
│   └── debug/                # 一次性诊断脚本，见 scripts/debug/README.md
├── tests/                    # 离线契约测试，不依赖 ArcGIS
├── examples/                 # JSON 计划示例
├── config/                   # agent.example.toml 模板（local.toml 不入库）
├── docs/                     # architecture / tools / providers / skills / validation
│   └── notes/                # 历史排障笔记（过程留痕，非使用文档）
└── workspace/                # 运行时产物，整体不入库（任务留痕、会话、长期记忆、连接配置、冒烟数据）
```

## 验证与测试

454 项离线测试（出图符号化功能较上一批次 328 项新增 126 项：`test_symbology` 114 项，另含 cartography 接线与技能包 `styles` 用例）覆盖类型化工具参数、逐次授权、单次写入许可、拒绝/取消/断线、授权响应重放、有界循环、任务留痕、worker 协议异常与恢复、多模型协议与子进程协议、出图流程、出图范围单位与坐标系一致性、只读导出、技能包校验、工具事件步骤编号、worker 进度流与启动停滞看门狗、自主数据发现、长期记忆增删去重与上限、未完成轮次的历史闭合、记忆注入与会话恢复、失败自纠、连续失败预算与成功清零、失败批次重新规划、终止边界、配置接线与提示词纪律，以及预设色板符号化、分级与唯一值计算、样式与标注校验、catalog 跨参数校验、CIM 矢量/栅格 renderer 与 Arcade 标注、技能包符号化契约。

离线验证（本次文档同步已重跑）：系统 Python **3.14.6** 经 `PYTHONPATH=src python -m unittest discover -s tests` **454/454 通过、退出码 0**（含 `test_symbology` 114 项），提示词 `## 出图` 章节扩充后实测 **4448 字符**（上限 8000）。真机冒烟：Pro Python **3.11.11** 运行 `scripts/smoke_symbology.py`（两次独立运行 **41 项检查全部通过**、9 条拒绝路径零残留）与既有 `scripts/smoke_cartography.py`，明细见 [验证记录](docs/validation.md)。上一批次（Agent 失败自纠）的系统 Python `scripts/test.ps1`（**6.635s**）与 Pro Python `discover`（**6.939s**）记录均为 **328/328 通过**、提示词当时 **3709 字符**。完整命令与本地日志位置见 [CHANGELOG](CHANGELOG.md)。[CI](.github/workflows/tests.yml) 配置 Python 3.11 / 3.14 矩阵，云端不依赖 ArcGIS，未据此声称本批云端运行通过。未执行在线模型/API 端到端对话或 GIS 业务层面验收；用 Pro Python 运行离线测试不等于有许可证的 ArcPy 冒烟，冒烟通过也不等于配色观感或分析结论业务正确；整分支审查仍待完成。以下是真实验证的运行说明，历史结果不代替本批验证。

```powershell
.\scripts\test.ps1
```

本地真实 ArcPy 集成检查：

```powershell
& 'E:\programs\ArcGIS\bin\Python\envs\arcgispro-py3\python.exe' scripts\smoke_arcpy.py
```

它在 `workspace/smoke/<唯一ID>` 新建人工要素与栅格，经独立 ArcPy 子进程运行缓冲区回归、数据接入、假地理数据库守卫，以及批次 2 的全部分析工具（插值、密度、面转栅格、分区统计、掩膜提取、栅格采样、地形、等高线、统计、属性连接、叠合、擦除、几何重塑），并核验拒绝路径与源数据未被改动。保留输入和输出供复查，不使用你的已有数据。

`scripts/smoke_gis_tools.py` 在独立人工数据目录验证 20 个真实 ArcPy 工具及原始数据未变、禁止覆盖。`scripts/smoke_agent_tools.py <人工数据目录>` 使用已配置模型验证“检查 → 筛选 → 缓冲 → 统计”的完整流程，产生 API 用量。这些 fixture 脚本的测试授权回调只允许其人工数据根目录内的操作，产品入口仍逐次交由用户确认。

出图工具另有 `scripts/smoke_cartography.py`，同样用 Pro Python 运行。它在 `workspace/smoke/cartography_<唯一ID>` 新建人工面要素、投影面、栅格和空表，经独立 ArcPy 子进程调用 `build_layout` 与 `export_layout`，核对生成的 `.aprx`、PNG/JPEG/PDF/TIFF/SVG 成果、导出前后工程文件摘要不变（只读导出）、`project_info` 返回的布局清单、导出图的**非白像素占比**（证明图幅真的渲染出数据，而不是一张空图），以及禁止覆盖、无几何图层、坐标系不一致、地理坐标系配米制范围、未知布局、工程不存在这些拒绝路径和被拒调用不留残留。它不打开也不修改你已有的工程。符号化另有 `scripts/smoke_symbology.py`，同样用 Pro Python 运行：它在 `workspace/smoke/symbology_<唯一ID>` 的合成栅格与矢量上按预设色板建分级色彩、唯一值、栅格多段色带与 Arcade 标注，用导出图像素取样证明色板**真的落色**、分级断点归属正确（41 项检查），并覆盖色板与数据类型不匹配、字段类型不对、分级参数越界、唯一值超 10 类、栅格标注等 9 条拒绝路径且零残留。

`scripts/smoke_toolbox.py` 默认只验证工具箱发现，返回 `executed: false`；加 `--interactive` 才执行计划并弹出逐次确认框。`scripts/smoke_chat.py` 的真实工具步骤需要终端确认。具体测试结果及未验证项见 [验证记录](docs/validation.md)。

受限执行沙箱中 ArcPy 可能发生 Windows 原生进程崩溃（本机曾返回 `3221225477`）；同一命令在正常本机执行环境中可以运行。此错误不会被当作成功。它与 Python 工具代码抛出的异常不同。

## 安全与隐私

完整说明见 [SECURITY.md](SECURITY.md)。要点：

- API 密钥只写入仓库根的 `.env.local`；`workspace/` 只保存连接配置（接口地址、模型 ID、能力开关，不含密钥）。两者都被 `.gitignore` 排除，源码中不出现任何真实密钥。
- 用 `setup-key` 的隐藏输入或面板的密钥输入框填写。不要放进命令行参数（会进入 Windows 进程列表与 shell 历史），也不要贴进对话或 Issue。
- 对话内容、附件文本和已授权读取的工具结果会发送给当前连接配置的服务商；GIS 数据文件本身不上传，只发送本地路径。
- 任务日志包含输入路径、字段信息和授权决定，请按本地 GIS 数据的权限要求管理 `workspace`。
- 本仓库不收集遥测，不联网执行代码；除模型接口调用外没有外部网络请求。

## 当前边界与后续

- 已有：多供应商流式对话、连接配置、附件、用量、历史会话、CLI、Pro 面板、62 个结构化工具、7 个声明式技能包、逐次用户授权、当前地图与选择集宿主桥接、制图出图与图片/PDF 导出和任务日志。
- 后续可扩展：更多分析工具、地图样式与分栏制图、业务级成果质量检查。
- 表面与栅格分析需要 Spatial Analyst 扩展；执行前签出失败时整次调用失败并说明许可证状态，不返回部分结果。

CLI 每次调用启动独立 ArcPy 进程，可靠性优先，但有导入开销。CLI 超时会结束工作进程：若工作进程已经写出完整结果，结果照常返回并标记 `worker_terminated`；否则错误中保留 worker 的 stderr 尾部与恢复提示。已生成的部分成果可能保留，当前不提供事务回滚。Pro 内部工具箱在当前进程同步执行，没有强制超时；需要使用 Pro 的任务机制管理长任务。

`Timeout` / `Stalled` 若返回 `ok=False`，LLM 会话仍可将失败反馈模型重新决策，后续调用仍受逐次授权、连续失败预算与总步数限制；终止 worker 不等于总任务立即终止。`180s × failure_budget` 不是通用总截止时间：runtime 模式、超时配置、模型请求、授权等待及成功后的预算清零都会影响总耗时。部分输出保留，纠错不自动删除或覆盖。

数据编辑工具先复制数据，再编辑副本，关闭覆盖；不提供删除数据集与任意 Python 执行工具。工程方面只有一处例外：`build_layout` 会复制 Pro 自带的空白模板，在你当前任务的 `workspace/runs/<任务ID>/outputs/cartography/` 里新建并保存一个独立出图工程；它从不打开、保存或改动你已有的工程，`export_layout` 也只读打开出图工程（真实冒烟用文件摘要核验导出前后一致）。添加图层与可见性工具会修改当前地图，每次都需明确授权。

## 贡献

改动前先读 [AGENTS.md](AGENTS.md) 里的仓库约束（核心保持可脱离 ArcPy 导入、写入需声明并逐次授权、不往 Esri 基础环境装包、公开计划 schema 或 CLI 变更要同步 README 与工具契约测试）。提交前运行 `.\scripts\test.ps1`；每批功能改动或修复按格式记入 [CHANGELOG.md](CHANGELOG.md)。

架构说明见 [docs/architecture.md](docs/architecture.md)，参考来源记录见 [docs/reference.md](docs/reference.md)。

## 许可证

[MIT](LICENSE) © 2026 YaoZA233
