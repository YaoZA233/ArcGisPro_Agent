# Agent 失败自纠与更长多步任务 · 设计规格

- 日期：2026-11-06
- 状态：已通过分节设计确认，待用户审阅本规格
- 范围：让 LLM 驱动的 Agent 在工具失败后自我纠错重试，而不是把问题抛回给用户；支持更长的多步任务。**不改动逐次授权安全模型。**

## 1. 背景与根因

用户反馈：与市面商业 Agent 相比，本 Agent "不够聪明"，遇到失败就把问题交回用户。接入 AI 的目的正是把问题交给 AI 解决。

经核查，根因是**控制流缺陷，而非模型能力不足**：

- `src/arcgis_agent/agent.py` 第 129-131 行：任何工具返回 `not result["ok"]` 时立即 `break` 并把状态置为 `"failed"`，循环终止。
- `src/arcgis_agent/deepseek.py` 的 `DeepSeekPlanner.next_action`：本来就已经把失败观测（含 `error` / `hint` / `stderr`）作为 `{"role":"tool"}` 消息追加进 `self.messages`，并在 `pending` 为空时重新调用 `client.complete(...)` 请求模型。
- `src/arcgis_agent/runtime.py` 与 `worker.py`：失败结果已附带 `hints.py` 的纠正提示（如 `ERROR 000732 → 用 find_datasets 定位`、`ERROR 999999 → 显式 cell_size`、`schema lock`、`transformation` 等）。

结论：**自我纠错的接线早已存在**（失败观测 + hint 已能回流模型），只是 agent 循环在模型有机会纠错之前就退出了。修复点是循环的失败分支，而非新增模型能力。

## 2. 目标与非目标

### 目标
1. LLM 驱动的会话（聊天 / Pro 面板）在工具失败后，把失败反馈给模型并允许其采取不同的纠正动作重试。
2. 用连续失败预算与总步数上限双重约束纠错，避免无意义空转与失控成本。
3. 支持更长的多步任务（提高默认步数上限）。
4. 通过提示词强化"先规划、失败反思、更强自主"，减少把技术问题抛回用户。

### 非目标（明确不做）
- **不削弱授权模型**：逐次授权、写操作单独授予、拒绝/取消/过期即停，全部保持不变（依用户选择"只减反问，保留逐次授权"）。
- **不引入结构化预规划阶段**：反应式工具循环里强行加"先产出完整计划再执行"的阶段会与循环打架，属过度设计；"先规划"仅作为提示词纪律落地。
- **不改变确定性计划执行器（PlanPlanner）的 fail-fast 语义**：CLI 静态计划与 Pro 工具箱必须保持"一步失败即停"。
- **不新增工具**：工具白名单数量不变（当前 62）。

## 3. 架构

### 3.1 Planner 能力标志
给 planner 增加一个"是否具备纠错能力"的标志，agent 循环据此决定失败后是终止还是反馈：

- `PlanPlanner.recovers = False`（确定性计划：CLI `run`、Pro 工具箱 `ExecutePlan` / `InspectProject`）——保持 fail-fast。
- `DeepSeekPlanner.recovers = True`（LLM：聊天、Pro 面板）——失败反馈给模型自纠。
- `agent.py` 读取方式：`recovers = getattr(planner, "recovers", False)`（缺省 False，向后兼容任何未声明该属性的 planner）。

### 3.2 失败分支改写
`agent.py` 循环内，工具执行得到 `result` 并追加观测后：

- `result["ok"]` 为真 → `consecutive_failures = 0`，继续循环（现状不变）。
- `result["ok"]` 为假：
  - `recovers` 为假 → `summary["status"] = "cancelled" if cancel_event and cancel_event.is_set() else "failed"`，`break`（**完全保留现状**，保住 `test_failed_tool_stops_following_steps` 的 `runtime.calls == 1` 断言）。
  - `recovers` 为真 → `consecutive_failures += 1`：
    - 若 `consecutive_failures >= self.failure_budget` → `summary["status"] = "failed"`，写入 `summary["error"]`（见 §5），`break`。
    - 否则**不 break**，进入下一轮循环；`planner.next_action(observations)` 会把失败观测回流模型，模型据此产生新的（不同的）动作或最终文本。

### 3.3 保持终止的条件（绝不自动重试）
以下分支在改动后**仍然立即终止**，因为它们是安全契约或不可恢复错误：

- 授权 `choice != "allow"`（`deny` / `cancel` / `expired`）→ 状态 `denied` / `cancelled` / `approval_expired`。
- 写操作未获单独授权 → 状态 `blocked`。
- `cancel_event.is_set()`（循环顶部与执行前后多处检查）→ 状态 `cancelled`。
- 循环内抛出的 Python 异常（如 `DeepSeekError` 模型返回格式非法、`validate_call` 失败）→ 由外层 `try/except` 捕获，状态 `failed`，`summary["error"] = {type, message}`。

原则：**永不绕过用户拒绝，永不自动重试被阻止的写操作。**

## 4. 组件改动清单

| 文件 | 改动 |
| --- | --- |
| `src/arcgis_agent/agent.py` | `PlanPlanner` 加 `recovers = False`；`Agent.__init__` 增加 `failure_budget: int = 3`；循环内加 `consecutive_failures` 计数与改写后的失败分支（§3.2）；预算耗尽时写 `summary["error"]` 并发 `recovery_exhausted` 事件。 |
| `src/arcgis_agent/deepseek.py` | `DeepSeekPlanner` 加类属性 `recovers = True`。`next_action` 纠错逻辑无需改动。 |
| `src/arcgis_agent/config.py` | `Settings` 增加 `failure_budget: int = 3`；`max_steps` 默认 `8 → 16`；从 `[agent]` 读取 `failure_budget`，校验 `type is int and 1 <= failure_budget <= 20`，否则 `ValueError`。 |
| `src/arcgis_agent/chat.py` | `ChatSession.__init__` 增加 `failure_budget: int = 3` 参数；构造 `Agent(...)` 时透传。 |
| `src/arcgis_agent/interfaces.py` | `create_session` 把 `settings.failure_budget` 传给 `ChatSession`。 |
| `src/arcgis_agent/prompts/system.md` | 提示词纪律：先规划、失败反思、更强自主（§6）。 |

`cli.py`、Pro 工具箱（`ArcGISAgent.pyt`）走 `PlanPlanner`，`recovers=False`，行为零变化；它们构造 `Agent` 时不传 `failure_budget`，使用默认值即可（对 fail-fast 路径无影响）。

## 5. 数据流

### 5.1 一次成功纠错的时序
1. 模型请求 `buffer(dataset="X", ...)` → 用户授权 → 执行 → ArcPy 返回 `ERROR 000732`，runtime 附 `hint`。
2. `agent.py` 追加观测 `{ok:False, error:{type,message,hint,stderr}}`，`consecutive_failures=1`，因 `recovers=True` 不 break。
3. 下一轮 `DeepSeekPlanner.next_action`：`active_call` 非空且 `len(observations)==seen+1` → 追加 `tool` 消息 → `seen+=1`、`active_call=None`；`pending` 空 → `client.complete(messages)`。
4. 模型读到 hint，改调 `find_datasets(keyword="X")`（不同动作）→ 成功 → `consecutive_failures=0`。
5. 模型用真实路径重新 `buffer` → 成功 → 继续后续步骤 → 返回最终文本（`next_action` 返回 `None`）→ 状态 `completed`。

### 5.2 预算耗尽
模型无视 hint 连续失败达 `failure_budget`（默认 3）次 → 状态 `failed`，`summary["error"]`：

```json
{
  "type": "<最后一次 error.type，缺省 ToolFailure>",
  "message": "连续 3 次工具调用失败，已停止自动重试；最后错误：<最后一次 error.message>",
  "hint": "<最后一次 error.hint，若存在>"
}
```

完整失败观测仍在 `summary["observations"]` 中持久化，供用户与日志查阅。同时发 `recovery_exhausted` 事件（含 `step`、最后 `error.type`）以便观测。

### 5.3 步数耗尽
总步数达 `max_steps`（默认 16，即 `index == self.max_steps` 守卫）→ 状态 `step_limit`（现状语义不变）。

### 5.4 Timeout / Stalled
runtime 把 worker 超时/停滞作为普通失败结果返回（`{ok:False, error:{type:"Timeout"/"Stalled", hint:...}}`），因此同样按"可纠错失败"处理：模型可改用更小数据、显式 `cell_size` 等重试，并计入连续失败预算。用户可随时 cancel（`cancel_event` 终止优先）。

## 6. 提示词纪律（system.md）

全部为提示词改动，不引入结构化阶段。当前文件 2973 字符，预计新增 300-400 字符，仍远低于 8000 上限。

- **先规划**：在"标准工作流"加入——多步任务先用一两句话说明执行计划，再开始调用工具。
- **失败反思**（强化现有第 25 行）：工具失败时依据 `error`+`hint` 诊断根因，采取**不同**的纠正动作；事实不足时先用 `describe` / `find_datasets` / `read_rows` 等只读工具补齐再重试；**绝不把技术错误原样抛回让用户解决**。
- **更强自主**（强化第 12-13 行）：技术性不确定一律自行用只读探查工具消解；只有无法从数据推断的业务取舍（分析范围、方法选择、是否覆盖已有成果等）才询问用户。
- **授权边界一字不改**：逐次授权、写操作单独授予、拒绝/取消即停。

## 7. 错误处理

- 预算耗尽与步数耗尽均为终止状态，写入 `summary.json` 并发 `finished` 事件（现状机制）。
- `summary["error"]` 类型保持与既有一致：字符串（`approval_required` / `blocked`）或字典 `{type, message[, hint]}`（异常 / 预算耗尽）。
- 安全终止分支（deny / cancel / blocked / 异常）优先级高于纠错：纠错只在"工具已执行且返回 ok=False"时触发。

## 8. 测试与验证

### 8.1 离线单元/集成测试（`./scripts/test.ps1`，仅需系统 Python ≥3.11）
- `tests/test_foundation.py`
  - **保留** `test_failed_tool_stops_following_steps`（PlanPlanner fail-fast，`runtime.calls == 1`）。
  - 新增：纠错 planner 失败后循环继续、下一动作成功 → `completed`。
  - 新增：连续失败达 `failure_budget` → `failed`，且超出后不再调用 runtime。
  - 新增：成功后计数清零（失败-成功-失败-成功 不触发预算耗尽）。
  - 新增：纠错 planner 下 `deny` / `blocked` 仍立即终止（状态 `denied` / `blocked`）。
  - 新增：配置解析 `failure_budget` 正常值与越界（0、21、非整数）→ `ValueError`。
- `tests/test_system_prompt.py`：`REQUIRED_MARKERS` 增加"执行计划/先规划""不同的纠正动作/不抛回用户""只读探查"等标记。
- `tests/test_approvals.py`：`len(TOOLS)` 仍为 62（无新工具）。
- 全量套件须全绿（当前 287，预计 +6~8）。

### 8.2 文档
- `README.md` / `README.en.md`：Agent 行为说明（失败自纠、更长任务）。
- `docs/architecture.md`：agent 循环失败分支与 planner 能力标志说明。
- `CHANGELOG.md`：2026-11-06 条目——具体行为变更、影响模块、验证方式、已知限制。

### 8.3 已知限制（写入 CHANGELOG）
- 纠错质量取决于模型对 hint 的利用；预算耗尽仍可能以 `failed` 终止，但此时已给出可操作的最后错误与 hint。
- Timeout 重试可能累计较长等待（默认 180s × 最多 3 次），用户可 cancel。
- 真机 ArcPy 验证不在离线套件内；如需，按 AGENTS.md 用 Pro Python 跑 `scripts/smoke_arcpy.py`（仅写合成数据于 `workspace/smoke`）。

## 9. 验收标准

1. 聊天/面板会话中，一次可恢复的工具失败（如路径错误）后，模型能自动改用纠正动作并最终完成任务，无需用户介入排错。
2. 确定性计划（CLI / 工具箱）失败即停，行为与改动前完全一致。
3. 用户拒绝、取消、写操作未授权时，Agent 立即停止，绝不自动重试或绕过。
4. 连续失败达预算或步数达上限时，以清晰状态终止并持久化最后错误与 hint。
5. 全量离线测试通过；提示词在预算内；文档与 CHANGELOG 同步更新。
