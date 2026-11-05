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
| `config.py` | TOML、环境变量、注册表发现；不导入 ArcPy |
| `agent.py` | Planner 协议、JSON 计划校验、观察循环、任务记录 |
| `catalog.py` | 60 个工具的类型化契约、参数校验、权限和授权预览 |
| `approvals.py` | CLI / 工具箱确认、单次 RPC 请求、超时与断线处理 |
| `tools.py` / `gis_tools.py` | 延迟导入 ArcPy、允许列表中的 GIS 操作，复制后编辑 |
| `skills.py` + `skills/*.json` | 声明式多步技能包：加载即对照工具注册表校验，只被 `list_skills`/`read_skill` 只读返回给模型参考，不执行代码 |
| `prompts/system.md` | 接入 LLM 的行为规范与系统提示词（单一事实来源）：`deepseek.py` 导入时加载，三家 provider 共用；人直接编辑 md 即可调整模型行为 |
| `analysis_tools.py` | 批次 2 的空间分析、叠合、几何重塑与属性连接；先检查扩展许可与要素/字段类型再调用 ArcGIS |
| `cartography.py` | 批次 3 的出图与导出：复制安装自带空白模板新建任务内工程、`arcpy.mp` 建布局/图幅/图廓、CIM 加图名；导出侧只读打开、永不 `save()` |
| `runtime.py` | ArcPy 子进程 / Pro 当前进程 / 离线模拟的统一接口 |
| `HostBridgeRuntime` / `ProContext.cs` | 授权后将当前地图工具转交 Pro SDK 的 QueuedTask |
| `worker.py` | 结构化请求和结果文件协议；普通输出不参与协议解析 |
| `cli.py` | 命令行入口、诊断、配置装配、退出码 |
| `providers.py` | 独立连接、凭据引用、OpenAI 兼容协议、Claude Messages 与流式转换 |
| `chat.py` | 会话、来源标记、跨连接历史转换、附件和用量聚合 |
| `arcgis/ArcGISAgent.pyt` | Pro 内的地理处理工具入口，允许访问 CURRENT |

## 新增工具

先在 `catalog.TOOLS` 声明类型化参数、必需项、写入属性、影响与宿主需求，再在 `validate_call` 增加业务约束，最后实现 ArcPy 或 SDK 调用。同一 schema 用于模型声明、CLI 查询、执行前校验和授权预览。创建数据或修改地图的工具必须标记 `writes=True`；数据输出由任务目录生成，关闭覆盖，栅格成果标记 `raster_output=True`，授权预览据此显示 TIFF 路径。需要扩展许可的分析工具在执行前经 `gis_tools.require_extension` 签出，签出失败直接报错。不要绕过统一入口增加任意代码或 shell 执行能力。技能包（`skills.py`）同样受此约束：它是只读方法模板，加载时对照 `catalog.TOOLS` 校验每个步骤的工具名、参数键与字面量取值，模型仍须逐个发起受授权的工具调用，绝不能成为执行任意代码或绕过授权的后门。

Agent 冻结每步参数，生成独立 request_id 和 digest；授权预览、副作用与实际执行使用同一份参数。授权事件为 `tool_requested → approval_requested → approval_decided → tool_executing → tool_result`。无 `approve` 回调不调用 runtime。单次写入许可不会扩散到下一步，全局写入能力也不代替逐次授权。

工具返回 JSON 可序列化数据。真实 GIS 操作出错时交给 runtime 转换为 `ok=False`，Agent 记录后停止；当前不自动修复或回滚。一次成功仅表示声明的工具已执行成功，不代表整个 GIS 分析已经经过业务验收。

## 模型接入

`DeepSeekPlanner` 实现 `Planner.next_action(observations) -> Action | None`，将模型工具调用映射到白名单工具；返回 `None` 表示规划结束。Agent 每一步独立验证参数、检查写入权限并限制步数。`DeepSeekClient` 使用标准库 HTTPS 请求，支持超时；`ChatSession` 按完整轮次裁剪上下文。当前没有自动重试，避免在未知请求状态下重复产生费用。

`PlanPlanner` 是用于验证基础设施的确定性实现。它不解释自然语言，也不根据观察结果修订计划。DeepSeek 适配器使用工具观察结果继续决策，不直接执行模型编写的 Python 代码。

Pro 插件是 WPF DockPane，使用标准输入/输出上的 JSON 行与 `arcgis_agent rpc` 子进程通信。供应商 SSE 流中的文本、返回的推理、工具事件及用量实时传到界面。RPC 主线程接收控制请求，任务线程等待单步授权；ArcPy 子进程可以被终止。`map_context`、`add_to_map`、`set_layer_visibility` 授权后由 Pro SDK 执行，按地图和图层 URI 精确定位，不自动保存工程。其余分析仍使用独立 ArcPy，不继承选择集或 CURRENT。CLI 的 `chat` 和 RPC 共享 `ChatSession`。

RPC 向面板发出 `approval_requested` 事件，面板返回 `{"type":"approval_response","request_id":"…","response":{"decision":"allow|deny|cancel","allow_writes":true}}`。写许可可省略，默认 false。broker 先登记请求再发事件，默认等待 600 秒；过期、重复、错误类型的答复返回 `control_error`。有效答复返回 `control_ack`，实际决定另写审计事件。EOF、取消或断线停止等待，不能默认同意。

宿主工具在授权后发出 `host_requested`，由面板返回 `{"type":"host_result","request_id":"…","response":{"ok":true,"data":{}}}`；失败时 ok=false 并附 error。后端等待 60 秒，面板为宿主请求设置 55 秒截止与任务代次标识；实际地图修改前再检查取消、超时和原任务有效性，防止排队操作迟到执行。send 仅在 Pro 内传 `host_tools:true`，CLI 的模型声明不含宿主工具。

附件在 `attachments.py` 中限制类型、数量、大小与图片模型能力。图片以 data URL 发送，文本以 UTF-8 内容发送，GIS 文件仅传路径。会话文件保留文本、最终答复、工具记录和推理内容，但不保存图片的 base64 数据；恢复会话后需要重新附加图片。`workspace/ui-options.json` 与 `workspace/appearance.json` 保存非密钥界面设置。

## 运行状态

`completed`：规划器结束且已执行的工具成功；`failed`：工具或调度异常；`blocked`：请求写入但缺少写许可；`denied`：用户拒绝本步；`approval_required`：入口未提供授权处理器；`approval_expired`：面板授权等待超时；`cancelled`：用户停止或断线；`step_limit`：规划器在预算耗尽后仍请求工具。任务会写入最终快照；日志写入本身不可用时命令失败，不伪造可审计成功。

当前没有任务恢复、多任务并发锁或跨任务工作区事务。每次成果独立目录减少冲突，默认逐步串行执行，避免共享 ArcPy 状态的问题。

## 多供应商适配

`ProviderStore` 管理独立连接、凭据引用、各连接选项和当前选择。普通 Python 可导入，管理配置不需要许可证或在线 API。连接元数据在工作区 `providers.json`，凭据只来自环境变量或根目录 `.env.local`，API Key 从不作为模型消息或对外状态字段。

`CompatibleClient` 在现有客户端流式设施上实现按供应商选择的请求参数；`AnthropicClient` 实现独立的 Messages HTTP/SSE 协议。内部使用统一的 assistant/tool 消息形状，Claude 在边界转换 system、图片、tool_use、tool_result、原生思考签名和累计 token 用量。`DeepSeekPlanner` 保留旧名称兼容现有调用者，其结构化工具调度由所有供应商共用。

ChatSession 按客户端配置能力连接流式事件与取消，记录历史来源。切换连接或型号后保留会话文字与完整工具结果，清理不可跨供应商复用的原生思考数据；恢复会话时也检查来源。所有客户端继续经过相同的工具契约和写入控制。

面板增加连接管理、隐藏密钥输入、协议/地址/默认模型/能力设置和预设。RPC 提供连接管理状态与请求，CLI 提供等价命令，详见 [多模型接入说明](providers.md)。计划仍使用 version=1 的 tool/arguments 结构；工具契约已扩展，详见 [工具说明](tools.md)。
