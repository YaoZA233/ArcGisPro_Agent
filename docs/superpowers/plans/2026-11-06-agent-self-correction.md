# Agent 失败自纠与更长多步任务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 LLM 驱动的会话在工具失败后把失败反馈给模型自我纠错重试（受连续失败预算与步数上限约束），而不是把问题抛回给用户；确定性计划保持失败即停；逐次授权安全模型一字不改。

**Architecture:** 根因是 `agent.py` 循环在任何 `not result["ok"]` 时立即 `break`，而 `DeepSeekPlanner.next_action` 早已会把失败观测（含 `error`/`hint`）回流模型。给 planner 增加 `recovers` 能力标志：`PlanPlanner.recovers=False`（fail-fast 不变），`DeepSeekPlanner.recovers=True`；agent 循环对可纠错 planner 不再 break，改为累加连续失败计数并继续循环，达 `failure_budget`（默认 3）才终止。`max_steps` 默认 8→16 支持更长任务。规划/反思/自主仅作为 `system.md` 提示词纪律落地，不引入结构化预规划阶段。

**Tech Stack:** Python 3.11+ 标准库（核心可脱离 ArcPy 导入）；unittest（离线，`PYTHONPATH=src` + `discover -s tests`）；Markdown 提示词。

**Spec:** `docs/superpowers/specs/2026-11-06-agent-self-correction-design.md`

## Global Constraints

- 目标平台 Windows + ArcGIS Pro 3.5.2；核心包必须可在无 ArcPy 下导入；离线测试仅需系统 Python ≥3.11。
- 仅用标准库；绝不向 Esri 基础 Python 环境安装任何包。
- **逐次授权安全模型完全保留**：`deny`/`cancel`/`expired`/写操作未授权 `blocked`/`cancel_event` 置位/循环内 Python 异常——全部立即终止，绝不自动重试或绕过用户拒绝。纠错只在"工具已执行且返回 `ok=False`"时触发。
- `PlanPlanner`（CLI 静态计划 + Pro 工具箱）保持 fail-fast：一步失败即停，`runtime.calls == 1`（`test_failed_tool_stops_following_steps` 必须继续通过）。
- 不新增工具：`len(TOOLS)` 保持 62。
- `failure_budget` 校验：`type is int and 1 <= failure_budget <= 20`，默认 3。`max_steps` 校验保持 `1..100`，默认改为 16。
- `prompts/system.md` 长度必须 `< 8000` 字符（每轮请求随附）。
- 密钥/凭据绝不进入源码、日志或记忆。
- 每批功能改动记入 `CHANGELOG.md`（日期、具体行为、影响模块、验证、已知限制）；公开 schema/CLI 变更同步 README 与工具契约测试。
- 测试运行（Git Bash，仓库根目录）：
  - 单项：`PYTHONPATH="src;tests" python -m unittest <module>.<Class>.<test> -v`
  - 全量：`PYTHONPATH="src" python -m unittest discover -s tests -v`（等价于 `./scripts/test.ps1`，PowerShell 下用 `powershell -ExecutionPolicy Bypass -File scripts/test.ps1`）

## File Structure

| 文件 | 职责 | 本计划改动 |
| --- | --- | --- |
| `src/arcgis_agent/agent.py` | 规划-执行循环、步数上限、任务留痕 | `PlanPlanner.recovers=False`；`Agent.__init__` 增 `failure_budget=3`；循环失败分支按 `recovers` 分流 + 连续失败预算 |
| `src/arcgis_agent/deepseek.py` | DeepSeek 客户端与 LLM planner | `DeepSeekPlanner.recovers=True`（`next_action` 纠错逻辑已存在，无需改） |
| `src/arcgis_agent/config.py` | `Settings` 加载与校验 | 增 `failure_budget` 字段+解析+校验；`max_steps` 默认 8→16 |
| `src/arcgis_agent/chat.py` | `ChatSession` 会话编排 | `__init__` 增 `failure_budget=3`，构造 `Agent` 时透传 |
| `src/arcgis_agent/interfaces.py` | `create_session` 接线 | 把 `settings.failure_budget` 传给 `ChatSession` |
| `config/agent.example.toml` | 配置示例 | `max_steps = 16`；新增注释化 `failure_budget` 示例 |
| `src/arcgis_agent/prompts/system.md` | 模型行为规范（单一事实来源） | 先规划 / 失败反思 / 更强自主三处纪律 |
| `tests/test_foundation.py` | agent 循环与配置契约 | 纠错/预算/清零/安全终止/能力标志/配置解析校验用例 |
| `tests/test_chat.py` | 会话编排契约 | 预算经 `ChatSession` 端到端生效用例 |
| `tests/test_system_prompt.py` | 提示词契约 | `REQUIRED_MARKERS` 增 4 个标记 |
| `README.md` / `README.en.md` / `docs/architecture.md` / `CHANGELOG.md` | 文档 | 行为说明、状态语义、测试计数、变更日志 |

任务顺序：Task 1（循环+能力标志，核心行为）→ Task 2（配置+接线，使预算可调并端到端生效）→ Task 3（提示词纪律）→ Task 4（文档+全量验证）。Task 2 的端到端测试依赖 Task 1。

---

### Task 1: Agent 循环失败自纠 + planner 能力标志

**Files:**
- Modify: `src/arcgis_agent/agent.py:28-34`（`PlanPlanner`）、`src/arcgis_agent/agent.py:50-52`（`Agent.__init__`）、`src/arcgis_agent/agent.py:61-131`（`run` 循环）
- Modify: `src/arcgis_agent/deepseek.py:211-214`（`DeepSeekPlanner` 类头）
- Test: `tests/test_foundation.py`

**Interfaces:**
- Consumes: 既有 `Action`、`PlanPlanner`、`Agent.run(planner, *, allow_writes, on_event, cancel_event, approve)`、`fixture_approval`、`MockRuntime`、`step()`、`validate_call`。
- Produces:
  - `Agent(runtime, workspace, max_steps=8, failure_budget=3)`（新位置参数 `failure_budget` 追加在 `max_steps` 之后）。
  - `PlanPlanner.recovers == False`（类属性）；`DeepSeekPlanner.recovers == True`（类属性）。
  - agent 循环用 `recovers = getattr(planner, "recovers", False)` 读取；失败且 `recovers` 为真时不 break，累加 `consecutive_failures`，达 `self.failure_budget` 置 `status="failed"` 并写 `summary["error"] = {"type", "message"[, "hint"]}`、发 `recovery_exhausted` 事件。
  - `result["ok"]` 为真时 `consecutive_failures` 清零。

- [ ] **Step 1: 写失败测试（追加到 `tests/test_foundation.py` 的 `FoundationTests` 类内）**

```python
    def test_recovering_planner_continues_after_tool_failure(self):
        class FailOnce:
            def __init__(self):
                self.calls = 0

            def call(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return {"ok": False, "error": {"type": "ArcPy", "message": "ERROR 000732", "hint": "用 find_datasets 定位"}}
                return {"ok": True, "data": {"path": "out"}}

        class Recovering:
            recovers = True

            def __init__(self, actions):
                self.actions = list(actions)

            def next_action(self, observations):
                return self.actions[len(observations)] if len(observations) < len(self.actions) else None

        runtime = FailOnce()
        result = Agent(runtime, self.root, 8, 3).run(Recovering([step(), step()]), approve=fixture_approval)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(runtime.calls, 2)
        self.assertFalse(result["observations"][0]["ok"])
        self.assertTrue(result["observations"][1]["ok"])

    def test_consecutive_failure_budget_stops_recovering_run(self):
        class AlwaysFail:
            def __init__(self):
                self.calls = 0

            def call(self, *args, **kwargs):
                self.calls += 1
                return {"ok": False, "error": {"type": "ArcPy", "message": "boom", "hint": "h"}}

        class AlwaysRecover:
            recovers = True

            def next_action(self, observations):
                return Action("environment", {})

        runtime = AlwaysFail()
        result = Agent(runtime, self.root, 8, 3).run(AlwaysRecover(), approve=fixture_approval)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(runtime.calls, 3)
        self.assertEqual(result["error"]["type"], "ArcPy")
        self.assertIn("连续 3 次", result["error"]["message"])
        self.assertEqual(result["error"]["hint"], "h")

    def test_success_resets_consecutive_failure_counter(self):
        class Pattern:
            results = [False, False, True, False, False, True]

            def __init__(self):
                self.calls = 0

            def call(self, *args, **kwargs):
                ok = self.results[self.calls]
                self.calls += 1
                return {"ok": True, "data": {}} if ok else {"ok": False, "error": {"type": "ArcPy", "message": "boom"}}

        class Scripted:
            recovers = True

            def __init__(self, count):
                self.count = count

            def next_action(self, observations):
                return Action("environment", {}) if len(observations) < self.count else None

        runtime = Pattern()
        result = Agent(runtime, self.root, 8, 3).run(Scripted(6), approve=fixture_approval)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(runtime.calls, 6)

    def test_denial_still_terminal_for_recovering_planner(self):
        class Recovering:
            recovers = True

            def next_action(self, observations):
                return Action("environment", {}) if not observations else None

        deny = lambda request: {"decision": "deny", "allow_writes": False}
        result = Agent(MockRuntime(), self.root, 8, 3).run(Recovering(), approve=deny)
        self.assertEqual(result["status"], "denied")
        self.assertEqual(result["observations"], [])

    def test_blocked_write_still_terminal_for_recovering_planner(self):
        class Recovering:
            recovers = True

            def next_action(self, observations):
                if observations:
                    return None
                return Action("buffer", {"dataset": "x", "distance": "1 Meters", "output_name": "out"})

        allow_no_write = lambda request: {"decision": "allow", "allow_writes": False}
        with patch.object(MockRuntime, "call", side_effect=AssertionError("Must not dispatch")):
            result = Agent(MockRuntime(), self.root, 8, 3).run(Recovering(), approve=allow_no_write)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["observations"], [])

    def test_planner_recovery_capability_flags(self):
        from arcgis_agent.deepseek import DeepSeekPlanner
        self.assertFalse(PlanPlanner.recovers)
        self.assertTrue(DeepSeekPlanner.recovers)
```

- [ ] **Step 2: 运行新测试，确认失败**

Run: `PYTHONPATH="src;tests" python -m unittest test_foundation.FoundationTests.test_recovering_planner_continues_after_tool_failure test_foundation.FoundationTests.test_consecutive_failure_budget_stops_recovering_run test_foundation.FoundationTests.test_planner_recovery_capability_flags -v`
Expected: FAIL —— `test_recovering_planner_continues_after_tool_failure` 得到 `status="failed"`（当前循环遇失败即 break）；`test_consecutive_failure_budget_stops_recovering_run` 报 `Agent.__init__() got multiple values`/无 `error` 或 `runtime.calls==1`；`test_planner_recovery_capability_flags` 报 `AttributeError: ... has no attribute 'recovers'`。

- [ ] **Step 3: 实现 `agent.py` 改动**

3a. `PlanPlanner` 加能力标志（`src/arcgis_agent/agent.py:28-31`）：

```python
class PlanPlanner:
    """Deterministic plan executor for the foundation, not a natural-language model."""
    recovers = False

    def __init__(self, steps: list[dict]):
        self.actions = [Action(step["tool"], dict(step["arguments"])) for step in steps]
```

3b. `Agent.__init__` 增 `failure_budget`（`src/arcgis_agent/agent.py:50-52`）：

```python
class Agent:
    def __init__(self, runtime: Runtime, workspace: Path, max_steps: int = 8, failure_budget: int = 3):
        self.runtime, self.workspace, self.max_steps = runtime, workspace.resolve(), max_steps
        self.failure_budget = failure_budget
```

3c. 在 `run` 内、`for` 循环之前初始化计数与能力标志（紧接 `observations: list[dict] = []` 与 `summary = {...}` 之后，`def event(...)` 之前或之后均可，建议在 `event("started", ...)` 之前）：

```python
        recovers = getattr(planner, "recovers", False)
        consecutive_failures = 0
```

3d. 替换循环末尾的失败分支（原 `src/arcgis_agent/agent.py:126-131`）。原代码：

```python
                observation = {"tool": action.tool, "arguments": action.arguments, **result}
                observations.append(observation)
                event("tool_result", {**observation, "step": index + 1})
                if not result["ok"]:
                    summary["status"] = "cancelled" if cancel_event and cancel_event.is_set() else "failed"
                    break
```

改为：

```python
                observation = {"tool": action.tool, "arguments": action.arguments, **result}
                observations.append(observation)
                event("tool_result", {**observation, "step": index + 1})
                if result["ok"]:
                    consecutive_failures = 0
                    continue
                if cancel_event and cancel_event.is_set():
                    summary["status"] = "cancelled"
                    break
                if not recovers:
                    summary["status"] = "failed"
                    break
                consecutive_failures += 1
                if consecutive_failures >= self.failure_budget:
                    last_error = result.get("error") or {}
                    summary["status"] = "failed"
                    summary["error"] = {
                        "type": last_error.get("type", "ToolFailure"),
                        "message": f"连续 {self.failure_budget} 次工具调用失败，已停止自动重试；最后错误：{last_error.get('message', '')}",
                    }
                    if last_error.get("hint"):
                        summary["error"]["hint"] = last_error["hint"]
                    event("recovery_exhausted", {"step": index + 1, "type": summary["error"]["type"]})
                    break
```

- [ ] **Step 4: 实现 `deepseek.py` 改动（`src/arcgis_agent/deepseek.py:211-214`）**

```python
class DeepSeekPlanner:
    recovers = True

    def __init__(self, client: DeepSeekClient, history: list[dict], prompt):
        self.client = client
```

（仅在类体首行加 `recovers = True`，其余不动。）

- [ ] **Step 5: 运行新测试，确认通过**

Run: `PYTHONPATH="src;tests" python -m unittest test_foundation.FoundationTests.test_recovering_planner_continues_after_tool_failure test_foundation.FoundationTests.test_consecutive_failure_budget_stops_recovering_run test_foundation.FoundationTests.test_success_resets_consecutive_failure_counter test_foundation.FoundationTests.test_denial_still_terminal_for_recovering_planner test_foundation.FoundationTests.test_blocked_write_still_terminal_for_recovering_planner test_foundation.FoundationTests.test_planner_recovery_capability_flags -v`
Expected: PASS（6 项全过）。

- [ ] **Step 6: 运行全量套件，确认无回归**

Run: `PYTHONPATH="src" python -m unittest discover -s tests -v`
Expected: PASS，且 `test_failed_tool_stops_following_steps`、`test_planner_cannot_loop_forever`、`test_exact_limit_can_complete`、`test_runtime_exception_is_persisted`、`test_offline_run_has_durable_trace_and_simulation_label` 均仍通过（总数 287 + 6 = 293）。

- [ ] **Step 7: 提交**

```bash
git add src/arcgis_agent/agent.py src/arcgis_agent/deepseek.py tests/test_foundation.py
git commit -m "feat: LLM planner 工具失败后自纠重试，受连续失败预算约束

agent 循环对 recovers=True 的 planner 不再遇失败即停，改为回流模型自纠；
连续失败达 failure_budget(默认3) 才终止。PlanPlanner 保持 fail-fast。
deny/cancel/blocked/异常仍立即终止，逐次授权安全模型不变。"
```

---

### Task 2: 配置 failure_budget + max_steps 默认 16 + 会话接线

**Files:**
- Modify: `src/arcgis_agent/config.py:16`（`max_steps` 字段默认）、`src/arcgis_agent/config.py` `Settings` 字段表末尾、`Settings.load`（解析+校验+`cls(...)` 位置参数）
- Modify: `src/arcgis_agent/chat.py:80-81`（`ChatSession.__init__`）、`src/arcgis_agent/chat.py:227`（构造 `Agent`）
- Modify: `src/arcgis_agent/interfaces.py:41`（`create_session`）
- Modify: `config/agent.example.toml:10`（`max_steps`）+ 新增 `failure_budget` 注释示例
- Test: `tests/test_foundation.py`（配置）、`tests/test_chat.py`（端到端）

**Interfaces:**
- Consumes: Task 1 的 `Agent(..., failure_budget=...)` 与 `DeepSeekPlanner.recovers=True`；既有 `Settings.load`、`ChatSession.send`、`fixture_approval`、`MockRuntime`、`FakeClient`/`assistant`/`call`（test_chat.py 顶部已定义）。
- Produces:
  - `Settings.failure_budget: int`（默认 3，字段顺序排在 `data_roots` 之后）；`Settings.max_steps` 默认 16。
  - `ChatSession(client, runtime, workspace, max_steps=8, memory=None, failure_budget=3)`；`self.failure_budget` 透传给 `Agent`。
  - `create_session` 把 `settings.failure_budget` 传入 `ChatSession`。

- [ ] **Step 1: 写配置失败测试（追加到 `tests/test_foundation.py` 的 `FoundationTests`）**

```python
    def test_config_failure_budget_parsed(self):
        config = self.root / "fb.toml"
        config.write_text('[agent]\nworkspace = "outputs"\nfailure_budget = 5\n', encoding="utf-8")
        self.assertEqual(Settings.load(config).failure_budget, 5)

    def test_config_failure_budget_and_max_steps_defaults(self):
        config = self.root / "fb-default.toml"
        config.write_text('[agent]\nworkspace = "outputs"\n', encoding="utf-8")
        settings = Settings.load(config)
        self.assertEqual(settings.failure_budget, 3)
        self.assertEqual(settings.max_steps, 16)

    def test_config_failure_budget_validated(self):
        for content in ('[agent]\nfailure_budget = 0', '[agent]\nfailure_budget = 21', '[agent]\nfailure_budget = "x"'):
            config = self.root / "fb-invalid.toml"
            config.write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError):
                Settings.load(config)
```

- [ ] **Step 2: 写端到端失败测试（追加到 `tests/test_chat.py` 的 `ChatTests`）**

```python
    def test_failure_budget_flows_through_chat_session(self):
        class AlwaysFail:
            def __init__(self):
                self.calls = 0

            def call(self, *args, **kwargs):
                self.calls += 1
                return {"ok": False, "error": {"type": "ArcPy", "message": "boom", "hint": "h"}}

        client = FakeClient([assistant(calls=[call("environment", {}, "a")]) for _ in range(4)])
        runtime = AlwaysFail()
        session = ChatSession(client, runtime, self.root, 8, None, 2)
        result = session.send("任务", approve=fixture_approval)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(runtime.calls, 2)
```

- [ ] **Step 3: 运行新测试，确认失败**

Run: `PYTHONPATH="src;tests" python -m unittest test_foundation.FoundationTests.test_config_failure_budget_parsed test_foundation.FoundationTests.test_config_failure_budget_and_max_steps_defaults test_foundation.FoundationTests.test_config_failure_budget_validated test_chat.ChatTests.test_failure_budget_flows_through_chat_session -v`
Expected: FAIL —— 配置用例报 `AttributeError: 'Settings' object has no attribute 'failure_budget'` 或 `max_steps` 为 8；端到端用例报 `TypeError: ChatSession.__init__() takes ... positional arguments`（第 6 个位置参数不被接受）或 `runtime.calls` 不为 2。

- [ ] **Step 4: 实现 `config.py` 改动**

4a. `Settings` 字段：把 `max_steps: int = 8` 改为 `max_steps: int = 16`；在字段表末尾（`data_roots: tuple[Path, ...] = ()` 之后）新增：

```python
    data_roots: tuple[Path, ...] = ()
    failure_budget: int = 3
```

4b. `Settings.load` 内：把 `steps = agent.get("max_steps", 8)` 改为 `steps = agent.get("max_steps", 16)`；在 `steps` 校验之后新增 `failure_budget` 解析与校验：

```python
        failure_budget = agent.get("failure_budget", 3)
        if type(failure_budget) is not int or not 1 <= failure_budget <= 20:
            raise ValueError("failure_budget must be an integer between 1 and 20")
```

4c. `Settings.load` 的 `return cls(...)`：在最后一个位置参数 `tuple(resolve(item) for item in roots)` 之后追加 `failure_budget`，使其对应新字段顺序：

```python
        return cls(resolve(agent.get("workspace", "workspace")),
                   resolve(executable) if executable else None,
                   arcgis.get("expected_version", "3.5.2"), timeout, steps,
                   name, base_url.rstrip("/"), model_timeout, thinking, max_tokens, model.get("provider", "deepseek"),
                   resolve(agent["skills_dir"]) if agent.get("skills_dir") else None,
                   tuple(resolve(item) for item in roots),
                   failure_budget)
```

- [ ] **Step 5: 实现 `chat.py` 改动**

5a. `ChatSession.__init__`（`src/arcgis_agent/chat.py:80-82`）增 `failure_budget` 参数并保存：

```python
    def __init__(self, client: DeepSeekClient, runtime: Runtime, workspace: Path, max_steps: int = 8,
                 memory: MemoryStore | None = None, failure_budget: int = 3):
        self.client, self.runtime, self.workspace, self.max_steps = client, runtime, workspace, max_steps
        self.failure_budget = failure_budget
        self.memory = memory if memory is not None else MemoryStore(Path(workspace) / "memory.json")
```

（其余原有初始化行保持不变。）

5b. 构造 `Agent`（`src/arcgis_agent/chat.py:227`）透传预算：

```python
            result = Agent(self.runtime, self.workspace, self.max_steps, self.failure_budget).run(planner, allow_writes=allow_writes,
                on_event=emit, cancel_event=getattr(self.client, "cancel_event", None), approve=approve)
```

- [ ] **Step 6: 实现 `interfaces.py` 改动（`src/arcgis_agent/interfaces.py:41`）**

```python
    session = ChatSession(client, runtime, settings.workspace, settings.max_steps, memory=memory,
                          failure_budget=settings.failure_budget)
```

- [ ] **Step 7: 更新 `config/agent.example.toml`**

把第 10 行 `max_steps = 8` 改为 `max_steps = 16`，并在其下新增注释示例：

```toml
max_steps = 16
# Optional: stop an LLM run after this many consecutive failed tool calls
# (the model self-corrects between failures). Integer 1..20; default 3.
# failure_budget = 3
```

- [ ] **Step 8: 运行新测试，确认通过**

Run: `PYTHONPATH="src;tests" python -m unittest test_foundation.FoundationTests.test_config_failure_budget_parsed test_foundation.FoundationTests.test_config_failure_budget_and_max_steps_defaults test_foundation.FoundationTests.test_config_failure_budget_validated test_chat.ChatTests.test_failure_budget_flows_through_chat_session -v`
Expected: PASS（4 项全过）。

- [ ] **Step 9: 运行全量套件，确认无回归**

Run: `PYTHONPATH="src" python -m unittest discover -s tests -v`
Expected: PASS。特别确认 `test_config_paths_relative_to_config_file`（显式 `max_steps = 3` → 3）、`test_config_limits_validated`（`max_steps = 0` → ValueError）仍通过；总数 293 + 4 = 297。

- [ ] **Step 10: 提交**

```bash
git add src/arcgis_agent/config.py src/arcgis_agent/chat.py src/arcgis_agent/interfaces.py config/agent.example.toml tests/test_foundation.py tests/test_chat.py
git commit -m "feat: failure_budget 可配置并接入会话，max_steps 默认 8→16

Settings 新增 failure_budget(默认3, 1..20 校验)，经 ChatSession 透传给 Agent；
max_steps 默认提高到 16 以支持更长多步任务。端到端测试验证预算生效。"
```

---

### Task 3: 提示词纪律（先规划 / 失败反思 / 更强自主）

**Files:**
- Modify: `src/arcgis_agent/prompts/system.md:12`（理解任务）、`:15`（逐步执行）、`:25`（失败处理）
- Test: `tests/test_system_prompt.py:14-33`（`REQUIRED_MARKERS`）

**Interfaces:**
- Consumes: 既有 `SYSTEM`（deepseek.py 导入时读取 system.md）、`REQUIRED_MARKERS` 契约测试。
- Produces: system.md 含 4 个新标记子串：`不要反问用户`、`说明执行计划`、`不同的纠正动作`、`绝不把技术错误`；长度仍 `< 8000`。

- [ ] **Step 1: 写失败测试（在 `tests/test_system_prompt.py` 的 `REQUIRED_MARKERS` 元组末尾、`"绝不把密钥"` 之后追加）**

```python
    "绝不把密钥",                     # never persist secrets into memory
    "不要反问用户",                   # autonomy: resolve technical uncertainty yourself
    "说明执行计划",                   # plan before multi-step execution
    "不同的纠正动作",                 # reflect on failure, change approach
    "绝不把技术错误",                 # never hand a technical error back to the user
)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `PYTHONPATH="src;tests" python -m unittest test_system_prompt.SystemPromptContract.test_prompt_carries_all_rule_markers -v`
Expected: FAIL —— `AssertionError: missing rule marker: 不要反问用户`（其余 3 个标记同样缺失）。

- [ ] **Step 3: 编辑 `system.md` 第 12 行（理解任务，强化自主）**

原：

```markdown
1. **理解任务**：明确用户要什么数据、什么分析、什么成果。字段名、坐标系等信息从数据探查中获取；只有无法从数据推断的业务取舍（如分析范围、方法选择）才询问用户。
```

改为：

```markdown
1. **理解任务**：明确用户要什么数据、什么分析、什么成果。技术性不确定（路径、字段名、坐标系、参数取值）一律自行用只读探查工具消解，不要反问用户；只有无法从数据推断的业务取舍（如分析范围、方法选择、是否覆盖已有成果）才询问用户。
```

- [ ] **Step 4: 编辑 `system.md` 第 15 行（逐步执行，加入先规划）**

原：

```markdown
4. **逐步执行**：多步任务用上一步工具返回的真实输出路径作为下一步输入，不得编造或简化路径。每步都是独立授权。
```

改为：

```markdown
4. **逐步执行**：多步任务先用一两句话说明执行计划，再开始调用工具；用上一步工具返回的真实输出路径作为下一步输入，不得编造或简化路径。每步都是独立授权。
```

- [ ] **Step 5: 编辑 `system.md` 第 25 行（失败处理，强化反思）**

原：

```markdown
- 工具失败时阅读返回的 `error` 与 `hint`，按提示修正后重试；不要用相同参数盲目重试。失败或取消后此前已生成的成果保留，报告时如实说明哪些步骤未完成。
```

改为：

```markdown
- 工具失败时阅读返回的 `error` 与 `hint`，诊断根因后采取不同的纠正动作，不要用相同参数盲目重试；事实不足时先用 `describe`、`find_datasets`、`read_rows` 等只读工具补齐信息再重试，绝不把技术错误原样抛回让用户自行解决。失败或取消后此前已生成的成果保留，报告时如实说明哪些步骤未完成。
```

- [ ] **Step 6: 运行提示词契约测试，确认通过且在预算内**

Run: `PYTHONPATH="src;tests" python -m unittest test_system_prompt -v`
Expected: PASS —— `test_prompt_carries_all_rule_markers`（含 4 个新标记）、`test_prompt_file_exists_and_is_loaded_verbatim`（`SYSTEM` 与文件一致）、`test_prompt_stays_within_token_budget`（`len(SYSTEM) < 8000`）、`test_prompt_has_no_todo_markers` 全过。

- [ ] **Step 7: 提交**

```bash
git add src/arcgis_agent/prompts/system.md tests/test_system_prompt.py
git commit -m "feat: 提示词强化先规划/失败反思/更强自主

多步任务先说明执行计划；失败后诊断根因采取不同纠正动作、用只读工具补齐事实，
绝不把技术错误抛回用户；技术不确定自行消解，仅业务取舍才询问。授权边界不变。"
```

---

### Task 4: 文档与变更日志 + 全量验证

**Files:**
- Modify: `README.md:167`（状态语义）、`README.md:193`（agent.py 树注释）、`README.md:221`（测试计数与覆盖）
- Modify: `README.en.md:193`（agent.py tree comment）、`README.en.md` 状态/计数对应处
- Modify: `docs/architecture.md:29`（agent.py 行）、`docs/architecture.md:50`（授权段补一句）、`docs/architecture.md:70`（状态语义）
- Modify: `CHANGELOG.md`（顶部新增 2026-11-06 条目）
- Test: 全量离线套件

**Interfaces:**
- Consumes: Task 1-3 的最终行为与测试总数。
- Produces: 文档与实现一致；CHANGELOG 记录本批改动。

- [ ] **Step 1: 运行全量套件，记录准确总数**

Run: `PYTHONPATH="src" python -m unittest discover -s tests -v`
Expected: PASS。记下末尾 `Ran N tests` 的 N（预计 297），用于 Step 2/3 的测试计数。

- [ ] **Step 2: 更新 `README.md`**

2a. 第 167 行状态语义，原句：

```markdown
日志时间使用 UTC。离线模拟的工具观察带 `simulated: true`，不会生成假 GDB。错误、缺少写入许可、步数超限分别返回 `failed`、`blocked`、`step_limit`。拒绝、缺少授权处理器、面板授权超时分别返回 `denied`、`approval_required`、`approval_expired`；取消返回 `cancelled`。以上未完成状态的命令行退出码非零。
```

改为（补充失败自纠与预算耗尽语义）：

```markdown
日志时间使用 UTC。离线模拟的工具观察带 `simulated: true`，不会生成假 GDB。LLM 会话（聊天 / 面板）工具失败后会把错误与 hint 回流模型自纠重试，连续失败达 `failure_budget`（默认 3）才返回 `failed`；确定性计划（CLI `run` / 工具箱）失败即停。缺少写入许可返回 `blocked`，步数超限返回 `step_limit`。拒绝、缺少授权处理器、面板授权超时分别返回 `denied`、`approval_required`、`approval_expired`；取消返回 `cancelled`。以上未完成状态的命令行退出码非零。
```

2b. 第 193 行树注释，原：

```markdown
│   ├── agent.py              # 规划-执行循环与步数上限
```

改为：

```markdown
│   ├── agent.py              # 规划-执行循环、步数上限与失败自纠预算
```

2c. 第 221 行测试计数与覆盖：把开头的 `287 项离线测试` 改为 Step 1 记录的实际总数（预计 `297 项离线测试`），并在覆盖列表末尾的 `…记忆注入与会话恢复。` 之前插入 `失败自纠与连续失败预算、` （即新增覆盖项描述）。最终该句形如：

```markdown
297 项离线测试覆盖类型化工具参数、逐次授权、单次写入许可、拒绝/取消/断线、授权响应重放、有界循环、任务留痕、worker 协议异常与恢复、多模型协议与子进程协议、出图流程、出图范围单位与坐标系一致性、只读导出、技能包校验、工具事件步骤编号、worker 进度流与启动停滞看门狗、自主数据发现、长期记忆增删去重与上限、未完成轮次的历史闭合、记忆注入与会话恢复、失败自纠与连续失败预算。系统 Python 3.14 和 Pro Python 3.11 均已通过；[CI](.github/workflows/tests.yml) 配置相同版本矩阵，云端不依赖 ArcGIS。
```

- [ ] **Step 3: 更新 `README.en.md`**

3a. 第 193 行树注释，原：

```markdown
│   ├── agent.py              # plan-execute loop and step ceiling
```

改为：

```markdown
│   ├── agent.py              # plan-execute loop, step ceiling and self-correction budget
```

3b. 找到英文版与 README.md:167 对应的状态语义段落（描述 `failed`/`blocked`/`step_limit` 的句子），按 2a 的同等含义补一句：LLM 会话失败后回流模型自纠、连续失败达 `failure_budget`（默认 3）才 `failed`，确定性计划失败即停。

3c. 找到英文版测试计数句（`287 offline tests ...`），把计数改为 Step 1 的实际总数（预计 297），并在覆盖列表末尾补 `failure self-correction and consecutive-failure budget`。

- [ ] **Step 4: 更新 `docs/architecture.md`**

4a. 第 29 行 agent.py 行，原：

```markdown
| `agent.py` | Planner 协议、JSON 计划校验、观察循环、任务记录 |
```

改为：

```markdown
| `agent.py` | Planner 协议（含 `recovers` 能力标志）、JSON 计划校验、观察循环、失败自纠与连续失败预算、任务记录 |
```

4b. 第 50 行授权段末尾追加一句（强调纠错不绕过授权）：

```markdown
失败自纠只作用于"工具已执行且返回 ok=False"：LLM planner（`recovers=True`）把失败观测与 hint 回流模型自纠，连续失败达 `failure_budget` 才终止；用户拒绝、取消、写入未授权与调度异常始终立即终止，纠错绝不绕过逐次授权。确定性 `PlanPlanner`（`recovers=False`）失败即停。
```

4c. 第 70 行状态语义，原：

```markdown
`completed`：规划器结束且已执行的工具成功；`failed`：工具或调度异常；`blocked`：请求写入但缺少写许可；`denied`：用户拒绝本步；`approval_required`：入口未提供授权处理器；`approval_expired`：面板授权等待超时；`cancelled`：用户停止或断线；`step_limit`：规划器在预算耗尽后仍请求工具。任务会写入最终快照；日志写入本身不可用时命令失败，不伪造可审计成功。
```

改为（`failed` 补充预算耗尽语义）：

```markdown
`completed`：规划器结束且已执行的工具成功；`failed`：工具或调度异常，或 LLM 会话连续失败达 `failure_budget`（默认 3）；`blocked`：请求写入但缺少写许可；`denied`：用户拒绝本步；`approval_required`：入口未提供授权处理器；`approval_expired`：面板授权等待超时；`cancelled`：用户停止或断线；`step_limit`：规划器在步数上限（默认 16）耗尽后仍请求工具。任务会写入最终快照；日志写入本身不可用时命令失败，不伪造可审计成功。
```

- [ ] **Step 5: 在 `CHANGELOG.md` 顶部（第 4 行 `## 2026-11-06：上下文与长期记忆…` 之前）新增条目**

```markdown
## 2026-11-06：Agent 失败自纠与更长多步任务（控制流修复 + 可配置预算 + 提示词纪律）

- 问题：与商业 Agent 相比"不够聪明"，工具一失败就把问题抛回用户。根因是控制流而非模型能力——`agent.py` 循环在任何 `not result["ok"]` 时立即 `break` 标记 `failed`，而 `DeepSeekPlanner.next_action` 早已会把失败观测（含 `error`/`hint`/`stderr`）作为 tool 消息回流模型并重新请求；`hints.py` 也已为常见 ArcPy 错误附纠正提示。自我纠错接线本就存在，只是循环在模型有机会纠错前就退出了。
- 失败自纠（`agent.py`、`deepseek.py`）：给 planner 增加 `recovers` 能力标志——`PlanPlanner.recovers=False`（CLI 静态计划与 Pro 工具箱保持 fail-fast，`test_failed_tool_stops_following_steps` 的 `runtime.calls==1` 不变），`DeepSeekPlanner.recovers=True`。`Agent.__init__` 新增 `failure_budget=3`；循环用 `getattr(planner,"recovers",False)` 分流：成功观测清零连续失败计数；失败且可纠错时不 break，累加计数并继续循环让模型自纠，连续失败达 `failure_budget` 才置 `failed` 并写 `summary["error"]={type,message[,hint]}`、发 `recovery_exhausted` 事件。`deny`/`cancel`/`expired`/写未授权 `blocked`/`cancel_event`/循环内异常仍立即终止——纠错绝不绕过逐次授权。
- 更长多步任务（`config.py`）：`Settings.max_steps` 默认 8→16（校验仍 1..100）；新增 `failure_budget` 字段（默认 3，校验 `type is int and 1..20`），`Settings.load` 的 `cls(...)` 位置参数末尾追加。`ChatSession.__init__` 新增 `failure_budget=3` 并透传给 `Agent`；`interfaces.create_session` 传 `settings.failure_budget`；`config/agent.example.toml` 更新 `max_steps=16` 并加注释化 `failure_budget` 示例。
- 提示词纪律（`prompts/system.md`，仅提示词、不引入结构化预规划阶段）：理解任务——技术性不确定一律自行用只读探查工具消解、不要反问用户，仅无法推断的业务取舍才询问；逐步执行——多步任务先用一两句话说明执行计划再调用工具；失败处理——诊断根因后采取不同的纠正动作、事实不足先用 `describe`/`find_datasets`/`read_rows` 补齐、绝不把技术错误原样抛回用户。授权边界一字未改。
- 验证：`tests/test_foundation.py` 新增 9 项（纠错后继续并完成、连续失败达预算终止且 `runtime.calls==failure_budget`、成功清零计数、纠错 planner 下 deny/blocked 仍立即终止、`PlanPlanner.recovers`/`DeepSeekPlanner.recovers` 标志、`failure_budget` 解析/默认/越界校验、`max_steps` 默认 16）；`tests/test_chat.py` 新增 1 项端到端（预算经 `ChatSession`→`Agent` 生效，`DeepSeekPlanner` 自纠，连续失败 2 次终止）；`test_system_prompt.py` 新增 4 个规则标记（不要反问用户 / 说明执行计划 / 不同的纠正动作 / 绝不把技术错误）。`PYTHONPATH=src python -m unittest discover -s tests` 全量 **297 项通过**（原 287 + 新增 10）；工具总数契约仍 62；提示词 < 8000 字符。
- 限制：本轮全部离线验证，未消耗 API 用量做真实模型端到端自纠观察，纠错质量取决于模型对 hint 的遵循度；预算耗尽仍以 `failed` 终止（但已给出可操作的最后错误与 hint）；`Timeout`/`Stalled` 作为普通失败回流，重试可能累计较长等待（默认 180s × 最多 `failure_budget` 次），用户可随时取消；`max_steps` 提高意味着长任务最多 16 次逐次授权提示（依"保留逐次授权"的选择）；真机 ArcPy 冒烟未在离线套件内，如需按 AGENTS.md 用 Pro Python 跑 `scripts/smoke_arcpy.py`（仅写合成数据于 `workspace/smoke`）。

```

- [ ] **Step 6: 运行全量套件最终确认**

Run: `PYTHONPATH="src" python -m unittest discover -s tests -v`
Expected: PASS，总数与 Step 1 记录一致（预计 297），无回归。

- [ ] **Step 7: 提交**

```bash
git add README.md README.en.md docs/architecture.md CHANGELOG.md
git commit -m "docs: 记录 Agent 失败自纠、可配置预算与提示词纪律

README/README.en/architecture 更新状态语义、agent.py 职责与测试计数；
CHANGELOG 新增 2026-11-06 条目（行为、模块、验证、限制）。"
```

---

## Self-Review

**1. Spec coverage（逐节对照规格）：**
- §3.1 planner 能力标志 → Task 1 Step 3a/4 + `test_planner_recovery_capability_flags`。✓
- §3.2 失败分支改写（ok 清零 / recovers 分流 / 预算耗尽）→ Task 1 Step 3d + 三个纠错测试。✓
- §3.3 保持终止条件（deny/cancel/blocked/异常）→ Task 1 deny/blocked 测试；cancel/异常路径未改动（既有 `test_runtime_exception_is_persisted` 守护）。✓
- §4 组件清单（agent/deepseek/config/chat/interfaces/system.md）→ Task 1（agent/deepseek）、Task 2（config/chat/interfaces/agent.example.toml）、Task 3（system.md）。✓
- §5 数据流（成功纠错/预算耗尽/步数耗尽/Timeout）→ Task 1 测试覆盖成功纠错与预算耗尽；步数耗尽由既有 `test_planner_cannot_loop_forever` 守护；Timeout 作为普通失败回流（无需新代码，规格已说明）。✓
- §6 提示词纪律（先规划/失败反思/更强自主）→ Task 3 三处编辑 + 4 标记。✓
- §7 错误处理（summary["error"] 形状、recovery_exhausted 事件）→ Task 1 Step 3d。✓
- §8 测试与验证（foundation/system_prompt/approvals 计数、文档、CHANGELOG）→ Task 1/2/3 测试 + Task 4 文档；`len(TOOLS)` 保持 62（无新工具，`test_approvals.py` 无需改）。✓
- §9 验收标准 1-5 → 分别由 Task 1（自纠/fail-fast/安全终止）、Task 2（预算/步数）、Task 3+4（提示词/文档/全量绿）覆盖。✓

**2. Placeholder scan：** 无 TBD/TODO；每个代码步骤均含完整可粘贴代码；文档步骤给出原句与改后句。测试计数处明确要求"以 Step 1 套件打印的实际 N 为准（预计 297）"，非占位而是可验证指令。✓

**3. Type consistency：**
- `Agent(runtime, workspace, max_steps=8, failure_budget=3)` 在 Task 1 定义，Task 2 的 `ChatSession`/`create_session` 与端到端测试按相同位置/关键字使用。✓
- `recovers` 类属性：`PlanPlanner.recovers=False`、`DeepSeekPlanner.recovers=True`，agent 用 `getattr(planner,"recovers",False)` 读取，测试断言一致。✓
- `Settings.failure_budget`（默认 3，字段在 `data_roots` 之后）与 `cls(...)` 末尾位置参数一致；`ChatSession(..., failure_budget=3)` 与 `interfaces` 关键字传参一致。✓
- `summary["error"]` 字典键 `type`/`message`/`hint` 在实现与测试（`result["error"]["type"]`、`["message"]`、`["hint"]`）一致。✓
- 4 个提示词标记子串（`不要反问用户`/`说明执行计划`/`不同的纠正动作`/`绝不把技术错误`）与 Task 3 Step 3/4/5 写入的文本逐字匹配。✓

无遗漏，无矛盾。计划可直接执行。
