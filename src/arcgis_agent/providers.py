"""Provider profiles and protocol adapters, implemented with the standard library.

Messages inside the agent use the Chat Completions shape. Native wire formats
are converted at this boundary; native Claude blocks retain thinking signatures.
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .deepseek import Cancelled, DeepSeekClient, DeepSeekError, iter_sse, tool_schemas


PRESETS = {
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-flash", "reasoning": "deepseek", "vision": True},
    "qwen": {"name": "Qwen / 通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "reasoning": "qwen"},
    "kimi": {"name": "Kimi / Moonshot", "base_url": "https://api.moonshot.cn/v1", "model": "kimi-k2.5", "reasoning": "kimi", "vision": True},
    "glm": {"name": "GLM / 智谱", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4.7", "reasoning": "glm"},
    "openai": {"name": "OpenAI / GPT", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1", "reasoning": "none", "vision": True},
    "claude": {"name": "Anthropic / Claude", "base_url": "https://api.anthropic.com/v1", "model": "claude-sonnet-4-5", "protocol": "anthropic", "reasoning": "anthropic", "vision": True},
    "grok": {"name": "xAI / Grok", "base_url": "https://api.x.ai/v1", "model": "grok-4", "reasoning": "none"},
    "custom": {"name": "自定义 / 中转站", "base_url": "https://example.com/v1", "model": "your-model", "reasoning": "none"},
}
REASONING = ("none", "deepseek", "qwen", "kimi", "glm", "openai", "anthropic")


def validate_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        raise ValueError("接口地址无效。")
    try:
        url = urllib.parse.urlparse(value)
        port = url.port
    except ValueError:
        raise ValueError("接口地址或端口无效。") from None
    local = url.hostname in ("localhost", "127.0.0.1", "::1")
    if (url.scheme != "https" and not (local and url.scheme == "http")) or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("接口地址须为 HTTPS，不能包含凭据、查询参数或片段；仅本机允许 HTTP。")
    return value.rstrip("/")


def preset_profile(preset: str, ident: str | None = None) -> dict:
    if preset not in PRESETS:
        raise ValueError("未知供应商预设。")
    return {"id": ident or preset, "preset": preset, "protocol": "openai", "vision": False,
            "tools": True, "stream_usage": True, **PRESETS[preset]}


def validate_profile(profile: dict) -> dict:
    fields = {"id", "preset", "name", "protocol", "base_url", "model", "reasoning", "vision", "tools", "stream_usage"}
    if not isinstance(profile, dict) or set(profile) - fields:
        raise ValueError("连接配置包含无效字段。")
    ident = profile.get("id")
    if not isinstance(ident, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", ident):
        raise ValueError("连接 ID 须以小写字母开头，最多 48 位，仅含字母、数字、下划线和连字符。")
    value = {**preset_profile(profile.get("preset", "custom"), ident), **profile}
    if value["protocol"] not in ("openai", "anthropic") or value["reasoning"] not in REASONING:
        raise ValueError("协议或推理参数模式无效。")
    if (value["protocol"] == "anthropic") != (value["reasoning"] == "anthropic") and value["reasoning"] != "none":
        raise ValueError("推理参数模式与接口协议不一致。")
    for key in ("name", "model"):
        if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 128 or any(ord(c) < 32 for c in value[key]):
            raise ValueError("连接名称或模型名称无效。")
    if any(c.isspace() for c in value["model"]):
        raise ValueError("模型 ID 不能包含空白字符。")
    for key in ("vision", "tools", "stream_usage"):
        if type(value[key]) is not bool:
            raise ValueError("模型能力必须为布尔值。")
    value["base_url"] = validate_url(value["base_url"])
    return value


def key_name(ident: str) -> str:
    return {"deepseek": "DEEPSEEK_API_KEY", "qwen": "DASHSCOPE_API_KEY", "kimi": "MOONSHOT_API_KEY",
            "glm": "ZHIPU_API_KEY", "openai": "OPENAI_API_KEY", "claude": "ANTHROPIC_API_KEY", "grok": "XAI_API_KEY"}.get(
                ident, "ARCGIS_AGENT_KEY_" + ident.encode("ascii").hex().upper())


def get_key(root: Path, ident: str) -> str:
    name = key_name(ident)
    value = os.environ.get(name, "").strip()
    if not value:
        path = root / ".env.local"
        if path.is_file():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                key, sep, raw = line.partition("=")
                if sep and key.strip() == name:
                    value = raw.strip().strip('"\'')
    if any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError("本地 API 密钥格式无效，请重新配置。")
    return value


def save_key(root: Path, ident: str, key: str) -> None:
    if not isinstance(key, str) or not key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in key.strip()):
        raise ValueError("密钥为空或包含无效字符。")
    name = key_name(ident)
    path = root / ".env.local"
    if path.is_symlink():
        raise ValueError("密钥文件不能是符号链接。")
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    lines = [line for line in lines if line.partition("=")[0].strip() != name]
    lines.append(name + "=" + key.strip())
    temp = path.with_name(".env.local.tmp")
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temp.replace(path)


class ProviderStore:
    def __init__(self, root: Path, workspace: Path, settings):
        self.root, self.path = root, workspace / "providers.json"
        self.profiles = {name: preset_profile(name) for name in PRESETS if name != "custom"}
        self.profiles["deepseek"].update(model=settings.model, base_url=settings.base_url)
        self.active = getattr(settings, "provider", "deepseek")
        self.options: dict[str, dict] = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.profiles = {}
            for profile in data["profiles"]:
                checked = validate_profile(profile)
                self.profiles[checked["id"]] = checked
            self.active = data["active"]
            self.options = data.get("options", {})
        if self.active not in self.profiles:
            raise ValueError("当前连接不存在，请检查 providers.json。")

    def public(self) -> list[dict]:
        return [{**p, "key_configured": bool(get_key(self.root, ident)), "key_env": key_name(ident)} for ident, p in self.profiles.items()]

    def persist(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"version": 1, "active": self.active, "profiles": list(self.profiles.values()), "options": self.options}, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def save(self, profile: dict, api_key: str | None = None) -> dict:
        checked = validate_profile(profile)
        # Validate the entire profile before touching the secret file.
        if api_key is not None and api_key != "":
            save_key(self.root, checked["id"], api_key)
        self.profiles[checked["id"]] = checked
        # Explicitly saving a default model supersedes earlier transient choices.
        self.options.pop(checked["id"], None)
        self.persist()
        return checked

    def select(self, ident: str):
        if ident not in self.profiles:
            raise ValueError("连接不存在。")
        self.active = ident
        self.persist()

    def delete(self, ident: str):
        if ident == self.active:
            raise ValueError("请先切换到其他连接，再删除此连接。")
        if ident not in self.profiles:
            raise ValueError("连接不存在。")
        del self.profiles[ident]
        self.options.pop(ident, None)
        # Secrets are retained locally so deleting metadata never destroys credentials.
        self.persist()

    def client(self, timeout: int = 120):
        profile = self.profiles[self.active]
        cls = AnthropicClient if profile["protocol"] == "anthropic" else CompatibleClient
        client = cls(get_key(self.root, self.active), profile, timeout)
        if self.active in self.options:
            client.configure(self.options[self.active])
        return client

    def save_options(self, options: dict):
        client = self.client()
        client.configure(options)
        self.options[self.active] = {key: getattr(client, key) for key in ("model", "thinking", "max_tokens", "image_detail")}
        self.persist()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DeepSeekError("接口返回重定向；请直接配置最终 API 地址，凭据未转发。")


class CompatibleClient(DeepSeekClient):
    def __init__(self, api_key: str, profile: dict, timeout: int = 120):
        self.profile = validate_profile(profile)
        super().__init__(api_key, self.profile["model"], self.profile["base_url"], timeout)
        self.thinking = "high" if self.profile["reasoning"] == "deepseek" else "off"

    def supports_images(self) -> bool:
        if self.profile["preset"] == "deepseek":
            return super().supports_images()
        return self.profile["vision"]

    def _open(self, request):
        if not self.api_key:
            raise DeepSeekError("此连接尚未配置密钥。请在连接设置中保存密钥，或使用 setup-key --provider 连接ID。")
        try:
            return urllib.request.build_opener(NoRedirect()).open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            hints = {400: "参数与模型不兼容，请检查能力或关闭推理", 401: "密钥无效", 403: "无访问权限", 404: "接口路径或模型不存在", 402: "余额不足", 429: "请求限流"}
            raise DeepSeekError(f"模型接口 HTTP {code}：{hints.get(code, '服务暂时不可用')}。") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise DeepSeekError("无法连接模型接口，请检查地址、网络与代理。") from None

    def _payload(self, messages: list[dict], streaming: bool) -> dict:
        mode = self.profile["reasoning"]
        clean = []
        for message in messages:
            item = {k: copy.deepcopy(v) for k, v in message.items() if k in ("role", "content", "tool_calls", "tool_call_id")}
            if mode in ("deepseek", "kimi", "glm") and "reasoning_content" in message:
                item["reasoning_content"] = message["reasoning_content"]
            clean.append(item)
        payload = {"model": self.model, "messages": clean, "stream": streaming, "max_tokens": self.max_tokens}
        if self.profile["tools"]:
            payload["tools"] = tool_schemas(self.available_tools)
        if streaming and self.profile["stream_usage"]:
            payload["stream_options"] = {"include_usage": True}
        enabled = self.thinking != "off"
        if mode in ("deepseek", "kimi", "glm"):
            payload["thinking"] = {"type": "enabled" if enabled else "disabled"}
            if mode == "deepseek" and enabled:
                payload["reasoning_effort"] = self.thinking
        elif mode == "qwen":
            payload["enable_thinking"] = enabled
        elif mode == "openai":
            payload["max_completion_tokens"] = payload.pop("max_tokens")
            if enabled:
                payload["reasoning_effort"] = "high" if self.thinking == "max" else self.thinking
        # none deliberately sends no reasoning parameters (model default).
        return payload

    def _stream(self, response) -> dict:
        message = super()._stream(response)
        if message.get("tool_calls") and not self.profile["tools"]:
            raise DeepSeekError("当前连接已禁用工具调用，拒绝执行服务返回的工具。")
        return message

    def complete(self, messages: list[dict]) -> dict:
        result = super().complete(messages)
        if result.get("tool_calls") and not self.profile["tools"]:
            raise DeepSeekError("当前连接已禁用工具调用，拒绝执行服务返回的工具。")
        return result


class AnthropicClient(CompatibleClient):
    def _headers(self):
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}

    def list_models(self) -> list[str]:
        models, after = set(), ""
        for _ in range(20):
            suffix = "/models?limit=100" + ("&after_id=" + urllib.parse.quote(after, safe="") if after else "")
            with self._open(urllib.request.Request(self.base_url + suffix, headers=self._headers())) as response:
                body = json.load(response)
            models.update(item["id"] for item in body.get("data", []) if isinstance(item.get("id"), str))
            if not body.get("has_more"):
                return sorted(models)
            after = body.get("last_id")
            if not isinstance(after, str) or not after:
                break
        raise DeepSeekError("模型列表分页不完整，请手动填写模型 ID。")

    def _payload(self, messages: list[dict], streaming: bool) -> dict:
        system, converted = [], []
        for msg in messages:
            role, content = msg["role"], msg.get("content")
            if role == "system":
                system.append(content)
                continue
            blocks = []
            if role == "tool":
                role = "user"
                blocks = [{"type": "tool_result", "tool_use_id": msg["tool_call_id"], "content": content}]
            elif role == "assistant" and msg.get("_anthropic_content"):
                blocks = copy.deepcopy(msg["_anthropic_content"])
            else:
                if isinstance(content, str) and content:
                    blocks.append({"type": "text", "text": content})
                elif isinstance(content, list):
                    for part in content:
                        if part["type"] == "text":
                            blocks.append(copy.deepcopy(part))
                        elif part["type"] == "image_url":
                            url = part["image_url"]["url"]
                            match = re.fullmatch(r"data:(image/[a-z]+);base64,([A-Za-z0-9+/=]+)", url)
                            if not match:
                                raise ValueError("Claude 图片须为本地 Base64 图片。")
                            blocks.append({"type": "image", "source": {"type": "base64", "media_type": match[1], "data": match[2]}})
                for call in msg.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": json.loads(call["function"]["arguments"])})
            if not blocks:
                blocks = [{"type": "text", "text": "[空消息]"}]
            if converted and converted[-1]["role"] == role:
                converted[-1]["content"].extend(blocks)
            else:
                converted.append({"role": role, "content": blocks})
        payload = {"model": self.model, "system": "\n".join(system), "messages": converted, "stream": streaming, "max_tokens": self.max_tokens}
        if self.profile["tools"]:
            payload["tools"] = [{"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]} for t in tool_schemas(self.available_tools)]
        if self.thinking != "off" and self.profile["reasoning"] == "anthropic":
            if self.max_tokens <= 1024:
                raise ValueError("Claude 推理需要输出上限大于 1024。")
            budget = {"low": 1024, "high": 4096, "max": 16384}[self.thinking]
            payload["thinking"] = {"type": "enabled", "budget_tokens": min(budget, self.max_tokens - 1)}
        return payload

    def _usage(self, raw: dict):
        old = self.last_usage
        # Anthropic message_delta output_tokens is cumulative, not an increment.
        if "input_tokens" in raw:
            old["prompt_tokens"] = raw["input_tokens"] + raw.get("cache_read_input_tokens", 0) + raw.get("cache_creation_input_tokens", 0)
        if "cache_read_input_tokens" in raw:
            old["prompt_cache_hit_tokens"] = raw["cache_read_input_tokens"]
        if "output_tokens" in raw:
            old["completion_tokens"] = raw["output_tokens"]

    def _message(self, blocks: list[dict], stop: str | None) -> dict:
        if stop not in ("end_turn", "tool_use", "stop_sequence"):
            raise DeepSeekError("Claude 响应未完整完成或达到输出上限；未执行不完整的工具。")
        message = {"role": "assistant", "content": "", "_anthropic_content": copy.deepcopy(blocks)}
        calls = []
        for block in blocks:
            kind = block.get("type")
            if kind == "text":
                message["content"] += block.get("text", "")
            elif kind == "thinking":
                message["reasoning_content"] = message.get("reasoning_content", "") + block.get("thinking", "")
            elif kind == "tool_use":
                if not self.profile["tools"]:
                    raise DeepSeekError("当前连接未启用工具调用。")
                if not isinstance(block.get("input"), dict):
                    raise DeepSeekError("Claude 工具参数无效。")
                calls.append({"id": block["id"], "type": "function", "function": {"name": block["name"], "arguments": json.dumps(block["input"], ensure_ascii=False)}})
            elif kind != "redacted_thinking":
                raise DeepSeekError("Claude 返回了不支持的内容块。")
        if calls:
            message["tool_calls"] = calls
        return message

    def complete(self, messages: list[dict]) -> dict:
        if self.cancel_event.is_set():
            raise Cancelled("用户已停止生成。")
        self.last_usage = {}
        self.actual_model = self.model
        payload = self._payload(messages, self.on_event is not None)
        request = urllib.request.Request(self.base_url + "/messages", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=self._headers(), method="POST")
        self._emit("model_started", model=self.model, thinking=self.thinking)
        with self._open(request) as response:
            if self.on_event is not None:
                return self._stream_anthropic(response)
            body = json.load(response)
        self.actual_model = body.get("model") or self.model
        self._usage(body.get("usage") or {})
        return self._message(body.get("content") or [], body.get("stop_reason"))

    def _stream_anthropic(self, response) -> dict:
        blocks, partial, closed = {}, {}, set()
        started, finished, stop = False, False, None
        deadline = time.monotonic() + self.stream_limit
        for data in iter_sse(response):
            if self.cancel_event.is_set():
                raise Cancelled("用户已停止生成。")
            if time.monotonic() >= deadline:
                raise DeepSeekError("模型流式响应超过总时长限制，已停止；未执行不完整的工具。")
            try:
                chunk = json.loads(data)
                kind = chunk["type"]
                if kind == "error":
                    raise DeepSeekError("Claude 流返回服务错误。")
                if kind == "message_start":
                    started = True
                    self.actual_model = chunk["message"].get("model") or self.model
                    self._usage(chunk["message"].get("usage") or {})
                elif kind == "content_block_start":
                    index = chunk["index"]
                    if type(index) is not int or not 0 <= index < 64 or index in blocks:
                        raise DeepSeekError("Claude 内容块索引无效。")
                    blocks[index] = copy.deepcopy(chunk["content_block"])
                    block = blocks[index]
                    if block.get("type") in ("text", "thinking"):
                        value = block.get("text" if block["type"] == "text" else "thinking", "")
                        if value:
                            self._emit("content_delta" if block["type"] == "text" else "reasoning_delta", text=value)
                elif kind == "content_block_delta":
                    index = chunk["index"]
                    if index in closed:
                        raise DeepSeekError("Claude 内容块已结束。")
                    block, delta = blocks[index], chunk["delta"]
                    field = {"text_delta": "text", "thinking_delta": "thinking", "signature_delta": "signature"}.get(delta["type"])
                    if field:
                        value = delta.get(field, "")
                        block[field] = block.get(field, "") + value
                        if field != "signature":
                            self._emit("content_delta" if field == "text" else "reasoning_delta", text=value)
                    elif delta["type"] == "input_json_delta":
                        partial[index] = partial.get(index, "") + delta.get("partial_json", "")
                elif kind == "content_block_stop":
                    index = chunk["index"]
                    if index not in blocks or index in closed:
                        raise DeepSeekError("Claude 内容块结束事件无效。")
                    closed.add(index)
                    if index in partial:
                        blocks[index]["input"] = json.loads(partial[index])
                elif kind == "message_delta":
                    stop = chunk.get("delta", {}).get("stop_reason") or stop
                    self._usage(chunk.get("usage") or {})
                elif kind == "message_stop":
                    finished = True
                    break
            except (ValueError, KeyError, TypeError):
                raise DeepSeekError("Claude 流格式无效，未执行不完整的工具。") from None
        if not started or not finished or set(blocks) != closed:
            raise DeepSeekError("Claude 流提前中断，未执行不完整的工具。")
        return self._message([blocks[i] for i in sorted(blocks)], stop)
