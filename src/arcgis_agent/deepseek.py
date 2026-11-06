"""DeepSeek Chat Completions adapter; standard library only."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .agent import Action
from .tools import TOOLS

# Single source of truth for the model's operating rules: a bundled markdown file
# that humans edit directly. Missing or unreadable file fails at import (fail fast).
SYSTEM = (Path(__file__).with_name("prompts") / "system.md").read_text(encoding="utf-8")


def tool_schemas(available: set[str] | None = None) -> list[dict]:
    available = available if available is not None else {name for name, item in TOOLS.items() if not item.host}
    return [{"type": "function", "function": {"name": name, "description": tool.description,
             "parameters": tool.schema()}}
            for name, tool in TOOLS.items() if name in available]


def read_api_key(root: Path) -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    path = root / ".env.local"
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "DEEPSEEK_API_KEY":
                return value.strip().strip('"\'')
    raise ValueError("缺少 DEEPSEEK_API_KEY。请运行 .\\scripts\\agent.ps1 setup-key，或设置环境变量。")


class DeepSeekError(RuntimeError):
    pass


class Cancelled(DeepSeekError):
    pass


def iter_sse(response):
    data = []
    for raw in response:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                yield "\n".join(data)
                data.clear()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        yield "\n".join(data)


class DeepSeekClient:
    def __init__(self, api_key: str, model: str = "deepseek-flash", base_url: str = "https://api.deepseek.com", timeout: int = 120):
        self.api_key, self.model, self.base_url, self.timeout = api_key, model, base_url.rstrip("/"), timeout
        self.thinking, self.max_tokens = "high", 8192
        self.on_event = None
        self.cancel_event = threading.Event()
        self.last_usage: dict = {}
        self.actual_model = model
        self.image_detail = "auto"
        # urlopen's timeout covers one blocked read only; a slow-drip stream needs a wall-clock limit.
        self.stream_limit = 900
        self.available_tools = {name for name, item in TOOLS.items() if not item.host}

    def supports_images(self) -> bool:
        return self.model in ("deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp")

    def _payload(self, messages: list[dict], streaming: bool) -> dict:
        payload = {"model": self.model, "messages": messages, "tools": tool_schemas(self.available_tools), "stream": streaming,
                   "max_tokens": self.max_tokens, "thinking": {"type": "disabled" if self.thinking == "off" else "enabled"}}
        if self.thinking != "off":
            payload["reasoning_effort"] = self.thinking
        if streaming:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def configure(self, options: dict) -> None:
        if not isinstance(options, dict) or set(options) - {"model", "thinking", "max_tokens", "image_detail"}:
            raise ValueError("无效的模型设置。")
        model = options.get("model", self.model)
        thinking = options.get("thinking", self.thinking)
        tokens = options.get("max_tokens", self.max_tokens)
        detail = options.get("image_detail", self.image_detail)
        if not isinstance(model, str) or not model.strip() or len(model) > 128 or any(c.isspace() for c in model):
            raise ValueError("模型名称无效。")
        if thinking not in ("off", "low", "high", "max"):
            raise ValueError("思考程度必须为 off / low / high / max。")
        if type(tokens) is not int or not 256 <= tokens <= 65536:
            raise ValueError("输出上限须为 256 至 65536 的整数。")
        if detail not in ("auto", "low", "high"):
            raise ValueError("图片精度必须为 auto / low / high。")
        self.model, self.thinking, self.max_tokens = model, thinking, tokens
        self.image_detail = detail

    def _emit(self, event: str, **data) -> None:
        if self.on_event:
            self.on_event({"event": event, **data})

    def _open(self, request):
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            hints = {400: "请求参数与当前模型不兼容", 401: "密钥无效", 402: "账户余额不足", 429: "请求频率过高"}
            raise DeepSeekError(f"DeepSeek API HTTP {code}：{hints.get(code, '服务暂时不可用')}。") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DeepSeekError(f"无法连接 DeepSeek：{type(exc).__name__}。请检查网络与代理。") from None

    def list_models(self) -> list[str]:
        request = urllib.request.Request(self.base_url + "/models", headers={"Authorization": "Bearer " + self.api_key})
        with self._open(request) as response:
            body = json.load(response)
        return sorted({item["id"] for item in body.get("data", []) if isinstance(item.get("id"), str)})

    def complete(self, messages: list[dict]) -> dict:
        if self.cancel_event.is_set():
            raise Cancelled("用户已停止生成。")
        streaming = self.on_event is not None
        self.last_usage = {}
        self.actual_model = self.model
        payload = self._payload(messages, streaming)
        request = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}, method="POST")
        self._emit("model_started", model=self.model, thinking=self.thinking)
        with self._open(request) as response:
            if streaming:
                return self._stream(response)
            body = json.load(response)
        self.actual_model = body.get("model") or self.model
        self.last_usage = body.get("usage") or {}
        try:
            message = body["choices"][0]["message"]
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError("invalid assistant message")
            if body["choices"][0].get("finish_reason") == "length":
                raise DeepSeekError("模型达到输出上限；请提高上限或缩小任务。")
            if body["choices"][0].get("finish_reason") not in (None, "stop", "tool_calls"):
                raise DeepSeekError("模型未正常完成本次响应，未执行不完整的工具。")
            return message
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise DeepSeekError("DeepSeek 返回格式不符合预期。") from exc

    def _stream(self, response) -> dict:
        message = {"role": "assistant", "content": ""}
        tools: dict[int, dict] = {}
        finished = False
        deadline = time.monotonic() + self.stream_limit
        for data in iter_sse(response):
            if self.cancel_event.is_set():
                raise Cancelled("用户已停止生成。")
            if time.monotonic() >= deadline:
                raise DeepSeekError("模型流式响应超过总时长限制，已停止；未执行不完整的工具。")
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                raise DeepSeekError("模型流返回了无效 JSON。") from None
            if chunk.get("error"):
                raise DeepSeekError("DeepSeek 在流式请求中返回服务错误。")
            if chunk.get("model"):
                self.actual_model = chunk["model"]
            if chunk.get("usage"):
                self.last_usage = chunk["usage"]
            for choice in chunk.get("choices", []):
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta") or {}
                for field, event in (("content", "content_delta"), ("reasoning_content", "reasoning_delta")):
                    value = delta.get(field)
                    if value:
                        if not isinstance(value, str):
                            raise DeepSeekError("模型流返回了无效文本。")
                        message[field] = message.get(field, "") + value
                        self._emit(event, text=value)
                for tool in delta.get("tool_calls") or []:
                    index = tool.get("index", 0)
                    if type(index) is not int or not 0 <= index < 32:
                        raise DeepSeekError("工具调用索引超出范围。")
                    item = tools.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if tool.get("id"):
                        item["id"] += tool["id"]
                    for key in ("name", "arguments"):
                        item["function"][key] += (tool.get("function") or {}).get(key) or ""
                if choice.get("finish_reason"):
                    if choice["finish_reason"] == "length":
                        raise DeepSeekError("模型达到输出上限；请提高上限或缩小任务。")
                    if choice["finish_reason"] not in ("stop", "tool_calls"):
                        raise DeepSeekError("模型未正常完成本次响应。")
                    finished = True
        if self.cancel_event.is_set():
            raise Cancelled("用户已停止生成。")
        if not finished:
            raise DeepSeekError("模型流提前中断，未执行不完整的工具调用。")
        if tools:
            message["tool_calls"] = [tools[i] for i in sorted(tools)]
        return message


class DeepSeekPlanner:
    recovers = True

    def __init__(self, client: DeepSeekClient, history: list[dict], prompt):
        self.client = client
        self.messages = [{"role": "system", "content": SYSTEM}, *history, {"role": "user", "content": prompt}]
        self.pending: list[dict] = []
        self.active_call: str | None = None
        self.final_text = ""
        self.seen_observations = 0

    def next_action(self, observations: list[dict]) -> Action | None:
        if self.active_call is not None:
            if len(observations) != self.seen_observations + 1:
                raise DeepSeekError("工具结果与调用不匹配。")
            self.messages.append({"role": "tool", "tool_call_id": self.active_call,
                                  "content": json.dumps(observations[-1], ensure_ascii=False)})
            self.seen_observations += 1
            self.active_call = None
            if not observations[-1]["ok"]:
                # The rest of this batch may depend on the failed call. Close its
                # IDs without dispatching stale actions, then ask the model again.
                for call in self.pending:
                    self.messages.append({"role": "tool", "tool_call_id": call["id"],
                                          "content": json.dumps({"ok": False, "error": {
                                              "type": "NotExecuted",
                                              "message": "前序工具调用失败，本批次后续调用未执行；请根据失败结果重新规划。",
                                          }}, ensure_ascii=False)})
                self.pending.clear()
        if not self.pending:
            message = self.client.complete(self.messages)
            if hasattr(self.client, "_emit"):
                self.client._emit("model_usage", model=self.client.actual_model, usage=self.client.last_usage)
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list):
                raise DeepSeekError("工具调用格式无效。")
            self.messages.append(message)
            if not calls:
                self.final_text = message.get("content") or "模型未返回文本。"
                return None
            self.pending = calls.copy()
        call = self.pending.pop(0)
        try:
            if call["type"] != "function" or not isinstance(call["id"], str):
                raise ValueError("invalid tool call")
            function = call["function"]
            arguments = json.loads(function["arguments"])
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be object")
            self.active_call = call["id"]
            return Action(function["name"], arguments)
        except (KeyError, TypeError, ValueError) as exc:
            raise DeepSeekError("模型返回的工具调用参数无效。") from exc
