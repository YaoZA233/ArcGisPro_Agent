# 多模型接入

本项目将“连接配置”和“请求协议”分离。每个连接保存名称、地址、默认模型、协议、推理模式和图片/工具能力；同一家可创建多个独立连接。CLI 与 Pro 面板读取相同工作区配置。

## 供应商预设

| 连接 ID | 默认 Base URL | 密钥环境变量 |
| --- | --- | --- |
| `deepseek` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| `qwen` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` |
| `kimi` | `https://api.moonshot.cn/v1` | `MOONSHOT_API_KEY` |
| `glm` | `https://open.bigmodel.cn/api/paas/v4` | `ZHIPU_API_KEY` |
| `openai` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `claude` | `https://api.anthropic.com/v1` | `ANTHROPIC_API_KEY` |
| `grok` | `https://api.x.ai/v1` | `XAI_API_KEY` |

预设模型仅为可修改的初始值，不能代表账户权限、最新型号或全部能力。Qwen 国际区等地址请按实际账户修改。刷新模型接口不可用时，直接填写服务商提供的准确模型 ID。中转站需要自己的地址和密钥，选择它实际实现的协议。

## 协议与地址

- `openai`：Chat Completions 兼容协议，追加 `/chat/completions`；模型发现追加 `/models`。
- `anthropic`：Claude 原生 Messages 协议，追加 `/messages`；模型发现使用带分页的 `/models`，请求携带 `x-api-key` 和 `anthropic-version`。

Base URL 须保留所需前缀，例如 `/v1`、`/compatible-mode/v1`、`/api/paas/v4`，不要填完整的请求路径。示例见 `examples/provider-relay.json`，使用前修改地址和型号。

仅接受 HTTPS；本机 `localhost`、`127.0.0.1`、`::1` 允许 HTTP。禁止 URL 中的用户凭据、查询参数和片段。遇到重定向直接失败，避免把密钥转发到另一地址。所有连接需要非空 API Key；无鉴权本地服务可填该服务允许的占位 key。

当前实现 API Key 接入，不包含网页账户/订阅登录、Grok 网页接口、OpenAI Responses 专用协议或 Gemini 原生协议。只提供其他协议的服务不能直接使用本适配。

## 模型能力与推理

图片和工具能力在连接管理中设置，更换模型时须同步检查。DeepSeek 图片能力按已知型号自动判断；其他型号使用连接设置。缺少图片能力时，新图片在请求前被拒绝，历史图片替换为文字占位。附件数量、大小与格式限制继续适用。

禁用 GIS 工具时不发送工具定义，服务返回的工具调用也不会执行。启用后，所有供应商都经过同一类型化工具白名单、参数校验、步数上限和逐次用户授权；读取也会询问，全局写入开关不跳过确认。Claude 图片转换为原生 Base64 块，工具结果按 `tool_result` 合并，同一连接与模型的思考块及签名保留到下一次工具请求。

| 推理模式 | 请求行为 |
| --- | --- |
| `none` | 不发送推理参数，使用服务默认行为，界面禁用程度选择 |
| `deepseek` | `thinking.type` 开关，加 low/high/max 的 `reasoning_effort` |
| `qwen` | `enable_thinking` 开关；low/high/max 均表示开启 |
| `kimi` / `glm` | `thinking.type` 开关；low/high/max 均表示开启 |
| `openai` | 使用 `max_completion_tokens`；开启时发送 low/high，max 映射 high；off 不传 effort，行为由模型决定 |
| `anthropic` | 显式思考预算 low 1024、high 4096、max 16384；裁剪至输出上限减一，输出上限须大于 1024 |

普通 GPT 预设为 none；选择支持 reasoning effort 的 GPT/o 系列时可改 openai。只支持 adaptive thinking 或其他参数的 Claude 型号不适用显式预算，可先用 none。参数可用性仍由具体型号和接口版本决定，HTTP 400 时先检查能力并尝试 none。

仅展示 API 返回的推理内容或摘要，部分服务不会返回。token 统计使用实际用量字段，Claude 包括缓存输入且不重复累加流式累计输出；OpenAI 缓存输入也参与统计。接口不接受 `stream_options` 时，关闭“请求流式 token 用量”；未返回用量显示 `—`。

## 存储与切换

- `.env.local`：本地明文凭据，被忽略，不进入模型配置、聊天、任务日志或 RPC 输出。环境变量优先；原有 `DEEPSEEK_API_KEY` 无需迁移。
- `workspace/providers.json`：连接列表、当前连接与各自的模型/推理/输出上限/图片精度，不含密钥。自定义连接的变量名在 `providers list` 的 `key_env` 中显示，按连接 ID 的十六进制编码生成，避免连字符与下划线碰撞。
- `workspace/ui-options.json`：旧版设置；连接尚无新格式选项时继续读取。
- `config/local.toml`：旧 DeepSeek 名称和地址作为初始值，`model.provider` 指定首次连接；保存后以 `providers.json` 为准。

切换保留会话文字和完整工具结果，清理其他连接或模型的原生推理信息。会话保存来源，恢复到不同连接或型号时也执行转换。新模型对历史工具上下文存在额外限制时，可新建对话。图片本体不写入会话文件。

任务执行中不能切换连接。CLI 与面板共享文件，但不向其他已启动进程广播状态，其他窗口需自行切换或重启，避免同时编辑。删除前先切换到另一个连接；删除配置保留本机密钥。

## CLI 与 RPC

CLI：`providers list|presets|save <JSON文件>|use <连接ID>|delete <连接ID>`、`setup-key --provider <连接ID>`。密钥只在隐藏提示输入，不接受命令行 key 参数。交互聊天增加 `/providers`、`/provider <连接ID>`、`/models`。

RPC 增加 `providers`、`provider_select`（`provider`）、`provider_save`（`profile`、可选 `api_key`、可选 `activate`）、`provider_delete`（`provider`），响应类型均为 `providers`。状态包括脱敏连接列表、预设、能力和 `key_configured`，任务结果增加 `provider`。原有 send/configure/cancel/history 继续使用。

## 验证范围

离线测试使用模拟 HTTP/SSE 响应验证各兼容供应商的工具往返，以及 Claude 图片、分片工具参数、思考签名、多工具结果、用量、截断拒绝、错误脱敏和取消。另覆盖账户密钥隔离、地址校验、持久化、CLI 和 RPC。

DeepSeek 另有真实 API 图片、推理和只读 ArcPy 环境工具验证。其他供应商尚未用真实账户逐一验证，也未验证任意中转站。离线测试、真实工具成功与 GIS 业务核验是不同验证层次。
