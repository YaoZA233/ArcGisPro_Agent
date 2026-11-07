# 基础架构与扩展约定

```text
CLI / Pro DockPane / ArcGIS Python Toolbox
             |
      Agent（有界循环、日志、写入控制）
             |
      Planner.next_action(observations)
             |
      Action(tool, arguments)
             |
      工具白名单与参数校验
             |
      单次授权（读取和写入均询问，默认不执行）
             |
      Runtime.call(tool, arguments, output_dir)
       /                  |                 \
ArcPyRuntime       InProcessRuntime       MockRuntime
独立 Pro Python     Pro 工具箱会话           标记为模拟
       \                  /
          ArcPy 结构化工具
```

## 模块边界

| 模块 | 责任 |
| --- | --- |
| `config.py` | TOML、环境变量、注册表发现、步数与连续失败预算校验；不导入 ArcPy |
| `agent.py` | Planner 协议与可选 `recovers` 能力标志、JSON 计划校验、观察循环、工具步数上限、连续失败自纠预算、任务记录 |
| `catalog.py` | 62 个工具的类型化契约、参数校验、权限和授权预览 |
| `approvals.py` | CLI / 工具箱确认、单次 RPC 请求、超时与断线处理 |
| `tools.py` / `gis_tools.py` | 延迟导入 ArcPy、允许列表中的 GIS 操作，复制后编辑 |
| `skills.py` + `skills/*.json` | 声明式多步技能包：加载即对照工具注册表校验，只被 `list_skills`/`read_skill` 只读返回给模型参考，不执行代码 |
| `prompts/system.md` | 接入 LLM 的行为规范与系统提示词（单一事实来源）：`deepseek.py` 导入时加载，共用规划器的所有供应商客户端使用；规划、证据消歧与纠错是提示词纪律，不保证模型遵循或业务成功 |
| `deepseek.py` | 流式客户端与共用 `DeepSeekPlanner`；失败观测回流、未执行批次调用 ID 闭合、请求模型重新决策 |
| `analysis_tools.py` | 批次 2 的空间分析、叠合、几何重塑与属性连接；先检查扩展许可与要素/字段类型再调用 ArcGIS |
| `cartography.py` | 批次 3 的出图与导出：复制安装自带空白模板新建任务内工程、`arcpy.mp` 建布局/图幅/图廓、CIM 加图名；导出侧只读打开、永不 `save()` |
| `symbology.py` | 预设色板驱动的符号化与标注：色板常量与兼容校验（`renderer_kind`/`validate_style_spec`）、分级/唯一值计算、CIM renderer 与 colorizer/labelClass 的原地构造；不碰工程文件、不做 IO、不决定输出路径，字段值读取经调用方注入的 arcpy 游标句柄委托；全部拒绝都发生在复制工程模板之前，被拒调用不残留半成品 `.aprx` |
| `runtime.py` | ArcPy 子进程 / Pro 当前进程 / 离线模拟的统一接口 |
| `HostBridgeRuntime` / `ProContext.cs` | 授权后将当前地图工具转交 Pro SDK 的 QueuedTask |
| `worker.py` | 结构化请求和结果文件协议；普通输出不参与协议解析 |
| `cli.py` | 命令行入口、诊断、配置装配、退出码 |
| `providers.py` | 独立连接、凭据引用、OpenAI 兼容协议、Claude Messages 与流式转换 |
| `chat.py` | 会话、来源标记、跨连接历史转换、附件和用量聚合；未完成轮次以结构化"未执行"结果闭合历史，保证 API 序列合法且下一轮可见此前尝试 |
| `memory.py` | 跨会话长期记忆存储（`workspace/memory.json`）与 `remember` 工具：进程内处理、精确去重、上限淘汰、首条消息注入；绝不写入密钥 |
| `arcgis/ArcGISAgent.pyt` | Pro 内的地理处理工具入口，允许访问 CURRENT |

## 新增工具

先在 `catalog.TOOLS` 声明类型化参数、必需项、写入属性、影响与宿主需求，再在 `validate_call` 增加业务约束，最后实现 ArcPy 或 SDK 调用。同一 schema 用于模型声明、CLI 查询、执行前校验和授权预览。创建数据或修改地图的工具必须标记 `writes=True`；数据输出由任务目录生成，关闭覆盖，栅格成果标记 `raster_output=True`，授权预览据此显示 TIFF 路径。需要扩展许可的分析工具在执行前经 `gis_tools.require_extension` 签出，签出失败直接报错。不要绕过统一入口增加任意代码或 shell 执行能力。技能包（`skills.py`）同样受此约束：它是只读方法模板，加载时对照 `catalog.TOOLS` 校验每个步骤的工具名、参数键与字面量取值，模型仍须逐个发起受授权的工具调用，绝不能成为执行任意代码或绕过授权的后门。

Agent 冻结每步参数，生成独立 request_id 和 digest；授权预览、副作用与实际执行使用同一份参数。授权事件为 `tool_requested → approval_requested → approval_decided → tool_executing → tool_result`。默认 `approval_mode=ask` 且无 `approve` 回调不调用 runtime；`approval_mode=full` 使用内置自动授权回调，但仍写入相同审计事件。每次纠正动作（含只读探查）也需要新的逐次授权；写操作还须具备调用方显式启用的全局写入能力或本次单次写入许可，二者满足其一即可。单次写入许可不会扩散到下一步，全局写入能力也不代替逐次授权。

工具返回 JSON 可序列化数据。真实 GIS 操作出错由 runtime 转换为 `ok=False` 时，Agent 记录完整失败观察，仅对可纠错规划器允许后续重新决策。拒绝、取消、授权过期、写入未授权、缺少授权处理器和执行循环内异常（包括 runtime 抛出的异常）均立即终止，不进入纠错；工具结果中的 `error.type="Cancelled"` 即使没有取消事件也终止为 `cancelled`。一次工具成功不代表整个 GIS 分析已经经过业务验收；纠错不提供事务回滚、自动删除或覆盖，部分成果可能保留，新数据输出仍限唯一任务目录。

## 模型接入

`DeepSeekPlanner` 实现 `Planner.next_action(observations) -> Action | None`，将模型工具调用映射到白名单工具；返回 `None` 表示规划结束。Agent 每一步独立验证参数、检查写入权限并限制步数。`DeepSeekClient` 使用标准库 HTTPS 请求，支持超时；`ChatSession` 按完整轮次裁剪上下文。工具失败后的模型重新决策不等于 HTTP 自动重试：模型请求异常仍终止，不自动重发未知状态的 API 请求。

`PlanPlanner.recovers=False`，静态 JSON 计划（CLI `run` / Pro 工具箱）不解释自然语言、不修订计划，保持失败即停。`DeepSeekPlanner.recovers=True`，所有共用该规划器的客户端（含已配置供应商）均可恢复；Agent 用 `getattr(planner, "recovers", False)` 读取可选能力，未声明的规划器仍失败即停。纠错只适用于实际工具调用返回的 `ok=False`，不是重放被拒操作或执行模型生成的 Python 代码。

失败且仍有预算时，`DeepSeekPlanner.next_action` 把原始观察（含 `error`、已有 `hint` / `stderr`）作为工具消息反馈；同批次 pending 调用不再执行，而以 `NotExecuted` 逐一闭合 ID、清空队列，再请求模型重新决策。即使后续调用看似独立也需重新规划。成功批次仍按原顺序逐个执行，不因本功能额外请求模型。先说明计划、只读证据消歧、失败诊断与改变动作只是提示词纪律，未引入结构化预规划阶段，不能保证模型遵循或纠错质量。

`Agent`、`Settings`、`ChatSession` 的 `max_steps` 默认统一为 16，配置接受精确整数 `1..100`。CLI / 工具箱的配置默认上限也从 8 提高为 16，显式值仍受尊重，不能把 fail-fast 不变说成所有行为零变化。`failure_budget` 默认 3，配置加载与 Agent 构造器接受精确整数 `1..20`，拒绝布尔、浮点、字符串和越界值；`Settings.load → create_session → ChatSession → Agent` 透传预算，直接构造 ChatSession 时由 `send()` 创建 Agent 才校验预算。

连续失败计数在每次工具成功后清零。预算计算的是失败次数而非额外重试次数：3 次连续失败 = 首次失败 + 至多两次未成功纠正；预算 1 则首次失败即停。成功可重置失败预算，但包含探查和纠正的总工具调用仍受 `max_steps` 约束。步数不计模型请求；上限耗尽后仍可请求一次模型以取得最终答复或判定 `step_limit`，超限工具不会执行。

`Timeout` / `Stalled` 若由 runtime 返回为普通失败观察，也受上述预算与授权约束；执行结果可能不确定，已有部分输出不会被纠错循环自动清理。`180s × failure_budget` 不是通用任务截止时间：超时配置、runtime 模式、成功后的预算清零、模型请求与授权等待均影响总耗时，Pro 进程内工具箱也没有强制超时。

Pro 插件是 WPF DockPane，使用标准输入/输出上的 JSON 行与 `arcgis_agent rpc` 子进程通信。供应商 SSE 流中的文本、返回的推理、工具事件及用量实时传到界面。RPC 主线程接收控制请求，任务线程等待单步授权；ArcPy 子进程可以被终止。`map_context`、`add_to_map`、`set_layer_visibility` 授权后由 Pro SDK 执行，按地图和图层 URI 精确定位，不自动保存工程。其余分析仍使用独立 ArcPy，不继承选择集或 CURRENT。CLI 的 `chat` 和 RPC 共享 `ChatSession`。

RPC 向面板发出 `approval_requested` 事件，面板返回 `{"type":"approval_response","request_id":"…","response":{"decision":"allow|deny|cancel","allow_writes":true}}`。写许可可省略，默认 false。broker 先登记请求再发事件，默认等待 600 秒；过期、重复、错误类型的答复返回 `control_error`。有效答复返回 `control_ack`，实际决定另写审计事件。EOF、取消或断线停止等待，不能默认同意。

宿主工具在授权后发出 `host_requested`，由面板返回 `{"type":"host_result","request_id":"…","response":{"ok":true,"data":{}}}`；失败时 ok=false 并附 error。后端等待 60 秒，面板为宿主请求设置 55 秒截止与任务代次标识；实际地图修改前再检查取消、超时和原任务有效性，防止排队操作迟到执行。send 仅在 Pro 内传 `host_tools:true`，CLI 的模型声明不含宿主工具。

附件在 `attachments.py` 中限制类型、数量、大小与图片模型能力。图片以 data URL 发送，文本以 UTF-8 内容发送，GIS 文件仅传路径。会话文件保留文本、最终答复、工具记录和推理内容，但不保存图片的 base64 数据；恢复会话后需要重新附加图片。`workspace/ui-options.json` 与 `workspace/appearance.json` 保存非密钥界面设置。

## 运行状态

`completed`：规划器结束且最后一条观察不是失败（允许无工具调用）；此前失败可仍在轨迹中，不代表所有观察成功，也不是 GIS 业务验收结论。`failed`：静态计划工具失败、执行循环异常、LLM 连续失败达到 `failure_budget`，或规划器在最后一次工具失败后直接结束；`blocked`：请求写入但缺少写入能力；`denied`：用户拒绝本步；`approval_required`：入口未提供授权处理器；`approval_expired`：授权等待过期；`cancelled`：用户停止、断线、取消信号或工具返回 `Cancelled`，取消优先于纠错；`step_limit`：工具调用上限耗尽后规划器仍请求工具，超限调用不执行。

完整失败观察保留在 `summary.json` 的 `observations` 中；恢复预算耗尽时，顶层 `error` 保留最后错误类型、含连续失败次数的消息与已有 `hint`，并将 `recovery_exhausted`（步骤与错误类型）持久化到 `events.jsonl`。规划器在失败后直接结束也保留最后错误，不标记为完成。任务写入最终快照；日志写入本身不可用时命令失败，不伪造可审计成功。

当前没有已终止任务的自动续跑、多任务并发锁或跨任务工作区事务；运行中失败自纠与历史会话恢复均不等于事务恢复。每次成果独立目录减少冲突，默认逐步串行执行，避免共享 ArcPy 状态的问题。

## 多供应商适配

`ProviderStore` 管理独立连接、凭据引用、各连接选项和当前选择。普通 Python 可导入，管理配置不需要许可证或在线 API。连接元数据在工作区 `providers.json`，凭据只来自环境变量或根目录 `.env.local`，API Key 从不作为模型消息或对外状态字段。

`CompatibleClient` 在现有客户端流式设施上实现按供应商选择的请求参数；`AnthropicClient` 实现独立的 Messages HTTP/SSE 协议。内部使用统一的 assistant/tool 消息形状，Claude 在边界转换 system、图片、tool_use、tool_result、原生思考签名和累计 token 用量。`DeepSeekPlanner` 保留旧名称兼容现有调用者，其结构化工具调度由所有供应商共用。

ChatSession 按客户端配置能力连接流式事件与取消，记录历史来源。切换连接或型号后保留会话文字与完整工具结果，清理不可跨供应商复用的原生思考数据；恢复会话时也检查来源。所有客户端继续经过相同的工具契约和写入控制。

面板增加连接管理、隐藏密钥输入、协议/地址/默认模型/能力设置和预设。RPC 提供连接管理状态与请求，CLI 提供等价命令，详见 [多模型接入说明](providers.md)。计划仍使用 version=1 的 tool/arguments 结构；工具契约已扩展，详见 [工具说明](tools.md)。

## 本批验证边界（2026-11-06）

主控已完成最终离线验证：系统 Python **3.14.6** 经 `scripts/test.ps1` **328 项 OK / 6.635s**，已安装 Pro Python **3.11.11** 经 `PYTHONPATH=src` 下 unittest discover **328 项 OK / 6.939s**，均退出码 0；基线 287 增加 41 项，提示词实测 **3709 字符 < 8000**。覆盖恢复循环、成功清零、失败批次重新规划、终止与授权边界、预算配置接线和提示词纪律；具体命令与本地日志位置见 [CHANGELOG](../CHANGELOG.md)。本次文档续作不重跑套件，整分支审查仍待完成。

本批没有在线模型/API、真实 ArcPy 许可证核验或 GIS 冒烟、GIS 业务成果验收；Pro Python 下离线测试不是有许可证的 ArcPy 验证，不能证明真实模型纠错质量。离线模拟、工具执行成功与 GIS 业务验收须分别报告，真实验证运行说明见 [README](../README.md#验证与测试)。
