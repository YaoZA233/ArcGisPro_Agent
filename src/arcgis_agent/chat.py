from __future__ import annotations

import copy
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .agent import Agent
from .attachments import prepare_attachments
from .deepseek import DeepSeekClient, DeepSeekPlanner
from .memory import MemoryStore, memory_block
from .runtime import Runtime

PRIOR_IMAGE_BUDGET = 16 * 1024 * 1024

NOT_EXECUTED_NOTES = {
    "denied": "用户拒绝了本次调用，工具未执行。",
    "cancelled": "任务已取消，本次调用未执行或已中断。",
    "approval_expired": "授权等待超时，本次调用未执行。",
    "approval_required": "当前入口未提供授权处理器，本次调用未执行。",
    "blocked": "写操作未获授权，本次调用未执行。",
    "step_limit": "已达本次工具调用上限，本次调用未执行。",
    "failed": "工具调用失败，任务已停止。",
}


def _dangling_tool_calls(messages: list[dict]) -> list[str]:
    """Call IDs from the trailing assistant tool_calls message that never got a tool reply."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            replied = {item.get("tool_call_id") for item in messages[index + 1:] if item.get("role") == "tool"}
            return [call.get("id") for call in message["tool_calls"]
                    if isinstance(call.get("id"), str) and call.get("id") not in replied]
        if message.get("role") == "user":
            return []
    return []


def _closed_history(planner, result: dict) -> list[dict]:
    """Close a turn so the API sequence stays valid: every tool_call gets a tool reply.

    A failed tool's real observation is attached when the planner never consumed it;
    calls that never ran get a structured not-executed result naming the reason.
    """
    messages = list(planner.messages)
    dangling = _dangling_tool_calls(messages)
    observations = result.get("observations") or []
    seen = getattr(planner, "seen_observations", 0)
    for position, call_id in enumerate(dangling):
        if position == 0 and len(observations) > seen:
            content = observations[-1]
        else:
            note = NOT_EXECUTED_NOTES.get(result.get("status"), "本次调用未执行。")
            error = result.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str) and error["message"]:
                note = error["message"]
            content = {"ok": False, "error": {"type": "NotExecuted", "message": note}}
        messages.append({"role": "tool", "tool_call_id": call_id,
                         "content": json.dumps(content, ensure_ascii=False)})
    return messages[1:]


def usage_numbers(usage: dict) -> dict:
    def number(key):
        value = usage.get(key, 0)
        return value if type(value) is int and value >= 0 else 0
    detail = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
    cache = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    return {"input": number("prompt_tokens"), "output": number("completion_tokens"),
            "cache_hit": number("prompt_cache_hit_tokens") if "prompt_cache_hit_tokens" in usage else (cache if type(cache) is int and cache >= 0 else 0),
            "reasoning": detail if type(detail) is int and detail >= 0 else 0}


class ChatSession:
    def __init__(self, client: DeepSeekClient, runtime: Runtime, workspace: Path, max_steps: int = 16,
                 memory: MemoryStore | None = None, failure_budget: int = 3):
        self.client, self.runtime, self.workspace, self.max_steps = client, runtime, workspace, max_steps
        self.failure_budget = failure_budget
        self.memory = memory if memory is not None else MemoryStore(Path(workspace) / "memory.json")
        self.history: list[dict] = []
        self.turns: list[dict] = []
        self.session_id = uuid.uuid4().hex
        self.total_usage = {"input": 0, "output": 0, "cache_hit": 0, "reasoning": 0}
        self.history_origin = self._origin()

    def _origin(self):
        profile = getattr(self.client, "profile", {})
        return [profile.get("id", "deepseek"), profile.get("protocol", "openai"),
                getattr(self.client, "base_url", ""), getattr(self.client, "model", "simulated")]

    def reset(self):
        self.history.clear()
        self.turns.clear()
        self.session_id = uuid.uuid4().hex
        self.total_usage = dict.fromkeys(self.total_usage, 0)
        self.history_origin = self._origin()

    def switch_client(self, client):
        previous = self._origin()
        self.client = client
        # Thinking signatures are valid only for their originating provider/model.
        # Retain conversational text and complete tool roundtrips across switches.
        if previous != self._origin():
            for message in self.history:
                message.pop("_anthropic_content", None)
                message.pop("reasoning_content", None)

    def _chat_path(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("会话编号无效。")
        return self.workspace / "chats" / (session_id + ".json")

    def list_chats(self) -> list[dict]:
        folder = self.workspace / "chats"
        files = sorted(folder.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:50]
        chats = []
        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                chats.append({key: data[key] for key in ("session_id", "title", "updated", "total_usage")})
            except (ValueError, OSError, KeyError):
                continue
        return chats

    def load(self, session_id: str) -> dict:
        data = json.loads(self._chat_path(session_id).read_text(encoding="utf-8"))
        history, turns = data.get("history"), data.get("turns")
        if not isinstance(history, list) or not all(isinstance(item, dict) for item in history) \
                or not isinstance(turns, list) or not isinstance(data.get("total_usage"), dict):
            raise ValueError("会话文件的历史记录格式无效，无法恢复；请选择其他会话。")
        self.history, self.turns, self.session_id = history, turns, session_id
        totals = {"input": 0, "output": 0, "cache_hit": 0, "reasoning": 0}
        for key, value in data["total_usage"].items():
            if key in totals and type(value) is int and value >= 0:
                totals[key] = value
        self.total_usage = totals
        self.history_origin = data.get("history_origin")
        return {"session_id": self.session_id, "turns": self.turns, "total_usage": self.total_usage}

    def _save(self):
        path = self._chat_path(self.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        history = copy.deepcopy(self.history)
        for message in history:
            if isinstance(message.get("content"), list):
                message["content"] = [part if part.get("type") != "image_url" else
                    {"type": "text", "text": "[前轮图片已分析；恢复会话后如需重新查看图片，请再次附加。]"}
                    for part in message["content"]]
        data = {"version": 1, "session_id": self.session_id, "title": self.turns[0]["prompt"][:48] if self.turns else "新对话",
                "updated": datetime.now(timezone.utc).isoformat(), "turns": self.turns,
                "history": history, "total_usage": self.total_usage, "history_origin": self.history_origin}
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def send(self, prompt: str, *, allow_writes: bool = False, on_event: Callable[[dict], None] | None = None,
             options: dict | None = None, attachments: list[str] | None = None, context: str = "",
             approve: Callable[[dict], dict] | None = None, approval_mode: str = "ask") -> dict:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("请输入任务内容。")
        if len(prompt) > 16000:
            raise ValueError("单条消息不能超过 16000 字符。")
        real_client = callable(getattr(self.client, "configure", None)) and hasattr(self.client, "cancel_event")
        if real_client:
            self.client.configure(options or {})
        model = getattr(self.client, "model", "simulated")
        thinking = getattr(self.client, "thinking", "off")
        if not isinstance(context, str):
            raise ValueError("工程上下文必须为路径文本。")
        vision = self.client.supports_images() if hasattr(self.client, "supports_images") else None
        content, metadata = prepare_attachments(attachments or [], model, (options or {}).get("image_detail", getattr(self.client, "image_detail", "auto")), vision=vision)
        prior_history = copy.deepcopy(self.history)
        if self.history_origin != self._origin():
            for message in prior_history:
                message.pop("_anthropic_content", None)
                message.pop("reasoning_content", None)
        # Bound retained images independently of text; avoid exceeding the API's
        # request-body limit after repeatedly attaching images in one conversation.
        image_budget = 0 if vision is False else PRIOR_IMAGE_BUDGET
        for message in reversed(prior_history):
            if not isinstance(message.get("content"), list):
                continue
            for index in range(len(message["content"]) - 1, -1, -1):
                part = message["content"][index]
                if part.get("type") != "image_url":
                    continue
                size = len(part.get("image_url", {}).get("url", ""))
                if size <= image_budget:
                    image_budget -= size
                else:
                    message["content"][index] = {"type": "text", "text": "[前轮图片已分析；如需重新查看，请再次附加。]"}
        # Long-term memory rides on the first message of a session; later turns
        # already carry it inside the retained history.
        block = memory_block(self.memory) if not prior_history else ""
        text = prompt.strip() + ("\n" + block if block else "") + \
            ("\n[ArcGIS Pro 已保存工程路径：" + context[:4096] + "]" if context else "")
        user_content = [{"type": "text", "text": text}, *content] if content else text
        planner = DeepSeekPlanner(self.client, prior_history, user_content)
        usage = {"input": 0, "output": 0, "cache_hit": 0, "reasoning": 0}
        model_used = model
        usage_available = False
        reasoning_parts: list[str] = []
        reasoning_size = 0
        started = time.monotonic()

        def emit(event: dict):
            nonlocal model_used, usage_available, reasoning_size
            if event["event"] == "reasoning_delta" and reasoning_size < 200000:
                piece = event.get("text", "")[:200000 - reasoning_size]
                reasoning_parts.append(piece)
                reasoning_size += len(piece)
            if event["event"] == "model_usage":
                model_used = event.get("model") or model
                usage_available |= bool(event.get("usage"))
                for key, value in usage_numbers(event.get("usage") or {}).items():
                    usage[key] += value
            if on_event:
                on_event(event)

        if real_client:
            self.client.on_event = emit
            if hasattr(self.runtime, "cancel_event"):
                self.runtime.cancel_event = self.client.cancel_event
        try:
            result = Agent(self.runtime, self.workspace, self.max_steps, self.failure_budget).run(planner, allow_writes=allow_writes,
                on_event=emit, cancel_event=getattr(self.client, "cancel_event", None), approve=approve,
                approval_mode=approval_mode)
        finally:
            if real_client:
                self.client.on_event = None
        # Every terminal state where the model actually acted keeps its exchange in
        # history: the next turn must know what was attempted and why it did not
        # complete. A turn aborted before any assistant output (e.g. an immediate
        # cancel) has nothing to remember, so prior history is left untouched.
        if any(message.get("role") == "assistant" for message in planner.messages):
            self.history = _closed_history(planner, result)
            self.history_origin = self._origin()
            # Discard entire oldest user turns, never orphan tool messages.
            while len(self.history) > 100 or len(json.dumps(self._text_history(), ensure_ascii=False)) > 180000:
                next_user = next((i for i, m in enumerate(self.history[1:], 1) if m.get("role") == "user"), None)
                if next_user is None:
                    self.history.clear()
                    break
                self.history = self.history[next_user:]
        if result["status"] == "completed":
            reply = planner.final_text
        elif result["status"] == "blocked":
            reply = "执行已暂停：此任务需要创建 GIS 成果。请启用允许写入后重新发送任务。"
        elif result["status"] == "cancelled":
            reply = "已停止本次任务。已创建的部分成果可能保留，请检查任务目录。"
        elif result["status"] == "denied":
            reply = "你已拒绝本次操作，工具未执行，后续步骤已停止。此前已完成的成果保留在任务目录。"
        elif result["status"] in ("approval_required", "approval_expired"):
            reply = "操作尚未执行：需要本次单独授权，或等待授权已超时。请重新发送任务并确认操作。"
        elif result["status"] == "step_limit":
            reply = "已达到本次工具调用上限，任务未完成。请缩小任务范围后重试。"
        else:
            error = result.get("error") or (result["observations"][-1].get("error") if result["observations"] else {})
            reply = "任务未完成：" + (error.get("message", str(error)) if isinstance(error, dict) else str(error))
        for key in usage:
            self.total_usage[key] += usage[key]
        result.update(reply=reply, usage=usage, total_usage=dict(self.total_usage), model=model_used,
                      requested_model=model, thinking=thinking, elapsed_seconds=round(time.monotonic() - started, 2),
                      session_id=self.session_id, attachments=metadata, usage_available=usage_available)
        profile = getattr(self.client, "profile", {})
        result["provider"] = profile.get("id", "deepseek" if real_client else "simulated")
        turn = {"prompt": prompt.strip(), **{k: result[k] for k in ("reply", "status", "model", "thinking", "usage", "run_dir", "elapsed_seconds", "attachments")}}
        turn["reasoning"] = "".join(reasoning_parts)
        turn["observations"] = result["observations"]
        turn["usage_available"] = usage_available
        self.turns.append(turn)
        self._save()
        (Path(result["run_dir"]) / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def _text_history(self):
        return [{**m, "content": [p for p in m["content"] if p.get("type") != "image_url"]}
                if isinstance(m.get("content"), list) else m for m in self.history]
