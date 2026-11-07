from __future__ import annotations

import json
import copy
import hashlib
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from .runtime import Runtime
from .tools import TOOLS, validate_call, approval_details


@dataclass(frozen=True)
class Action:
    tool: str
    arguments: dict


class Planner(Protocol):
    """Future LLM adapters return structured actions through this contract."""
    def next_action(self, observations: list[dict]) -> Action | None: ...


class PlanPlanner:
    """Deterministic plan executor for the foundation, not a natural-language model."""
    recovers = False

    def __init__(self, steps: list[dict]):
        self.actions = [Action(step["tool"], dict(step["arguments"])) for step in steps]

    def next_action(self, observations: list[dict]) -> Action | None:
        return self.actions[len(observations)] if len(observations) < len(self.actions) else None


def validate_plan(plan: dict, max_steps: int) -> list[dict]:
    if not isinstance(plan, dict) or set(plan) != {"version", "steps"} or type(plan["version"]) is not int or plan["version"] != 1:
        raise ValueError("Plan must contain version=1 and steps only")
    steps = plan["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= max_steps:
        raise ValueError(f"Plan must contain 1..{max_steps} steps")
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"tool", "arguments"}:
            raise ValueError("Each step must contain tool and arguments only")
        validate_call(step["tool"], step["arguments"])
    return steps


class Agent:
    def __init__(self, runtime: Runtime, workspace: Path, max_steps: int = 16, failure_budget: int = 3):
        if type(max_steps) is not int or not 1 <= max_steps <= 100:
            raise ValueError("max_steps must be an integer in 1..100")
        if type(failure_budget) is not int or not 1 <= failure_budget <= 20:
            raise ValueError("failure_budget must be an integer in 1..20")
        self.runtime, self.workspace, self.max_steps = runtime, workspace.resolve(), max_steps
        self.failure_budget = failure_budget

    def run(self, planner: Planner, *, allow_writes: bool = False,
            on_event: Callable[[dict], None] | None = None,
            cancel_event: threading.Event | None = None,
            approve: Callable[[dict], dict] | None = None,
            approval_mode: str = "ask") -> dict:
        from .approvals import full_access_approval, validate_approval_mode
        validate_approval_mode(approval_mode)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:12]
        run_dir = self.workspace / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        observations: list[dict] = []
        summary = {"run_id": run_id, "status": "running", "run_dir": str(run_dir), "observations": observations}

        def event(kind: str, data: dict) -> None:
            record = {"time": datetime.now(timezone.utc).isoformat(), "event": kind, **data}
            with (run_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            if on_event:
                on_event(copy.deepcopy(record))

        event("started", {"allow_writes": allow_writes, "approval_mode": approval_mode})
        try:
            recovers = getattr(planner, "recovers", False)
            consecutive_failures = 0
            for index in range(self.max_steps + 1):
                if cancel_event and cancel_event.is_set():
                    summary["status"] = "cancelled"
                    break
                action = planner.next_action(observations)
                if action is None:
                    if observations and not observations[-1]["ok"]:
                        summary["status"] = "failed"
                        summary["error"] = copy.deepcopy(observations[-1].get("error") or {
                            "type": "ToolFailure", "message": "Tool returned ok=False"})
                    else:
                        summary["status"] = "completed"
                    break
                if index == self.max_steps:
                    summary["status"] = "step_limit"
                    break
                action = Action(action.tool, copy.deepcopy(action.arguments))
                validate_call(action.tool, action.arguments)
                if cancel_event and cancel_event.is_set():
                    summary["status"] = "cancelled"
                    break
                event("tool_requested", {"tool": action.tool, "arguments": action.arguments,
                                          "step": index + 1, "approval_mode": approval_mode})
                request = {"request_id": uuid.uuid4().hex, "run_id": run_id, "step": index + 1,
                           "tool": action.tool, "arguments": copy.deepcopy(action.arguments),
                           **approval_details(action.tool, action.arguments, run_dir / "outputs")}
                preview = getattr(type(self.runtime), "approval_context", None)
                if preview is not None:
                    context = preview(self.runtime, action.tool, action.arguments)
                    if context is not None:
                        request["selection"] = copy.deepcopy(context)
                request["digest"] = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                event("approval_requested", request)
                handler = full_access_approval if approval_mode == "full" else approve
                if handler is None:
                    summary["status"] = "approval_required"
                    summary["error"] = "每次工具调用必须由用户单独授权；当前入口未提供授权处理器。"
                    break
                decision = handler(copy.deepcopy(request))
                if not isinstance(decision, dict) or decision.get("decision") not in ("allow", "deny", "cancel", "expired") or type(decision.get("allow_writes", False)) is not bool:
                    raise ValueError("Invalid approval decision; operation was not executed")
                choice = decision["decision"]
                if cancel_event and cancel_event.is_set():
                    choice = "cancel"
                event("approval_decided", {"request_id": request["request_id"], "tool": action.tool, "digest": request["digest"], "decision": choice,
                                            "allow_writes": decision.get("allow_writes", False)})
                if choice != "allow":
                    summary["status"] = {"deny": "denied", "cancel": "cancelled", "expired": "approval_expired"}[choice]
                    break
                if TOOLS[action.tool].writes and not (allow_writes or approval_mode == "full" or decision.get("allow_writes", False)):
                    summary["status"] = "blocked"
                    summary["error"] = "Write tool requires an explicit single-operation write grant or --allow-writes; per-tool approval is always required"
                    break
                if cancel_event and cancel_event.is_set():
                    summary["status"] = "cancelled"
                    break
                event("tool_executing", {"tool": action.tool, "arguments": action.arguments, "request_id": request["request_id"], "step": index + 1})
                result = self.runtime.call(action.tool, action.arguments, run_dir / "outputs",
                                           on_progress=lambda text: event("tool_progress",
                                               {"tool": action.tool, "step": index + 1, "text": text}))
                observation = {"tool": action.tool, "arguments": action.arguments, **result}
                observations.append(observation)
                event("tool_result", {**observation, "step": index + 1})
                last_error = result.get("error") or {}
                if (cancel_event and cancel_event.is_set()) or last_error.get("type") == "Cancelled":
                    summary["status"] = "cancelled"
                    break
                if result["ok"]:
                    consecutive_failures = 0
                    continue
                if not recovers:
                    summary["status"] = "failed"
                    break
                consecutive_failures += 1
                if consecutive_failures >= self.failure_budget:
                    summary["status"] = "failed"
                    summary["error"] = {
                        "type": last_error.get("type", "ToolFailure"),
                        "message": f"连续 {self.failure_budget} 次工具调用失败，已停止自动重试；最后错误：{last_error.get('message', '')}",
                    }
                    if last_error.get("hint"):
                        summary["error"]["hint"] = last_error["hint"]
                    event("recovery_exhausted", {"step": index + 1, "type": summary["error"]["type"]})
                    break
        except Exception as exc:
            summary["status"] = "cancelled" if cancel_event and cancel_event.is_set() else "failed"
            summary["error"] = {"type": type(exc).__name__, "message": str(exc)}
        # Callbacks (including recovery_exhausted) may cancel on the final step.
        if cancel_event and cancel_event.is_set():
            summary["status"] = "cancelled"
        summary["simulated"] = any(item.get("simulated", False) for item in observations)
        event("finished", {"status": summary["status"], "simulated": summary["simulated"]})
        (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
