# 更新日志

## 2026-11-06：整理 CLI 对话输出和 Markdown 排版

- 交互式对话按模型思考、模型说明、工具步骤与结果、最终答复及用量状态分区；授权框集中展示操作、影响、参数与预计输出，保留逐次确认和默认拒绝。未返回用量时明确显示未知。
- 终端 Markdown 保留段落和原始换行，中文长行与列表按显示宽度折行；宽表格改为折行而非截断，支持转义竖线。非终端输出不使用 ANSI 样式。
- 影响 `src/arcgis_agent/interfaces.py`、`approvals.py`、`mdrender.py`、`tests/test_terminal_output.py`、`tests/test_mdrender.py` 和 README。
- 验证：`./scripts/test.ps1` 离线测试通过，覆盖交错的推理、工具结果、表格、授权默认拒绝和缺失用量。未连接真实模型或执行真实 ArcPy 工具。
- 限制：为了按完整片段渲染 Markdown，推理与正文不逐 token 显示；CLI 仍使用无第三方依赖的简化 Markdown 解析器，不覆盖所有扩展方言。

## 2026-11-06：修复 Agent 面板 Markdown 输出

- 面板流式答复、完成答复与历史答复使用同一 Markdig 解析流程，支持常见 CommonMark 块和行内嵌套、表格、任务列表、删除线和自动链接；表格单元格保留格式，宽表格可横向滚动。原始答复复制和导出保持不变。
- 涉及 `addin/ArcGISAgent/MessageRenderer.cs`、`AgentView.xaml.cs`、两个 C# 项目文件、预览自检和 `scripts/build-addin.ps1`；插件包包含 Markdig 依赖。
- 验证：`./scripts/test.ps1` 458 项离线测试通过；WPF 预览 Markdown 自检与截图通过；插件 Release 构建和打包通过。未在 ArcGIS Pro 内安装或交互验证。
- 限制：原始 HTML 作为文字显示，图片以说明及地址显示、链接显示地址；不支持所有 Markdown 方言、数学公式或图表扩展。终端渲染仍为独立的简化实现。

按功能批次记录具体改动、涉及模块、验证结果和当前限制。项目版本当前保持 `0.1.0`，以下新增能力尚未单独发布版本。

## 2026-11-06：出图符号化与标注（预设色板 + 分级/唯一值 + Arcade 标注）

- 能力：`build_layout` 新增两个可选参数 `styles`（每层 `{layer, palette, field?, classes?, method?}`）与 `labels`（每层 `{layer, field, size?}`），由 **9 个预设色板**驱动符号化与 Arcade 标注，返回新增 `symbology` 段（实际 renderer、色板、分级断点与色值、唯一值类别、`unmatched` 未配到语义色的地类名、`sampled` 是否因超过 20 万值抽样近似）与 `labels` 段。色板：`BLUES`/`GREENS`/`REDS`/`RD_BU`（SEQUENTIAL 连续）、`CATEGORY10`/`LAND_USE`（QUALITATIVE，按地类名配语义色）、`ELEVATION`/`HILLSHADE_GRAY`（TERRAIN）、`BASE`（示意图）。**kind × 数据类型兼容矩阵**：栅格接受 SEQUENTIAL+TERRAIN 共 6 板，一律走连续多段色带（RASTER_RAMP，禁 `field`）；矢量接受 SEQUENTIAL→分级色彩、QUALITATIVE→唯一值、BASE→简单符号 共 7 板，TERRAIN 用于矢量、QUALITATIVE/BASE 用于栅格均被拒。分级 `classes` 默认 5、只能取 3/5/7（预设色板只在这三档备有色值，`CLASS_COUNTS` 由色板档数交集派生、schema 用 enum 同源），`method` 默认 `EQUAL_INTERVAL`、仅 `EQUAL_INTERVAL`/`QUANTILE`；唯一值类别上限 10，超出即拒；标注字号默认 9、限 6–24，栅格不能标注。轮廓固定不开放（分级白线 #FFFFFF 0.2、唯一值深灰线 #666666 0.4）。配色不开放自由调色，**换配色需重建工程**（`output_name` 不得与已有成果重名）。
- 图名颜色修正（`cartography.py` 的 `_add_title`）：改为写 `CIMRGBColor().values = [0, 0, 0, 100]`。此前用 `red=`/`green=`/`blue=` 关键字设色会被运行时静默忽略。
- 影响模块：新增 `symbology.py`（纯 CIM definition 原地构造，不碰工程文件、不做 IO、不决定输出路径；字段值读取经调用方注入的 arcpy 游标句柄委托）；`catalog.py` 加 `styles`/`labels` schema 与跨参数校验（同一图层重复符号化/标注拒绝）；`cartography.py` 接线并把全部符号化/标注拒绝前移到复制工程模板之前；`interpolation_surface_map`/`csv_points_to_density_map` 两个技能包的 `build_layout` 步骤默认出符号正确的图（晕渲 ELEVATION、等高线 BASE、密度 REDS）；`prompts/system.md` 出图章节新增色板选型、参数禁忌与如实汇报纪律。
- 真机 CIM 实测行为（本机 Pro 3.5.2 实测，已落进 `symbology.py` 实现与注释，后续改动勿凭记忆违反）：① `CIMRGBColor` 的颜色只存在 `values=[r,g,b,alpha]`，`red=`/`green=`/`blue=` 关键字被静默忽略并渲成灰；② 从零构造的符号会落盘但完全不渲染，必须 `copy.deepcopy` 运行时生成的默认符号再改色；③ 栅格符号化改 `definition.colorizer`，`CIMRasterLayer.renderer` 恒为 `None`（arcpy.cim 内根本没有 CIMRaster\*Renderer 类），故 RASTER_RAMP 分派必须在读 `.renderer` 之前，否则 AttributeError；④ 容器属性名：分级是 `breaks`（`classBreaks` 赋值被静默丢弃）、唯一值是 `groups`→中间层 `CIMUniqueValueGroup.classes`（直接赋 `classes` 被丢弃）、栅格色带是 `CIMMultipartColorRamp.colorRamps`；⑤ 标注改运行时默认那个 `labelClass`（整列表替换会落盘但零字渲染），真正生效的字号是 `CIMTextSymbol.height`（`fontSize` 回读缺失），并需另置 `labelVisibility` 为真。
- 验证：离线 `PYTHONPATH=src python -m unittest discover -s tests` 全绿 **458 项、退出码 0**（本功能新增 `tests/test_symbology.py` **117 项**：色板常量完整性、断点与唯一值计算、样式与标注校验、catalog schema 与跨参数校验、CIM 助手与矢量/栅格 renderer、Arcade 标注，另含 cartography 接线 11 项与技能包 `styles` 契约用例）；提示词实测 **4454 字符 < 8000**。真机 Pro Python 3.11 运行 `scripts/smoke_symbology.py` 两次独立运行 **41 项检查全部通过**、9 条拒绝路径各返回预期中文错误且零残留，合成 fixture 仅写入 `workspace/smoke/symbology_<uuid>`（详见 `docs/validation.md`）。
- 已知限制：栅格分级（直方图拉伸）未实现，但已记录 `colorizer.stretchStats.histogram` 可作为将来实现入口；标注避让（防重叠）为最小实现，仅设表达式与字号，不做冲突消解；色板观感未经制图专业评审；换配色需重建工程，不能在已有 `.aprx` 上原地改色板；`LAND_USE` 语义色按关键词组顺序取首个命中（"水浇地""湿地"须排在水体组之前，否则静默错配），未命中落兜底灰并经 `unmatched` 回报。
- 全分支终审整改（2026-11-06 独立评审后）：① 修掉一条 Important——`classes` 原按 3–7 区间放行，但预设色板只有 3/5/7 档色值，`classes=4/6` 会过了 schema 与校验却在复制工程模板之后的 `plan_style` 才被拒；现 `CLASS_COUNTS=(3,5,7)` 由 SEQUENTIAL 档数交集派生，`symbology` 校验、`catalog` schema（改为 enum）、`system.md`/`tools.md` 措辞全部同源，非法档数在建模板前即被拒且点名合法值。② 修掉一条 Minor——栅格色带误传 `field` 时，旧文案建议"改用支持分级的色板"，而栅格换任何 SEQUENTIAL 板同样拒 `field`；改为栅格专属指引（去 `field`，需按字段分级请先 `reclassify` 离散或转要素）。③ 补 4 条离线用例：非法档数 4/6 拒绝、合法档数全放行、栅格 `field` 拒绝文案、以及双样式图层接线（钉死"正确的 plan 落到正确的图层"而非把 `plans[0]` 套满所有层）。评审确认 CIM 三条铁律、只读工程、拒绝前置、离线不 import arcpy 六项不变量均未被破坏，图名格式未落地等已知缺陷维持原判（批次 3 起即存在，与本批符号化无关）。

## 2026-11-06：Agent 失败自纠与更长多步任务（控制流、可配置预算与提示词纪律）

- 本条合并本功能尚未提交的 Task 2 / Task 3 记录，并补全 Task 1 控制流与 Task 4 文档；下方既有历史不变。根因是 `agent.py` 在工具失败后立即退出，既有失败观察回流尚未给模型纠错机会。
- 失败自纠（`src/arcgis_agent/agent.py`、`deepseek.py`）：`PlanPlanner.recovers=False`，CLI 静态计划与 Pro 工具箱保持失败即停；`DeepSeekPlanner.recovers=True`，聊天 / 面板及所有共用该规划器的供应商客户端可在实际工具调用返回 `ok=False` 后重新决策。未声明能力的 planner 仍失败即停。同批次剩余调用以 `NotExecuted` 闭合 ID、清空 pending 后再请求模型，不执行失败前的旧决策；全成功批次仍串行执行。
- 有界恢复（`agent.py`）：一次成功清零连续失败计数；`failure_budget=3` 是首次失败加至多两次未成功纠正，第三次连续失败即停，不是额外重试三次；预算 1 首次失败即停。预算耗尽返回 `failed`，`summary.json` 保留完整观察及最后错误类型、含失败次数的消息与已有 `hint`，`events.jsonl` 记录 `recovery_exhausted`。`completed` 仅指规划器结束且最后观察不是失败（允许无工具调用），此前失败可仍在轨迹中；最后工具失败后直接结束仍为 `failed`，并非所有工具均成功或 GIS 业务验收通过。
- 安全边界不变：拒绝、取消、授权过期、缺少授权处理器、写入未授权及循环内异常（含 runtime / 模型请求抛出异常）立即终止，不进入自纠；工具返回 `Cancelled` 也以取消终止，取消优先。每次实际调用（含只读探查和纠正）独立授权；写入能力是全局 `allow_writes` **或**本次单次写入许可，二者满足其一即可，但都不能代替本次授权，单次许可不传递。新数据输出仍限唯一任务目录，禁止删除或覆盖已有成果，不隐式保存源工程。
- 配置与接线（`config.py`、`chat.py`、`interfaces.py`、`config/agent.example.toml`）：`Agent` / `ChatSession` / `Settings` 的 `max_steps` 默认统一由 8 改为 16，范围仍为精确整数 `1..100`；CLI / 工具箱默认步数也提高，不能称为所有行为零变化。新增 `failure_budget=3`，配置加载与 Agent 构造器接受精确整数 `1..20`，拒绝布尔、浮点、字符串及越界值。`Settings.load → create_session → ChatSession → Agent` 透传预算；保留 `ChatSession` 第五个位置参数 `memory` 及显式步数覆盖。直接构造 ChatSession 的预算在 `send()` 创建 Agent 时校验；未改用户 `config/local.toml`。
- 提示词纪律（`src/arcgis_agent/prompts/system.md`）：多步调用前简短说明计划，依据 `error` / `hint` 诊断后采用不同纠正动作；用逐次授权的 `describe` / `find_datasets` / `read_rows` 补齐事实，以地图上下文和元数据消歧，仅对剩余业务取舍提问。空搜索只在配置根内尝试合理替代关键词，仍无结果则如实报告，不无限扫描或编造路径。仅提示词纪律，不引入结构化预规划阶段，也不保证全部技术问题可解；许可证、访问权限、安全审批及必要业务范围确认不能省略。无获授权的可行替代方案、缺许可或预算耗尽时报告尝试、阻碍、未完成步骤及保留成果。删除“是否覆盖已有成果”的业务选择示例；即使用户同意也不能覆盖。保留空选择集、禁任意代码、Spatial Analyst 扩展许可警告与离线模拟 / 工具成功 / 业务验收三层边界。
- 离线覆盖（`tests/test_foundation.py`、`test_chat.py`、`test_memory.py`、`test_system_prompt.py`）：恢复测试 fake 返回 `Action` 而非 dict；覆盖失败后恢复、预算耗尽与成功清零、安全终止、失败批次 ID 闭合与重新规划、失败后不得假完成、默认值/类型/边界校验、工厂接线与位置记忆兼容、16 步完成/第 17 步不执行及显式上限、未完成历史与提示词授权契约。新增 **41 项**，基线 287 → **328 项**；工具总数契约仍为 62，提示词实测 **3709 字符 < 8000**。
- 最终验证（主控已执行，本轮文档续作不重跑）：系统 Python **3.14.6** 经 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test.ps1`，**328 项 OK，6.635s，退出码 0**；已安装 Pro Python **3.11.11** 经 `PYTHONPATH=src "E:/programs/ArcGIS/bin/Python/envs/arcgispro-py3/python.exe" -m unittest discover -s tests -v`，**328 项 OK，6.939s，退出码 0**。本地证据为忽略目录中的 `workspace/self-correction/final-system-tests.txt` 与 `workspace/self-correction/final-pro-python-tests.txt`，不以此前中间轮次或计划预估计数代替最终结果。
- 文档（`README.md`、`README.en.md`、`docs/architecture.md` 及本功能既有 spec / plan）：同步预算、状态、授权、验证边界，在原规格与计划顶部记录实际执行状态及偏差；下方字面示例和提交步骤保留为历史提案，不是执行记录。用户未要求提交，未自动创建 commit；实现已离线验证，仍待整分支审查。
- 限制：本功能没有在线模型/API、真实 ArcPy（含许可证核验或 GIS 冒烟）及 GIS 业务验收；Pro Python 下的离线测试不等于真实 ArcPy 验证。纠错质量与模型遵循度未实测，原有 worker 测试诊断输出仍存在。`Timeout` / `Stalled` 返回 `ok=False` 时按普通失败反馈，仍受逐次授权与预算约束；`180s × failure_budget` 不是通用总截止时间，实际耗时取决于 runtime、超时配置、模型/授权等待及成功后的预算清零，总工具调用仍受步数上限约束。部分输出保留，不自动清理、覆盖或事务回滚；Pro 进程内工具箱没有强制超时。

## 2026-11-06：上下文与长期记忆（失忆 bug 修复 + 终端会话恢复 + 跨会话记忆）

- 问题：终端对话表现为"没有上下文、没有记忆"。三处根因：① `chat.py send()` 只在 `status == "completed"` 时把 `planner.messages` 落回 `self.history`，被拒绝 / 写入未授权 / 失败 / 取消 / 步数超限的轮次全部丢弃，下一轮模型看不到此前尝试；② 终端只有 `/new`，没有列出与恢复历史会话的入口（`workspace/chats/*.json` 早已持久化却无从取回）；③ 没有任何跨会话的长期记忆，用户偏好每次都要重讲。
- 失忆修复（`chat.py`）：新增 `_dangling_tool_calls` 与 `_closed_history`——所有终态轮次都保留在历史中，未执行的调用以结构化 `{"ok": false, "error": {"type": "NotExecuted", ...}}` 工具消息闭合，保证 OpenAI 兼容协议"每个 assistant tool_calls 后必须跟对应 tool 应答"的序列合法性。失败轮次若 planner 尚未消费真实观察，则用真实 observation 闭合（保留错误原文），其余按 `NOT_EXECUTED_NOTES` 写明原因（拒绝/取消/超时/未授权写入/步数超限/失败）。仅当模型产生过 assistant 消息才提交历史：模型尚未输出即被取消的轮次不落历史（保留既有 `test_cancel_does_not_commit_partial_history` 语义）。
- 长期记忆（新增 `memory.py`）：`MemoryStore` 无状态读写 `workspace/memory.json`（损坏/缺失降级为空），`add`（去空白、非空、≤500 字符、精确去重返回 `duplicate`、上限 200 条超限淘汰最旧、`.tmp`+`replace` 原子保存）、`remove`（1 基、类型与范围校验）、`clear`、`render`（最新优先占用约 6000 字符预算，截断标注）；`execute_memory_tool` 无 store 时抛 `ValueError`；`memory_block` 生成注入文本。新增 `remember` 工具（`catalog.py`，`writes=False`、非宿主、参数仅 `text`），在 `ArcPyRuntime`/`InProcessRuntime`/`MockRuntime` 三条链路均于进程内直接处理（不启动 ArcPy worker），`runtime.py` 与 `tools.py` 统一把 `(ValueError, OSError)` 收敛为结构化错误。
- 会话恢复与记忆管理（`interfaces.py`）：新增模块级 `resolve_session_id`（完整 32 位十六进制直通；短前缀唯一匹配；空/无匹配/歧义报错）。终端新增 `/chats`（列出 `编号 | 更新时间 | 标题 | 用量`）、`/resume 编号`、`/memory`（编号列出全部记忆）、`/forget 序号`；帮助行与首行提示同步更新。
- 接线：`create_session`（CLI chat 与面板 RPC 共用）构造共享 `MemoryStore(workspace/memory.json)` 注入 `ArcPyRuntime` 与 `ChatSession`；`cli.py run` 的 `MockRuntime`/`ArcPyRuntime` 同样接入；`ChatSession.__init__` 新增 `memory` 参数（默认 `workspace/memory.json`），首条消息（无 prior history）注入 `memory_block`，其后轮次不重复注入，`reset()` 后重新注入；`arcgis/ArcGISAgent.pyt` 经 `runtime(config)` 助手接入 `InProcessRuntime`。提示词 `prompts/system.md` 新增"## 长期记忆"一节（自动注入、`remember` 一条一个稳定事实、"绝不把密钥、凭据或一次性任务细节写入记忆"）。
- 验证：新增 `tests/test_memory.py` 27 项（MemoryStore 增删去重上限与坏文件降级、render 预算截断、`remember` schema 契约与三条运行时链路含"ArcPyRuntime 用不存在的 python 也不启动 worker"、历史修复的 denied/blocked/failed/unknown-tool/多调用批次/completed 形态、记忆注入首轮命中后续不重复与 reset 重注入、模型经工具写入记忆、`resolve_session_id` 四种分支）；`test_system_prompt.py` 新增 3 个规则标记（长期记忆 / remember / 绝不把密钥）；`test_approvals.py` 工具总数契约 61→62；`test_chat.py` 的写入未授权用例改为"闭合并保留历史"新语义；`test_discovery.py` 的 InProcessRuntime 桩补 `memory` 形参。`PYTHONPATH=src python -m unittest discover -s tests` 全量 **287 项通过**（原 260 + 新增 27）。
- 限制：本轮全部为离线验证，未消耗 API 用量做真实模型的端到端记忆注入/`remember` 对话观察，模型是否主动、恰当使用 `remember` 取决于提示词遵循度；`ArcPyRuntime` 的 `remember` 进程内路径已用"不存在的 python.exe"证明不启动 worker，但未在真实 Pro Python 下实跑（该工具不依赖 ArcPy）；面板经 `create_session` 自动获得记忆，但面板 UI 未新增记忆/会话管理控件（仅终端命令）；历史闭合改变了"写入未授权轮次回滚"的旧行为，属有意变更。

## 2026-11-05：自主数据发现（find_datasets 工具 + data_roots 配置 + 提示词工作流）

- 问题：用户问"某要素的属性表有什么字段"时，模型直接反问数据路径。根因是 `prompts/system.md` 明文要求"需要数据路径时向用户询问"，且工具白名单没有任何按名称搜索数据的能力（`list_datasets` 也要先给工作空间路径），模型无从自主定位。
- 新增只读工具 `find_datasets`（`catalog.py` 注册、`gis_tools.py` 实现）：在 `[agent] data_roots` 配置的数据根目录内用 `arcpy.da.Walk` 递归搜索要素、表、栅格（含 `.gdb`/`.gpkg` 成员），按不区分大小写的关键词匹配数据集名或所属工作空间名，返回候选绝对路径、类型与工作空间；`root` 参数只能是配置根目录或其子目录（运行时校验，越界报错）；未配置 `data_roots` 或根目录不存在时返回带配置指引的错误；搜索限深 12 层，默认 50 项、上限 500，超限报告 `truncated`；空结果是合法结论。逐次授权模型不变，`writes=False`。
- 配置链路：`config.py` `Settings` 新增 `data_roots`（最多 16 个非空路径，相对路径以配置文件为基准解析，类型不合法在加载时报错）；`ArcPyRuntime` 把 `data_roots` 写入 worker 的 `request.json`，`worker.py` 透传给 `tools.execute`，`InProcessRuntime` 直接传参；`cli.py run`、`interfaces.py create_session`（CLI chat 与面板 RPC 共用）和 `arcgis/ArcGISAgent.pyt` 三个入口全部接线；`agent.example.toml` 增加注释示例。搜索范围严格限定在配置根内，不引入任意路径遍历。
- 提示词（`prompts/system.md`）：红线改为"数据位置不明时先自主查找，不要一上来向用户索要路径"；标准工作流插入第 2 步"自主定位数据"（Pro 面板先 `map_context` 读图层数据源，否则 `find_datasets` 关键词搜索；唯一候选直接探查，多个候选让用户选，搜空如实说明后再询问）；Pro 面板一节把 `map_context` 从"可先用"改为"先用、优先由此定位"。`hints.py` 的 error 000732 提示补充 `find_datasets` 自救路径。
- 验证：新增 `tests/test_discovery.py` 20 项（假 ArcPy 上的关键词/工作空间匹配、limit 截断、root 越界与子目录收窄、未配置与根目录缺失报错、限深、坏文件降级；schema 契约；`Settings.load` 的 data_roots 解析与拒绝；worker 请求载荷、worker 透传、InProcessRuntime 转发三条接线）；`test_system_prompt.py` 新增 3 个规则标记；`test_approvals.py` 工具总数契约 60→61。`python -m unittest discover -s tests` 全量 **260 项通过**（原 240 + 新增 20）。真实 ArcPy 冒烟（Pro Python，只读，`workspace/smoke` 历史合成数据）：无关键词搜索正确枚举 `.gdb` 内部成员（`input.gdb` 的 boundary/points FeatureClass）、独立 `source.tif` 栅格与各 `results.gdb` 成果并报告 `truncated`；关键词无匹配返回合法空结果；`root=C:\Windows`（配置根之外）被拒绝并返回中文错误。本机 `config/local.toml` 已加 `data_roots = ["../workspace"]`（该文件不入库）。
- 限制：`.gdb` 成员类型经 `Describe` 逐个获取，超大根目录下可能偏慢，靠限深与 limit 兜底；模型是否总是先搜索再询问取决于提示词遵循度，无法在离线测试中验证，需实际对话观察；`MockRuntime` 对该工具仍返回通用模拟结果；本次冒烟使用历史 fixture 数据，未新建合成数据。

## 2026-11-05：Pro 面板 Markdown 渲染补全

- 问题复核：面板此前只在 `CompleteAssistant` 完成时把回复交给 `MessageRenderer.Create`，而旧渲染器仅识别围栏代码、`#{1,4}` 标题、`-`/`*` 项目符号、`> ` 引用与行内 `**粗体**`/`` `代码` ``。GIS 回复最常见的**表格、有序列表、链接、斜体、`---` 分隔线**都不在识别范围，原样落到段落分支，用户看到的仍是 `| ... |`、`1.`、`[文本](url)` 等 Markdown 源码——即“面板也是原生输出”的真实原因（流式生成期间 `liveAnswer` 同样是原始文本，完成后才替换）。
- 重写 `addin/ArcGISAgent/MessageRenderer.cs`：补齐 H1–H6 标题（H1/H2 加下划线与强调色）、有序/无序列表（按缩进层级悬挂）、表格、链接（渲染为“强调色文本 + 灰色 URL”，不做超链接导航以保持只读）、斜体与 `__粗体__`、`---` 分隔线；行内解析统一到一个 `AddInline`，围栏代码块带语言标签。
- 表格实现要点：用**单个共享 `Grid` + `Auto` 列**保证跨行列对齐；此前用 `Star` 列在 `FlowDocument` 的 `BlockUIContainer` 内会因可用宽度为无限而塌缩成一列一个字，改为 `Auto` 后按内容自然排布，表头加粗强调色、单元格细线分隔、左对齐不拉伸。CJK 全角字符在 WPF 下按真实字形宽度排版，无需额外宽度补偿。
- 新增验证入口：`addin/Preview/Program.cs` 增加 `--capture-markdown` 模式，用一段含标题/表格/有序列表/嵌套项目符号/引用/围栏代码/链接/分隔线的样例调用 `MessageRenderer.Create` 并离屏渲染为 PNG，纳入既有界面验证工具集（仅 Preview 工程，不进入随 Pro 发布的 addin）。
- 验证：`dotnet build addin/Preview -c Release` **0 警告 0 错误**；`--capture-markdown` 渲染成功，人工核对表格列对齐、列表缩进、引用与代码块、链接与分隔线均正确显示（`workspace/md-preview.png`，`workspace/` 已被 `.gitignore` 忽略，不入库）。终端渲染改动不受影响，`python -m unittest discover -s tests` 仍 240 项通过。
- 限制：流式生成期间仍显示原始文本，完成后才切换为渲染视图（未做逐 token 增量渲染）；表格单元格不支持换行内多段与合并单元格；链接为纯展示不可点击；未覆盖图片、删除线、任务列表等语法。真实 Pro 面板内的显示效果需在 ArcGIS Pro 运行时目视确认，本次以独立 Preview 渲染为准。

## 2026-11-05：终端 chat 回复改为 Markdown 渲染

- 问题：`agent chat` 终端交互此前把模型的 `content_delta` 原样 `print` 到 stdout，用户看到的是 `#`、`**`、反引号、`|` 表格等未渲染的 Markdown 源码；而 Pro 面板（`addin/ArcGISAgent/MessageRenderer.cs`）早已渲染，只有终端这条路缺失。
- 新增 `src/arcgis_agent/mdrender.py`：零第三方依赖的 Markdown → 终端渲染器（仓库规则禁止向 Esri 基础 Python 环境安装包，故不引入 `rich`）。支持标题（H1/H2 带下划线）、粗体/斜体、行内代码、围栏代码块（带语言标签与边框）、有序/无序列表（含缩进嵌套）、引用块、分隔线、链接（渲染为“文本 (URL)”）、以及表格。
- 颜色/降级：TTY 且未设置 `NO_COLOR`/`TERM=dumb`/`ARCGIS_AGENT_NO_COLOR` 时输出 ANSI 彩色；非 TTY（重定向、离线测试）自动降级为去除 Markdown 语法的纯文本，表格用 ASCII `+|-`，不产生 ANSI。CJK 字符按 `unicodedata.east_asian_width` 计双宽，表格与对齐不再错位。Windows 传统控制台尽力启用 VT（`os.system("")`）。
- `src/arcgis_agent/interfaces.py` `interactive()`：`content_delta` 改为缓冲并在单行内滚动显示 `[生成中… N 字]` 进度（不再回显原始 Markdown），回合结束后统一渲染 `result["reply"]`；stdout 重配置为 UTF-8 + `errors="replace"`，避免 GBK 控制台遇到 `²`、`━` 等字符崩溃。推理流（`[模型推理]`）与工具事件输出维持原样，本次不改。
- 验证：新增 `tests/test_mdrender.py`（11 项，覆盖标题去标记、行内语法剥离、链接、代码块保真、列表项目符号、CJK 表格等宽对齐、纯文本降级、无 TTY 时 `supports_color` 为假）；`python -m unittest discover -s tests` 全量 **240 项通过**（原 229 + 新增 11）；`py_compile` 通过。
- 限制：渲染为轻量正则实现，非完整 CommonMark——不支持图片语法、嵌套有序列表的复杂续行、跨行折断的行内标记（`**` 恰好断在换行处会原样显示）；表格不支持单元格内换行与合并。仅在真实终端交互路径验证渲染效果，未接入自动化 TTY 截图。Pro 面板渲染逻辑未改动。

## 2026-11-05：README 英文版与中英切换

- 新增 `README.en.md`：中文 README 的完整英文对应版，同样 12 个章节、265 行，含快速开始、Pro 面板用法、多供应商、60 工具分组、JSON 计划与任务目录、配置、仓库结构、229 项离线测试与 ArcPy 冒烟、安全隐私、边界与后续、贡献、MIT 许可。
- Pro 面板的界面词按 `addin/ArcGISAgent/Config.daml` 的实际 caption 书写（功能区 `GIS Agent` 为英文，按钮 `打开 Agent`、组名 `智能助手` 当前为中文），英文版保留中文原文并附英文释义，避免读者按翻译找不到按钮。
- 两份 README 顶部互加语言切换行；`docs/`、`CHANGELOG.md` 目前只有中文，英文版在相应位置注明。
- 验证：两份 README 的相对文件链接与页内锚点扫描均为 0 处失效；新增英文文件按同一规则做密钥与本机身份扫描，无凭据、无用户名；`./scripts/test.ps1` 229 项离线测试保持通过。
- 限制：本次为文档改动，未触及任何运行时代码；`docs/` 各专题文档尚未翻译。

## 2026-11-05：仓库整理与开源发布准备

- 根目录 6 份 2026-11-05 排障过程稿（`CRITICAL_FIX_COMPLETE.md`、`EMERGENCY_DIAGNOSIS.md`、`FINAL_FIX_PYTHON_PATH.md`、`NEXT_STEPS.md`、`QUICK_START.md`、`USE_CLI_INSTEAD.md`）原样移入 `docs/notes/2026-11-05-tool-hang/`，新增 `docs/notes/README.md` 说明这些是过程留痕、结论以本日志与 `docs/fix-tool-hang-issue.md` 的更正标注为准。
- `scripts/` 下 10 个一次性诊断脚本移入 `scripts/debug/`，新增 `scripts/debug/README.md` 逐个说明用途与运行方式；`docs/fix-tool-hang-issue.md` 与笔记内的脚本路径同步更新。脚本内 `parents[1]` 仓库根推导改为 `parents[2]`。
- `.gitignore` 重写并分组：显式排除 `.env*`（保留 `.env.example`）、`config/local.toml`、整个 `workspace/`（任务留痕、会话、`providers.json`）、`scripts/workspace/`、本机 `.dotnet` SDK、Addin `bin`/`obj`、`.qoder-credits/`、`.claude/settings.local.json`、IDE 与操作系统产物。
- 新增 `.env.example`（各供应商密钥变量名模板，值留空）、`.gitattributes`（Windows 脚本 CRLF、文本/二进制归类）、`SECURITY.md`（密钥存放与填写方式、数据流向、泄露处置、已知边界）、`LICENSE`（MIT）。
- `README.md` 重组为面向公开仓库的结构：新增摘要与目录表、仓库结构、安全与隐私、贡献与许可章节，补 `build-addin.ps1 -ArcGISProBin` 的真实构建要求（项目直接引用 Pro 安装目录 DLL，不经 NuGet），离线测试数按实测从 226 更正为 229。
- 验证：`./scripts/test.ps1` 系统 Python **229 项全部通过**；`python -m py_compile scripts/debug/*.py` 通过；移动后脚本仓库根推导指向实际根目录；全仓库 Markdown 相对链接扫描 0 处失效；密钥扫描确认 `sk-` 开头的真实值只出现在已被忽略的 `.env.local`，源码、文档与本日志均无凭据。
- 限制：本次只做仓库整理与本地 Git 初始化准备，未改动任何运行时行为；`workspace/` 与 `.env.local` 仍只存在于本机，公开仓库不含任务留痕与连接配置。


## 2026-11-05：面板卡顿排查与启动恢复

- `addin/ArcGISAgent/AgentView.xaml.cs`：显式指定的后端 Python 优先，路径无效时明确报错；启动握手失败或超时后清理进程，避免重用未就绪后端。保留用户环境中的代理、凭据及工作进程配置，仅移除 Pro/Python 上下文变量。
- `src/arcgis_agent/runtime.py`、`tools.py`、`hints.py`：增加启动 worker 前的进度标记，并移除未经对照实验证实的许可握手和底图因果断言；保留超时与取消保护。
- `src/arcgis_agent/runtime.py`：文件协议 worker 的标准输入改为 DEVNULL，避免与面板 RPC 后端共享控制输入；流式和非流式路径均覆盖。
- `tests/test_reliability.py`：验证挂起的 worker 尚在运行时进度已到达、可以取消，以及 RPC 输入管道保持打开时 worker 不等待或读取该管道。
- 验证：系统 Python 离线测试 229 项通过；插件 Release 构建 0 警告、0 错误。Pro 3.5.2 运行时沙箱外只读 doctor 成功。新版面板先复现 worker 进程启动后、脚本首条日志前卡住；输入管道隔离并重启面板后，真实 `environment`、`project_info`、`list_datasets`、`describe` 工具与对应对话均完成，用户确认多轮完成。工具调用不是对 GIS 业务成果的验收。
- 限制：现场复测支持管道隔离有效，但未证明所有历史卡顿都由同一原因引起；模型网络、复杂数据和 ArcGIS 操作仍可能超时。

## 2026-11-05：批次 9 面板工具调用在 ArcPy 启动阶段挂起的根因与修复

### 根因

面板对话"输出一句话后第一步工具一直卡住、无任何输出"的直接原因：worker 子进程冷启动 `import arcpy` 时（`tools.py` 顶部导入），外部 Python 须与正在运行的 ArcGIS Pro 完成许可握手；当 Pro 正忙（典型场景：`work_1.aprx` 含在线底图 World Topographic Map，弱网下持续加载 cdn.arcgis.com 瓦片）时该握手会停滞数分钟。证据：三次卡死运行的事件流 stderr 尾部都停在 `[worker] tool=... starting`（`import arcpy` 之前），连只读的 `environment` 工具同样挂起；Pro 关闭时 doctor 与探针均秒级成功。此前批次 7 修复的是另一个卡点（`isBroken` 在线探测），本卡点此前从未定位。

### 行为变更

- **worker 启动进度标记**：`tools.py` 在 `import arcpy` 前后向 stderr 输出 `[tools] importing arcpy...` 与 `[tools] arcpy ready in X.Xs`；`worker.py` 在宣告行后追加环境指纹（`ESRI_SOFTWARE_CLASS` / `PYTHONHOME` / `CONDA_PREFIX`），便于下次故障直接定位卡点。
- **worker 环境净化**：`runtime.py` 新增 `_worker_environment()`，为 worker 设置 `PYTHONIOENCODING=utf-8` 并剔除 `ESRI_SOFTWARE_CLASS`。面板后端继承 Pro 进程环境，该变量会使外部解释器按 Pro 进程内方式处理许可/GP 握手，与运行中的 Pro 交互可停滞数分钟。
- **实时 stderr 流式转发**：`ArcPyRuntime.call` 新增 `on_progress` 回调；取消/聊天路径经 `_run`（读线程逐行转发 stderr）把 worker 进度变成 `tool_progress` 事件，面板步骤行实时显示"参数与运行日志"，CLI 打印 `[worker] ...` 行。
- **启动停滞看门狗**：worker 启动后若 `stall_seconds`（默认 180）内未出现 `arcpy ready` 即终止运行，错误类型 `Stalled`，消息说明"ArcPy 启动超时无进展（通常因 ArcGIS Pro 正忙或在线服务等待）"并附 `STALL_HINT`。慢而合法的工具仍受原有 `timeout_seconds`（默认 180，`config/local.toml` 600）约束，二者互不干扰。`_run`（流式 + 看门狗，供面板/CLI 聊天）与 `_spawn`（`subprocess.run`，供 CLI `run`/doctor）保持分离，既有测试补丁不受影响。
- 面板步骤行新增实时进度文本（`运行日志`）并随 `tool_result` 归入详情。

### 影响模块

`src/arcgis_agent/tools.py`（import arcpy 计时标记）、`src/arcgis_agent/worker.py`（环境指纹）、`src/arcgis_agent/hints.py`（`STALL_HINT`）、`src/arcgis_agent/runtime.py`（环境净化、`_run` 流式与看门狗、`Stalled` 错误）、`src/arcgis_agent/agent.py`（`tool_progress` 事件）、`src/arcgis_agent/interfaces.py`（CLI 进度打印）、`addin/ArcGISAgent/AgentView.xaml.cs`（步骤行运行日志）、`tests/test_foundation.py`、`tests/test_reliability.py`。

### 验证

- 系统 Python 3.14.6：`./scripts/test.ps1` **226 项离线测试全部通过**（新增 4 项：启动停滞被看门狗截断且 stderr 随错误返回、worker 进度逐行流式转发、worker 环境剔除 Pro 上下文变量、`tool_progress` 事件契约）。
- 真实 Pro Python 3.11 / ArcGIS Pro 3.5.2（Pro 关闭时）流式探针：`[worker] tool=environment starting` → 环境指纹全为 None → `[tools] importing arcpy...` → `[tools] arcpy ready in 2.4s` → `ok: True, version 3.5.2`。
- `scripts/build-addin.ps1`：0 警告 0 错误，已重新生成 `addin/ArcGISAgent/bin/Release/net8.0-windows/ArcGISAgent.esriAddinX`。

### 已知限制

- 面板后端是常驻进程，必须重装插件并重启 Pro 才载入本批代码。
- 若 Pro 持续繁忙（在线底图反复加载），180s 看门狗会把卡住的任务变成明确的 `Stalled` 失败而不是无限等待；治本仍需 Pro 空闲或移除在线图层（见 `STALL_HINT`）。
- Pro 运行中的最终面板表现待用户重装后实测确认。

## 2026-11-05：批次 8 实时思考链与工具步骤时间线（面板 + CLI）

### 行为变更

- **后端步骤编号**：`agent.py` 的 `tool_requested` / `tool_executing` / `tool_result` 事件新增 `step` 字段（1 起），CLI 与面板无需自行计数即可对齐“正在做第几步”。事件流协议不变，旧面板后端忽略多余字段。
- **CLI 实时进度**：`interfaces.py` 聊天交互现在在 `[请求工具]`、`[等待授权]` 行显示步骤号，`[结果]` 行显示步骤号与单步耗时（如 `步骤 2 (3.4s)`）。
- **面板实时步骤时间线**：`AgentView.xaml.cs` 在答复卡片内新增实时步骤行（`StepRow`），每步显示“步骤 N · 工具名 · 状态”与单步耗时，状态随事件流转：`● 等待授权 → ✓ 已授权 · 等待执行 → … 执行中 → ✓ 完成 / ✗ 失败`，拒绝/取消/超时显示 `⊘ 已拒绝 / 已取消 / 授权超时 · 任务停止`。每步的 `Expander` 在获得参数后展开“参数与结果”，完成后可折叠查看完整参数与结果文本，失败时自动展开。授权卡片标题与状态栏同样带上步骤号。
- **面板实时推理**：`reasoning_delta` 到达时推理面板（“模型推理 · 实时生成”）自动展开并逐段追加模型推理内容；用户手动收起后不再强制展开（`reasoningCollapsed`），`ShowReasoning` 开关关闭时不追加。答复流结束后标题改为“模型推理 · 由 API 返回”。
- **历史会话回放**：恢复历史对话时，观察记录按步骤时间线渲染（状态 + 参数与结果文本），与实时会话观感一致。原 `AddDetails` 旧渲染方式已删除。
- 旧前端（未重装插件）对接新后端仅不显示步骤时间线，功能不受影响。

### 影响模块

`src/arcgis_agent/agent.py`（工具事件携带 `step`）、`src/arcgis_agent/interfaces.py`（CLI 步骤号与耗时打印）、`addin/ArcGISAgent/AgentView.xaml.cs`（StepRow 时间线、实时推理自动展开、授权卡片步骤号、历史回放）、`tests/test_foundation.py`（新增 1 项步骤号契约测试）。

### 验证

- 系统 Python 3.14.6：`./scripts/test.ps1` **222 项离线测试全部通过**（新增 1 项：`test_tool_events_carry_step_numbers` 断言 2 步计划的事件序列 `step == [1,1,1,2,2,2]`）。
- `scripts/build-addin.ps1`：`dotnet build -c Release` **0 警告 0 错误**，已生成 `addin/ArcGISAgent/bin/Release/net8.0-windows/ArcGISAgent.esriAddinX`。

### 已知限制

- 面板 UI 变更需重装插件才生效：关闭 Pro → 双击 `.esriAddinX` 重新安装 → 重启 Pro；旧面板后端进程载入旧代码时工具事件无 `step`，面板按 0/空处理，不影响任务执行。
- 推理流显示依赖所连模型 API 实际返回推理内容（DeepSeek 原生 reasoning 或兼容接口），未返回时不显示；Pro 内最终视觉效果待用户重装后确认。

## 2026-11-05：批次 7 project_info 超时根因确认与防御性诊断加固

### 行为变更

- **worker 阶段进度输出**：`worker.py` 在执行前向 stderr 打印 `[worker] tool=<name> starting`；`project_info` 在打开工程、枚举地图、逐图层记录前打印阶段标记（新增 `_progress` 辅助，失败静默不影响工具）。超时错误本就携带 worker stderr 尾部（`error.stderr`），此前 worker 全程静默使 180s 超时无法定位卡点；下次同类故障可直接看到卡在"打开工程"还是某个图层。
- **超时提示文案更新**：`TIMEOUT_HINT` 增加"地图含在线底图时弱网下打开工程也会长时间等待，可暂时切换为离线底图"与"关闭 Pro 中其他耗时操作后重试"。
- **新增 `config/local.toml`**：`[arcgis] timeout_seconds = 600`，为弱网下在线底图连接留余量（默认仍为 180）。

### 根因确认（当日现场取证）

- 同一失败工程 `E:\GIS工程文件\20261028_yza\work_1\work_1.aprx`：Pro 运行状态下用 Pro Python 只读探针分段计时——工程打开 3.1s，6 个本地图层 `isBroken` 各 ≤0.01s，`World Topographic Map`（cdn.arcgis.com 瓦片服务）按 2026-11-04 修复跳过，全流程约 4s 完成，即**当前代码对同一工程无超时**。
- 截图失败来自修复前启动的常驻后端进程（面板后端为长驻 Python 进程，源码在进程启动时载入、不热更新），或故障时段 cdn.arcgis.com 完全不可达导致"打开工程"阶段（Pro 初始化在线瓦片图层）本身停滞。当日实测该 CDN 可达但一个小 JSON 下载即需 3.3s，印证链路延迟波动大（2026-11-04 分段诊断曾测得单次探测 33s、故障时 >180s）。

### 影响模块

`worker.py`、`tools.py`（`project_info` 阶段进度与 `_progress`）、`hints.py`、`config/local.toml`（新增）、`tests/test_reliability.py`（新增 1 项 worker stderr 宣告测试）、docs/validation.md、README.md。

### 验证

- 系统 Python 3.14.6：`./scripts/test.ps1` **221 项离线测试全部通过**（新增 1 项）。
- 真实探针（Pro Python 3.11，Pro 运行中，同一 .aprx）：打开 3.1s、本地图层探测均 ≤0.01s、服务图层跳过、总约 4s，无残留文件、无工程改动。

### 已知限制

- 故障时段网络完全不可达时，"打开工程"阶段（Pro 初始化在线底图）仍可能超过预算；600s 超时与离线底图建议为缓解手段，工具侧无法强制 Pro 跳过在线图层初始化。
- 面板后端须重启（重开面板或 Pro）才会载入新代码与新配置。

## 2026-11-05：批次 6 内置 LLM 行为规范 md（系统提示词单一事实来源）

### 行为变更

- **系统提示词迁移为内置 md 文件**：新增 `src/arcgis_agent/prompts/system.md`，把原 `deepseek.py` 内嵌的 `SYSTEM` 字符串重组为结构化规范（绝对红线 / 标准工作流 / 工具与参数纪律 / 授权与任务状态 / 技能包 / Pro 面板与选择集 / 出图），供接入的 LLM 每轮请求阅读。`deepseek.py` 在导入时直接读取该文件作为 `SYSTEM`（fail-fast），DeepSeek、OpenAI 兼容与 Claude 三个 provider 自动共用，无行为回退。
- **内容增补**（在原 12 条规则全量保留基础上）：新增标准工作流五步（理解→探查→规划→执行→验收→汇报）、错误处理纪律（读 `error`/`hint` 修正后重试、不盲目重试同参数、如实说明未完成步骤）、拒绝/取消行为约束（不绕过被拒操作、不拆解暗中重做）、空结果是合法结论不得放宽条件重试、明确"离线模拟/工具成功/业务正确"三层区分。
- 无 token 膨胀：全文 2483 字符 / 46 行，随每次请求发送的成本与原 SYSTEM 相当。

### 影响模块

`src/arcgis_agent/prompts/system.md`（新增）、`deepseek.py`（SYSTEM 改为从 md 加载）、`tests/test_system_prompt.py`（新增 4 项：文件逐字加载、12 个关键规则标记齐全、无草稿残留标记、长度上限 8000 字符）、docs/architecture.md、CHANGELOG.md。

### 验证

- 系统 Python 3.14.6：`./scripts/test.ps1` **220 项离线测试全部通过**（新增 4 项系统提示词契约测试）。
- 原 `SYSTEM` 的 12 条规则逐条比对迁移，无遗漏；测试断言关键标记（执行诚实、逐次授权、路径纪律、0.0005 度值、verify_dataset、技能流程、注入防护、密钥防护、selection_token、出图两步）全部在位。
- 未验证项：真实模型 API 下新提示词的端到端行为质量（会产生用量，未执行）。

### 已知限制

- 规范文件随请求整体发送，用户自行大幅扩写时需自行留意 token 成本（测试上限 8000 字符）。
- 提示词内容变更不走 schema 校验，仅由契约测试守护关键标记。

## 2026-11-05：批次 5 声明式技能系统（7 个内置技能包，白名单 60 个工具）

### 行为变更

- **技能系统**：新增 `src/arcgis_agent/skills.py` 与 `src/arcgis_agent/skills/*.json`。技能包是声明式多步方法模板（工具名 + 参数模板 + 占位符 + 注意事项），供模型在复杂任务前加载参考；**不是执行器**——模型读取模板后仍逐个发起普通工具调用，每步照常单独授权。不引入 eval/exec/shell，符合仓库安全约束。
- **内置 7 个技能包**：`hydrology_watershed`（填洼→流向→汇流累积）、`suitability_site_selection`（筛选→缓冲→擦除→验收）、`data_quality_check`（结构→预览→断言→统计）、`interpolation_surface_map`（插值→等高线→阴影→出图）、`coordinate_unify_overlay`（核对→投影→叠合→验收）、`csv_points_to_density_map`（表格转点→核密度→出图）、`attribute_edit_delivery`（副本加字段→表达式计算→验收→CSV 交付）。
- **新工具** `list_skills` / `read_skill`（均只读、非宿主，工具计数 58→60）：列出技能清单、读取完整模板。占位符仅允许 `{{params.<名>}}` 与 `{{steps.<output_name>.<字段>}}`（只能引用之前步骤的真实输出名）。
- **加载时严格校验**（fail fast）：步骤工具必须存在于注册表且非宿主工具；参数键/必填项对照工具 schema；字面量字符串核对 `enum` 与 `pattern`；占位符语法与引用顺序校验；顶层键白名单；损坏 JSON 或非法技能文件报错并指明路径。
- **离线快速路径**：技能工具在 `ArcPyRuntime.call` 中于进程内直接处理，不再为每次读取启动约 10 秒的 ArcPy worker 子进程；`tools.execute` 在 `import arcpy` 之前拦截，`InProcessRuntime`/`MockRuntime` 同样支持（Mock 返回真实技能数据而非模拟标记）。未知技能名在 `ArcPyRuntime` 返回结构化错误供模型自纠。
- **用户自定义技能目录**：`config/local.toml` 的 `[agent] skills_dir`；用户技能按同名覆盖内置（清单标注 `source`），目录内重名拒绝。
- **CLI**：新增 `agent.ps1 skills` 命令输出技能清单 JSON。
- **系统提示**：复杂多步任务先 `list_skills` → `read_skill` → 替换占位符逐个执行，不得声称技能本身已执行任何步骤。

### 影响模块

`src/arcgis_agent/skills.py`（新增）、`src/arcgis_agent/skills/*.json`（7 个新增）、`catalog.py`（2 个新工具）、`tools.py`（arcpy 导入前拦截）、`runtime.py`（三个运行时的技能路径与 `skills_dir` 参数）、`config.py`（`Settings.skills_dir`）、`cli.py`（`skills` 命令与运行时接线）、`interfaces.py`（聊天运行时接线）、`arcgis/ArcGISAgent.pyt`（工具箱接线 skills_dir）、`deepseek.py`（系统提示）、`tests/test_skills.py`（新增 26 项）、`tests/test_approvals.py`（计数 58→60）、`tests/test_foundation.py`（skills_dir 配置解析）、README、docs/skills.md（新增）、docs/tools.md、docs/validation.md、config/agent.example.toml。

### 验证

- 系统 Python 3.14.6：**216 项离线测试全部通过**（新增 `tests/test_skills.py` 26 项技能契约测试 + `test_foundation.py` 1 项 skills_dir 配置解析），覆盖内置包全量加载校验、占位符语法/引用顺序、枚举与命名模式漂移拒绝、宿主工具拒绝、用户目录覆盖与重名、损坏文件 fail fast、Mock/InProcess/ArcPy 三种运行时的技能读取。
- CLI 实测：`agent.ps1 skills` 返回 7 个技能；`agent.ps1 tools` 返回 60 个工具；`ArcPyRuntime(Path("nonexistent-python.exe"))` 能成功读取技能并对未知技能名返回结构化错误，证实未启动 worker。
- 技能包本身不执行 GIS 操作，无需真实 ArcPy 冒烟；各步骤引用的工具签名已在批次 2/4 的真实冒烟中验证。

### 已知限制

- 技能模板中的步骤参数是示例性默认值（如 `power: 2`、`data_type: FLOAT`），模型须按实际数据调整；加载校验不核对数值语义。
- 占位符替换由模型完成，运行时不强制"发起调用时不得残留 `{{…}}`"；残留占位符会被各工具的路径存在性检查拒绝。
- `skills_dir` 已接入 CLI、聊天/Pro 面板后台会话与 Python 工具箱（三者均从 `Settings`/`config` 读取）；未配置时统一退回内置技能。
- 静态 JSON 计划不解析占位符，技能只服务自然语言聊天与面板流程。

## 2026-11-05：批次 4 查询扩展、结构化验收、字段计算、导出与栅格水文（15 个新工具，白名单 58 个）

### 行为变更

- **复合属性筛选**：`filter` 从单条件扩展为 `{"all": [...]}` / `{"any": [...]}`（每个最多 8 叶子，不嵌套），并新增 `IN / NOT_IN`（`values` 列表，≤100 项，禁 null）与文本专用 `CONTAINS / NOT_CONTAINS / STARTS_WITH`（LIKE 转译并转义引号）。`read_rows`、`select_features`、`set_field_value`、`calculate_field`、`export_table_csv` 全部受益。
- **空间选择与邻近**：新增 `select_by_location`（`SelectLayerByLocation` + 临时要素图层导出，13 个本机实测 overlap type；WITHIN_A_DISTANCE 类强制 `search_distance`）、`near_analysis`（副本上计算 NEAR_FID/NEAR_DIST，返回 `matched_count`，支持 GEODESIC）、`create_thiessen_polygons`（点→泰森多边形，ONLY_FID/ALL）。
- **结构化验收工具** `verify_dataset`（只读）：数量区间、字段存在、非空、唯一性、wkid、范围包含逐项断言，返回 `checks` 明细与整体 `passed`，失败明确列 `failures`。这是让 agent 自己执行"业务级核验"而不是只看工具成功与否的关键补口。
- **受限字段计算** `calculate_field`：副本上按 `!字段! * 2 + 1` 形式的算术表达式更新数值字段。AST 节点白名单在编译期拒绝函数调用/下标/字符串/逻辑运算；引用字段必须为数值字段，行值空或非数值直接失败；目标字段禁自引用。不引入 eval/exec，符合仓库安全约束。
- **导出**：`export_table_csv`（UTF-8 BOM CSV，支持字段选择与复合筛选）、`export_features`（GeoJSON 转经 `FeaturesToJSON` 转 WGS84 / Shapefile）。另新增 `define_projection`（副本上定义坐标系）。
- **栅格与水文**：`reclassify`（RemapRange 区间映射，可选 NoData 重映射）、`hillshade`、`resample`、`project_raster`（栅格投影，沿用 project 工具的地理变换显式选择守卫）、水文链 `fill` → `flow_direction` → `flow_accumulation`（D8/MFD/DINF）。
- **失败提示**：新增字段不存在（000728）、除零、schema lock、栅格属性表缺失（21048）四类 hint；许可证提示补充 Near/泰森的许可级别要求。

### 影响模块

`src/arcgis_agent/catalog.py`（15 个新工具 schema、复合 FILTER、表达式白名单校验、授权预览）、`gis_tools.py`（复合 where_clause、verify_dataset、calculate_field 求值器、CSV/GeoJSON/Shapefile 导出、select_by_location/near/泰森/define_projection 实现）、`analysis_tools.py`（7 个栅格水文工具）、`hints.py`、`deepseek.py`（系统提示：复合筛选用法与 verify_dataset 验收纪律）、`tests/test_advanced_tools.py`（新增）、`tests/test_approvals.py`（工具计数 43→58）、`scripts/smoke_batch4.py`（新增真实冒烟）、README、docs/tools.md、docs/validation.md。

### 验证

- 系统 Python 3.14.6：**189 项离线测试全部通过**（新增 21 项），`./scripts/test.ps1` 同批通过。
- Pro Python 3.11.11 / ArcGIS Pro 3.5.2 真实冒烟 `scripts/smoke_batch4.py`：**32 项检查全部通过**。所有新工具的 ArcPy 签名与枚举先在本机 3.5.2 探针实测（含确认 `SHARES_A_LINE_SEGMENT_WITH` 不在本机取值表内故未开放），再编码实现。
- 安全不变量复验：源数据几何与属性前后未变；verify_dataset 只读；表达式的 12 类恶意/越界构造全部被离线测试拒绝。

### 已知限制

- `export_features` 的 Shapefile 分支中，超过 10 字符的字段名会被 ArcGIS 静默截断，工具暂未检测该截断。
- `resample`/`project_raster` 的 `MAJORITY` 重采样未真实实测。
- `select_by_location` 的 13 个 overlap type 中 CLEMENTINI 与 3D 变体未开放（3D 关系需要 Z 值数据，CLEMENTINI 与常规版本在合成数据上不可区分）。
- 复合筛选的 all/any 不支持嵌套；需要更复杂逻辑时须分解为多次调用。
- C# Pro 面板对新工具的授权卡片展示只有离线契约覆盖，未做面板端真实点验。

## 2026-11-04：批次 3 审计修复与真实运行加固（离线 168 项 / 真实 29 项）

### 行为变更

- `build_layout` 返回值新增 `spatial_reference`（图层坐标系名与 wkid）与 `extent_source`（`requested` / `data_extent`），把出图范围的单位显式交给调用方。
- 图层落到地图之后新增一致性核验：`addDataFromPath` 之后比较地图坐标系与图层坐标系，不一致即报错且不残留工程。本机 3.5.2 实测确认 Pro 会让空地图采纳第一个图层的坐标系（`Blank.aprx` 自带地图为 3857，加入 4326 图层后变为 4326），这一未文档化行为现在变成显式守卫而不是隐式依赖。
- 新增地理坐标系单位守卫：图层坐标系为 Geographic 而 `extent` 给出经度 ±180／纬度 ±90 之外的数值时直接拒绝。根因是 `camera.setExtent()` 按地图自身单位读数，米制数值在经纬度地图上只会导出一张空白图且不报错。
- 图层无法加入地图（`addDataFromPath` 返回 None）时报出数据路径，不再静默生成空图幅。
- 坐标系相关的三条拒绝提示按数据类型分流：`project` 工具实际包装 `management.Project`，只接受要素类，因此栅格图层改为提示"先在 ArcGIS Pro 中用 Project Raster"，不再把用户指向用不了的工具。

### 写入与清理

- 建图与导出改为"暂存文件 + 一次 `os.replace` 原子发布"：进程被超时终止后，同名输出不会被半截文件永久阻塞；发布失败时暂存与目标一并删除。
- `_target` 只计算路径与检查覆盖，不再创建目录；目录只在真要写文件时建立。因此被拒的调用不再残留空的 `cartography` 目录或半成品 `.aprx`。
- 导出后核验 ArcGIS 的实际落盘路径与声明路径一致，不一致判定失败并清理，避免把写到别处的成果当作交付。
- `elements` 传空数组得到干净图面（只有图幅与图名），图例侧栏 `SIDE_BAND` 只在包含 `LEGEND` 时预留。

### 影响模块

`src/arcgis_agent/cartography.py`、`tools.py`（`_layout_record`）、`deepseek.py`（系统提示补充 extent 单位规则）、`tests/test_cartography.py`、`tests/test_foundation.py`、`scripts/smoke_cartography.py`、README、docs/tools.md、docs/validation.md。

### 验证

- 系统 Python 3.14 与 Pro Python 3.11.11：**168 项离线测试全部通过**（本轮新增 14 项：原子发布、目录与时序清理、坐标系单位与一致性拒绝、`addDataFromPath` 失败、空 `elements`、图例侧栏、导出声明路径核验、枚举与实现常量的漂移守卫、`_layout_record` 契约）。`./scripts/test.ps1` 同批通过。
- Pro Python 真实冒烟 `scripts/smoke_cartography.py`：**29 项检查全部通过**，新增 `map_adopts_geographic_reference`、`projected_layer_build`、`projected_sheet_renders_data`、`geographic_sheet_renders_data`、`raster_layer_sheet`、`empty_elements_build_a_clean_sheet`、`jpeg_and_svg_export`、`only_published_files_remain`、`metre_box_refused_for_geographic_layer`、`metre_box_accepted_for_projected_layer`、`no_project_for_refused_extent`。
- 图面内容证明改为"非白像素占比"：本机实测渲染成功的三张 PNG 为 `0.36 / 0.48 / 0.37`，`raster.png` 为 `0.37`，阈值 0.05；此前用亮度 <210 判据会被 Pro 随机默认填色的浅色填充误判，故阈值取 <250。
- 冒烟脚本现在记录所有被拒调用的实际错误文本（`refusals`），失败可直接定位是消息变了还是执行失败。
- 只读性复验：导出前后 `.aprx` SHA256 一致；`only_published_files_remain` 确认任务目录只剩已发布成果，无暂存残留；源数据要素数未变。

### 已知限制

- 单位守卫只覆盖"地理坐标系 + 明显越界数值"这一侧；投影图层被给了经纬度数值仍会照原样设相机，工具无法判断这类数值的意图。
- 图面内容证明是像素占比，不是业务级验收（版面、注记密度、颜色是否合适）；图层符号使用 Pro 默认随机填色，暂无符号化与标注工具。
- 真实冒烟每项工具调用都要冷启动一个 ArcPy 子进程，180 秒超时在整机高负载时会误报失败（本轮出现过一次三项同时红的负载抖动，代码未变、独占复跑通过）。

## 2026-11-04：批次 3 制图出图与只读导出（2 个新工具，白名单 43 个）

### 新增工具

- `build_layout`：复制本机 ArcGIS Pro 安装自带的空白模板（`Resources/ArcToolBox/Services/routingservices/data/Blank.aprx`），在任务目录 `outputs/cartography/<output_name>.aprx` 新建一个独立出图工程，加入 1–12 个有几何的图层、图幅、图名与图廓元素，然后保存**这个新建工程**。可选 `page_size`（A4/A3/A2/A1/LETTER/TABLOID，默认 A4）、`orientation`（默认 LANDSCAPE）、`elements`（`LEGEND`/`NORTH_ARROW`/`SCALE_BAR`/`DUAL_SCALE_BAR`，默认前三项，可传空数组）、`extent`（省略时取图层范围并集）。
- `export_layout`：只读打开 `.aprx`（或工具箱内的 `CURRENT`），把布局导出为 PNG/JPEG/PDF/TIFF/SVG，`dpi` 72–600（默认 300），写入同目录。全程不调用 `save()`，返回 `project_modified: false`。
- 两者均 `writes=True`，授权预览分别显示 `cartography/<name>.aprx` 与 `cartography/<name>.<ext>`；输出名沿用 ASCII 规则并拒绝覆盖已有成果。
- `project_info` 新增 `layouts` 清单（名称、页面宽高与单位、元素数量），用于在只读场景下选定 `export_layout` 的布局名。
- 系统提示补充两步出图流程：`export_layout` 的 `project` 与 `layout` 必须来自 `project_info` 或 `build_layout` 的返回。

### 契约与守卫

- 枚举与调用形状按本机 3.5.2 实测定型：`createLayout(宽, 高, "MILLIMETER", name=)`、`createMapFrame(polygon, map, name=)`、`createMapSurroundElement` 的样式类名必须用下划线形式（`NORTH_ARROW`/`SCALE_BAR`/`LEGEND`，`NORTHARROW`/`SCALEBAR` 被运行时拒绝）、文本元素只能走 `getDefinition("V3")` + CIM 图形、`Layout.export(mp.CreateExportFormat(fmt, path))` 返回输出路径、`resolution` 实测生效。
- 运行时明确拒绝的能力一律不暴露：`georeferenceMapFrame`、`worldFile`、按格式设置色彩模式（PNG 无该属性，TIFF/PNG 只接受真彩色枚举）。`Map.getCamera` 不存在，比例尺通过 `MapFrame.camera` + `camera.setExtent()` 设置。
- 图层先校验再建工程：无几何的表、坐标系不一致的图层组合、`SCALE_BAR` 与 `DUAL_SCALE_BAR` 同时请求、`extent` 次序错误都在**复制模板之前**拒绝。
- 新增 `cartography.py` 承载批次 3 实现，`gis_tools.py` 只负责路由。

### 缺陷修复

- 真实冒烟首轮暴露：`build_layout` 在复制模板之后才校验图层，被拒的调用会在任务目录残留半成品 `.aprx`。先补两条失败测试复现（图层校验先于复制、坐标系混合不留半成品），再把全部可拒绝的检查前移，并用 `completed` 标记 + `finally` 清理与 `del project` 收尾。修后该检查为 `no_project_for_refused_build=true`。
- 同一轮把 `_export_layout` 的失败路径补齐：空或零字节导出一律判为失败并删除自己创建的文件；工程不存在、布局名未知、多布局未指定各自返回中文可行动提示。

### 影响模块

`src/arcgis_agent/cartography.py`（新）、`catalog.py`、`gis_tools.py`、`tools.py`、`deepseek.py`、`tests/test_cartography.py`（新）、`tests/test_approvals.py`、`scripts/smoke_cartography.py`（新）、README、docs/tools.md、docs/architecture.md、docs/validation.md。

### 验证

- 系统 Python 3.14 与 Pro Python 3.11.11：**154 项离线测试全部通过**（批次 3 新增 27 项，含用桩对象驱动真实 `_build_layout`/`_export_layout` 的流程与清理测试）。
- Pro Python 真实冒烟 `scripts/smoke_cartography.py`：**18 项检查全部通过**，覆盖建图、三种图廓元素、自动与显式范围、A3 纵向双比例尺、PNG/PDF/TIFF 导出、导出前后工程文件 SHA256 一致（只读证明）、`project_info` 布局清单，以及六条拒绝路径与"被拒调用不留残留"。人工数据仅写入 `workspace/smoke/cartography_<uuid>`，未打开或修改任何用户工程。
- `./scripts/test.ps1` 离线套件通过。

### 已知限制

- 出图的**业务级人眼验收**（版面美观、注记密度、比例是否合适）未做，冒烟以文件字节与元素清单为代理证据；元素版位使用固定页边距，暂不提供位置/大小参数。
- 依赖安装自带 `Blank.aprx`；缺失时工具报中文错误且不执行，其他 Pro 版本或精简安装需重新核验。
- 未在用户真实工程与真实数据上跑完整出图；工具箱内 `CURRENT` 只读导出仅有离线桩覆盖。
- Pro 内部工具箱在当前进程同步执行，仍无强制超时；C# 面板对新工具的授权卡片未做真机交互验证。

## 2026-11-04：批次 2 空间分析、叠合、几何重塑与属性连接（13 个新工具，白名单 41 个）

### 新增工具

- 表面与栅格：`interpolate_surface`（IDW/Kriging）、`kernel_density`、`polygon_to_raster`、`terrain_derivatives`（坡度/坡向）、`contour_lines`、`zonal_statistics`、`extract_by_mask`、`sample_raster_at_points`。
- 叠合与统计：`union`、`erase`、`summarize_statistics`（含分组与多种统计）、`join_table`（属性连接到副本）。
- 几何重塑：`reshape_geometry`（`CENTROID` / `SIMPLIFY` / `MIN_BOUNDING`）。
- 栅格类工具新增 `raster_output` 标记，授权预览显示 TIFF 目标而不是 results.gdb 路径；全部标记 `writes=True`，输出仍限定在唯一任务目录且拒绝覆盖。

### 契约与守卫

- 所有枚举、参数位置与调用形状按本机 ArcGIS Pro 3.5.2 实测确定：`arcpy.sa.IDW`、`analysis.Unify`、`cartography.FeatureToPoint`、`JoinField` 的 join type、`SimplifyPolygon` 的 `Percent` 容差、`ZonalStatisticsAsTable` 的 `COUNT` 在该运行时都不存在，已按真实签名实现并在 `validate_call` 增加条件约束（IDW 配 `power`、Kriging 配 `variogram`、`SIMPLIFY` 必须线性容差、`MIN_BOUNDING` 必须几何类型）。
- 新增 `require_extension`：Spatial Analyst 类工具执行前签出扩展，失败即整次调用失败并说明许可证状态，不返回部分结果。
- 新增 `analysis_tools.py` 承载批次 2 实现；分析执行统一重置 `extent / outputCoordinateSystem / cellSize / mask / snapRaster / overwriteOutput / addOutputsToMap`，不继承 Pro 会话设置。栅格参数只接受纯数字并要求按输入数据单位填写（地理坐标系须用度值），避免此前实测到的 ERROR 999999。
- 结果观察统一为 `_finish`：栅格返回宽高、波段、像元类型、像元大小、坐标系与范围；要素与表返回数量、字段清单和几何类型，并附 `messages` 尾部；一律带 `source_modified: false`。
- `uniqueItems` 校验改为按 JSON 规范化比较，此前对字典数组（如 `stats`）会直接抛 `TypeError`。
- `hints.py` 增加 `ERROR 010819`（路径含空格）、`ERROR 999999`（像元过大或栅格统计缺失）、`ERROR 000462`（许可证级别不足）三条恢复建议。

### 影响模块

`src/arcgis_agent/catalog.py`、`analysis_tools.py`（新）、`gis_tools.py`、`hints.py`、`tests/test_analysis_tools.py`（新）、`scripts/smoke_arcpy.py`、README、docs/tools.md、docs/architecture.md、docs/validation.md。

### 验证

- 系统 Python 3.14 离线测试 118 项通过（批次 2 契约与扩展签出 19 项）。
- Pro Python 真实冒烟 `scripts/smoke_arcpy.py` **24 项检查全部通过**，批次 2 的 13 个工具全部经独立子进程与逐次授权实跑；含拒绝路径（文本分区字段、缺失连接列、非面叠合）与源数据未变核验。详见 [验证记录](docs/validation.md)。

### 已知限制

- `MIN_BOUNDING` 未暴露分组参数（`group_option` 默认 `NONE`，一次调用只返回一个整体外包几何）。
- `join_table` 无“保留全部/仅匹配”语义（本机 `JoinField` 没有该参数），始终返回副本全部记录。
- 未实现制图与地图导出（批次 3）、拓扑与网络分析、地统计图层；分析结果的业务正确性仍需人工核验。

## 2026-11-04：ArcPy 子进程协议与会话恢复可靠性修复

### 行为变更

- 被终止的 worker 不再丢弃证据：超时与取消都会保留并返回 worker 的 stderr 尾部，提示优先匹配具体故障模式（如 ERROR 000732），否则回落到超时排查建议。取消返回 `error.type = "Cancelled"`，不再是外抛 `InterruptedError` 或被误报为 `WorkerFailure`。
- 若 worker 已完整写出 `result.json` 之后才在收尾阶段被终止（ArcGIS 后台进程占用是常见原因），现在返回该真实结果并标记 `worker_terminated: true`，而不是报超时——此前一次成功会被误判为失败。不完整的写出仍按不存在处理。
- 协议临时目录改为显式清理并忽略占用错误：Windows 上 ArcPy 残留句柄导致的 `PermissionError` 不再吞掉已成功的工具结果（清理失败时目录保留在临时目录，不影响成果）。
- `worker.py` 的结果序列化移入受保护路径：无法序列化的返回值现在写成 `ok=false` 的 `SerializationError`（进程退出码 1），不再退化成父进程的 `WorkerFailure` 并绕过恢复提示。
- `ChatSession.load` 校验会话文件的历史形状与用量字段：外来的 `history`/`turns`/`total_usage` 返回明确的“会话文件的历史记录格式无效”错误，不再在后续对话中以 `AttributeError`/`KeyError` 崩溃。
- 模型流式响应增加总时长上限（默认 900 秒，OpenAI 兼容与 Claude 两条协议路径都覆盖）：`urlopen` 的超时只覆盖单次阻塞读取，慢速滴流的响应此前可能无限期挂住；超限时报错并明确“未执行不完整的工具”。
- 系统提示补充栅格参数单位约定，降低模型对地理坐标系填写米值导致 ERROR 999999 的概率。

### 影响模块

`src/arcgis_agent/runtime.py`、`worker.py`、`chat.py`、`deepseek.py`、`providers.py`、`tests/test_reliability.py`（新）、`tests/test_providers.py`、docs/validation.md。

### 验证

- 系统 Python 3.14 离线测试 127 项通过；新增 8 项覆盖上述 worker 协议与会话恢复分支（含真实子进程打桩：写出后挂起、无结果挂起、立即成功、目录被占用、序列化失败）。
- Pro Python 真实冒烟 `scripts/smoke_arcpy.py` 24 项检查在该改动之后复跑通过。

### 已知限制

- Pro 工具箱在当前进程内同步执行，仍没有强制超时；面板宿主请求的 C# 侧超时与任务代次处理本轮未改动，需要重新编译插件并在真实 Pro 会话中核验。
- 步数上限判定仍需向规划器多问一次，因为自然语言适配器在这一次返回最终文本；该调用不是纯粹浪费，但会在预算耗尽时多产生一次模型请求。

## 2026-11-04：批次 1 数据接入工具与失败守卫（参考 GIS-agent-main 能力移植）

### 新增工具

- `import_table_points`：CSV/Excel 表转点要素类。自动识别经纬度列（lat/latitude/y/纬度 等候选）或显式 `lat_field/lon_field`；坏坐标行跳过并计数；`wkid` 默认 4326；Excel 表需 `Sheet1$` 工作表后缀。成果为任务目录 results.gdb 新点要素类，源表不变。
- `convert_dataset`：GPKG 等矢量数据复制为 results.gdb 新要素类；KML/KMZ 经 `KMLToLayer` 解包到任务目录 `kml_<output_name>/<output_name>.gdb` 并返回成员清单，由 `copy_features` 选入 results.gdb。授权预览对 KML 分支显示解包目录而非 results.gdb 路径。

### 可靠性增强

- 假 `.gdb` 守卫：路径以 `.gdb` 结尾但 `Describe` 非 Workspace 时，`describe` 与全部走 `_exists` 的工具直接报“不是真实地理数据库”，不再等 ArcPy 抛难懂异常。
- 新增 `hints.py` 故障→恢复建议表（假地理数据库、ERROR 000732、ERROR 000800、缺地理变换）；worker 异常与 runtime WorkerFailure 在错误 payload 附加 `hint`；Timeout 附加网络路径/数据范围排查建议。
- 覆盖策略维持“拒绝覆盖”，不移植参考仓库的覆盖前自动备份。

### 影响模块

`src/arcgis_agent/catalog.py`（2 个新工具声明与授权预览）、`gis_tools.py`（实现与守卫）、`tools.py`（describe 守卫）、`worker.py` / `runtime.py`（hint 接线）、`hints.py`（新）、`scripts/smoke_arcpy.py`、`tests/test_ingestion.py`（新）、README、docs/tools.md。

### 验证

- 系统 Python 3.14 离线测试 99 项通过（新增 6 项：参数校验、授权预览输出、hint 匹配、WorkerFailure/Timeout 携带 hint）。
- Pro Python 3.5.2 真实冒烟 4 项检查全过：缓冲区面积回归、import_table_points（2 条入库 / 1 条坏坐标跳过 / 自动识别 latitude / WKID 4326）、假 .gdb 守卫报错、GPKG 转换计数一致；合成数据仅位于 `workspace/smoke/<uuid>`。

### 已知限制

- KML/KMZ 分支未经真实数据冒烟（合成 KML 不现实），仅代码路径与授权预览有离线覆盖。
- Excel 多工作表需模型/用户显式给出 `Sheet1$` 后缀；未做工作表枚举。

## 2026-11-04：修复 project_info 因在线服务图层超时

### 行为变更

- `project_info` 读取图层时不再对**在线服务图层**（`dataSource` 为 HTTP(S) URL）和**分组图层**调用 `isBroken`。该属性对服务图层会发起实时联网连通探测；服务不可达时（如受限网络下的 Esri 在线底图）单次探测可停滞数十秒至数分钟，超出子进程 180 秒预算被终止，表现为 `ArcPy exceeded 180s; worker terminated`。此类图层现在返回 `is_broken: null` 并附 `broken_check` 原因；本地路径图层保留真实探测。
- 影响模块：`src/arcgis_agent/tools.py`（新增 `_layer_record`）、`tests/test_foundation.py`、`docs/tools.md`。

### 验证

- 分段诊断（Pro Python 3.11，Pro 运行且目标工程打开）：失败工程 `work_1.aprx` 前 6 个本地图层 `isBroken` 各 0.0s，第 7 层 `World Topographic Map`（cdn.arcgis.com 矢量瓦片服务）单次探测 33.0s；故障发生时同一探测超过 180s。
- 修复后经生产路径 `ArcPyRuntime` 子进程 worker 复跑同一工程：4.3s 返回 ok，7 个图层齐全，服务/分组图层 `is_broken: null` 且带原因说明。
- 系统 Python 3.14 离线测试 93 项通过，含新增的 `_layer_record` 跳过联网探测断言。

### 已知限制

- 本地路径图层若指向不可达的网络共享或企业级地理数据库，`isBroken` 仍可能因连接等待变慢，仍受 180 秒超时约束。
- 跳过探测后工具不再报告在线服务图层当下是否可达，模型仅见 null 与原因说明。

## 2026-11-03：ArcGIS 工具接入与每次操作授权

### 接入 24 个结构化工具

- 新建 `src/arcgis_agent/catalog.py`，集中声明工具的类型化参数、必需项、写入属性、宿主需求、中文操作名称、副作用和预计输出。模型、CLI、参数校验和授权预览使用同一契约。
- 新建 `src/arcgis_agent/gis_tools.py`，扩展数据浏览、记录统计、属性预览、复制、裁剪、相交、合并、融合、投影、空间连接、属性筛选、字段增删改、测地几何计算、几何修复和栅格裁剪；保留原有环境、工程、描述、缓冲区工具。
- 字段和几何编辑先创建副本，数据输出限制在独立任务目录，拒绝覆盖。属性过滤使用真实字段和结构化条件，字段赋值只接受常量；没有任意 SQL、Python 或 shell 执行工具。
- `describe` 补充字段长度、可空性、必需性、可编辑性和数据范围。属性预览限制行列数、文本长度与响应总量。
- 模型工具声明改为从目录生成；所有供应商共用相同的调用与授权约束。模型依据上一步真实输出继续规划，不自行拼造已生成的路径。

### 每次工具调用都向用户询问

- 新建 `approvals.py`；Agent 在所有读取、分析和地图修改前先请求单次授权。缺少授权处理器则返回 `approval_required`，不会执行。
- CLI 的 chat / run / demo 每步显示操作、参数和输出，支持 y 允许、n 拒绝、c 取消，回车默认拒绝。Python 工具箱使用 Yes / No / Cancel 原生确认框，默认 No。
- Pro 面板新增授权卡片，支持允许本次读取或写入、拒绝、取消任务；窄幅时暂时收起输入区和设置。等待授权时明确显示工具尚未执行。
- `--allow-writes` 或面板写入复选框不能跳过逐次确认；一次写入许可不扩散到下一步。拒绝后停止当前任务的后续步骤，保留此前成果。
- 参数在授权前冻结；每次请求有独立 request_id、digest 和审计事件。RPC 处理重复、过期、错误类型的响应，10 分钟未确认则停止；取消和断线不视为同意。

### Pro 当前地图桥接

- 增加 `map_context`、`add_to_map`、`set_layer_visibility`，由 Pro SDK QueuedTask 执行。当前地图及图层使用 URI 定位，授权后才修改，不自动保存工程。
- 后台 RPC 增加授权回复与宿主结果协议，等待期间继续接收取消。面板串行发送控制消息。
- 宿主操作设置截止时间及任务代次检查，执行前再次检查取消状态，避免旧任务的排队请求迟到修改地图。
- ArcPy 子进程仍不继承 Pro 选择集或 CURRENT。地图状态读取与磁盘 GIS 数据分析有独立边界。

### 文档、样例与验证

- 更新 README、架构、供应商说明和参考来源；新建 `docs/tools.md`，列出 24 个工具、参数和限制；新增 `examples/select_features.json`。
- 新增 `tests/test_approvals.py`，并使原有测试显式使用测试授权处理器；产品没有自动同意开关。新增真实工具与真实模型人工数据验证脚本。
- 系统 Python 3.14 / Pro Python 3.11 均通过 **79 项离线测试**。
- 仅在 `workspace/smoke` 人工数据上验证 **20 个真实 ArcPy 工具**，独立检查原始数据未变、禁止覆盖、数量、投影、字段值、面积及栅格尺寸。
- DeepSeek 实际完成“检查 → 筛选 → 50 米缓冲 → 统计”，四次独立测试授权，最终 2 个缓冲区；独立核验面积各约 7833.33 m²。fixture 脚本仅允许其人工数据目录，不代替产品中的用户确认。
- Pro 插件和 WPF 预览构建均为 0 错误、0 警告；340 / 500 像素浅色、深色授权卡片验证通过。插件已注册，安装包哈希与构建包一致，重启 Pro 加载。
- **未验证范围**：三个 SDK 宿主工具尚未在实际 Pro 地图逐一交互操作，仅通过编译和 RPC 模拟；人工数据核验不等于用户业务验收。任务失败或取消不提供事务回滚。

验证记录与人工数据路径见 [docs/validation.md](docs/validation.md)，工具使用见 [docs/tools.md](docs/tools.md)。

## 2026-11-03：多供应商连接（前一批次，补记）

- 增加 DeepSeek、Qwen、Kimi、GLM、GPT、Claude、Grok 及自定义中转接口；区分 OpenAI Chat Completions 兼容协议与 Claude Messages 原生协议。
- 增加独立连接、账户凭据引用、连接切换、模型能力设置、原生内容块和用量转换；CLI 与 Pro 面板共享配置。
- 增加连接管理界面、隐藏密钥输入、模型刷新、流式文本、接口返回的推理内容、图片与文件附件、token 用量、历史恢复及浅色/深色外观。
- 当时通过 63 项离线测试、DeepSeek 真实图片/推理/只读环境验证。其他供应商以模拟协议测试为主，未使用各家真实账户逐一验证。

## 后续更新约定

每个后续功能或修复批次追加一条记录，注明日期、用户可见行为、关键模块、验证结果和未完成项；没有实测的能力明确注明。日志不记录密钥、用户私有数据内容或未经验证的成功结论。
