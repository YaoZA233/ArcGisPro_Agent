from __future__ import annotations

import getpass
import json
import re
import sys
import threading
import time
from pathlib import Path

from .chat import ChatSession
from .config import Settings, discover_python
from .deepseek import DeepSeekClient, DeepSeekError, read_api_key
from .mdrender import render_markdown, supports_color, terminal_width
from .memory import MemoryStore
from .runtime import ArcPyRuntime, HostBridgeRuntime
from .tools import TOOLS
from .approvals import RequestBroker, terminal_approval
from .selections import SelectionStore
from .providers import PRESETS, REASONING, ProviderStore, preset_profile, save_key

ROOT = Path(__file__).resolve().parents[2]


def create_session(settings: Settings) -> ChatSession:
    store = ProviderStore(ROOT, settings.workspace, settings)
    client = store.client(settings.model_timeout_seconds)
    if store.active not in store.options:
        client.configure({"max_tokens": settings.max_tokens, **({"thinking": settings.thinking} if store.active == "deepseek" else {})})
    saved = settings.workspace / "ui-options.json"
    if saved.is_file() and store.active not in store.options:
        try:
            options = json.loads(saved.read_text(encoding="utf-8"))
            if options.pop("provider", "deepseek") == store.active:
                client.configure(options)
        except (ValueError, TypeError):
            pass
    memory = MemoryStore(settings.workspace / "memory.json")
    runtime = ArcPyRuntime(discover_python(settings.python), settings.timeout_seconds, settings.skills_dir,
                           data_roots=settings.data_roots, memory=memory)
    session = ChatSession(client, runtime, settings.workspace, settings.max_steps, memory=memory,
                          failure_budget=settings.failure_budget)
    session.providers = store
    return session


def resolve_session_id(prefix: str, chats: list[dict]) -> str:
    """Match a full session id or an unambiguous prefix of one saved chat."""
    prefix = (prefix or "").strip().lower()
    if not prefix:
        raise ValueError("请提供会话编号；用 /chats 查看列表。")
    if re.fullmatch(r"[0-9a-f]{32}", prefix):
        return prefix
    matches = [chat["session_id"] for chat in chats
               if isinstance(chat.get("session_id"), str) and chat["session_id"].startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError("没有匹配的会话编号；用 /chats 查看列表。")
    raise ValueError("编号前缀匹配到多个会话，请多输入几位。")


def setup_key(provider: str = "deepseek") -> int:
    """Read through the terminal, never accept secrets on the command line."""
    from .providers import validate_profile
    validate_profile(preset_profile("custom", provider))
    key = getpass.getpass("请输入 API Key（输入不会显示）: ").strip()
    destination = ROOT / ".env.local"
    save_key(ROOT, provider, key)
    print(f"密钥已保存在 {destination}；不会显示密钥。")
    return 0


def select_provider(session, ident: str, settings: Settings):
    store = session.providers
    if ident not in store.profiles:
        raise ValueError("连接不存在。")
    # Construct before persisting, so invalid settings cannot change active state.
    old = store.active
    store.active = ident
    try:
        client = store.client(settings.model_timeout_seconds)
    finally:
        store.active = old
    if ident not in store.options:
        client.configure({"max_tokens": settings.max_tokens})
    session.switch_client(client)
    store.select(ident)


def provider_state(session) -> dict:
    store = getattr(session, "providers", None)
    if not store:
        return {}
    client = session.client
    return {"provider": store.active, "providers": store.public(),
            "presets": [preset_profile(p) for p in PRESETS],
            "reasoning_modes": list(REASONING), "base_url": client.base_url,
            "protocol": client.profile["protocol"], "vision": client.supports_images(),
            "tools": client.profile["tools"], "reasoning_mode": client.profile["reasoning"],
            "key_configured": bool(client.api_key)}


def interactive(settings: Settings, allow_writes: bool) -> int:
    session = create_session(settings)
    # Never let a GBK/ASCII console codec crash the reply; degrade to '?' instead.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
    color = supports_color()
    width = terminal_width()
    print(f"ArcGIS Agent · {session.providers.active} / {session.client.model} | 输入 /new 新对话，/chats 历史会话，/quit 退出。")
    print("每次工具调用均需单独确认；写入可在本次提示中授权。" + (" 已预先启用写入能力。" if allow_writes else ""))
    print("/model 名称 切换模型；/think off|low|high|max；/attach 路径 附加文件；/status 查看设置")
    print("/providers 列出连接；/provider 连接ID 切换；/models 获取账户模型列表")
    print("/resume 编号 恢复历史会话；/memory 查看长期记忆；/forget 序号 删除记忆")
    attachments = []
    while True:
        try:
            prompt = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if prompt in ("/quit", "/exit"):
            return 0
        if prompt == "/new":
            session.reset()
            print("已开始新对话。")
            continue
        if prompt == "/chats":
            chats = session.list_chats()
            if not chats:
                print("暂无历史会话。")
            else:
                for chat in chats:
                    usage = chat.get("total_usage") or {}
                    print(f"  {chat['session_id'][:8]} | {chat.get('updated', '')[:16].replace('T', ' ')} | "
                          f"{chat.get('title', '')} | 输入 {usage.get('input', 0)} / 输出 {usage.get('output', 0)} tokens")
                print("用 /resume 编号 恢复会话（编号可只输前几位）。")
            continue
        if prompt.startswith("/resume"):
            try:
                full = resolve_session_id(prompt[7:].strip(), session.list_chats())
                loaded = session.load(full)
                print(f"已恢复会话 {full[:8]}：{len(loaded['turns'])} 轮对话。继续提问即可；/new 开始新对话。")
            except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
                print("提示：" + str(exc))
            continue
        if prompt == "/memory":
            entries = session.memory.entries()
            if not entries:
                print("暂无长期记忆；对话中模型可经你授权后用 remember 工具记录。")
            else:
                for number, item in enumerate(entries, 1):
                    print(f"  {number}. {item['text']}")
                print(f"用 /forget 序号 删除某条；完整文件在 {session.memory.path}。")
            continue
        if prompt.startswith("/forget "):
            try:
                removed = session.memory.remove(int(prompt[8:].strip()))
                print(f"已删除记忆：{removed['removed']}")
            except (ValueError, OSError) as exc:
                print("提示：" + str(exc))
            continue
        if prompt == "/providers":
            print(json.dumps(session.providers.public(), ensure_ascii=False, indent=2))
            continue
        if prompt.startswith("/provider "):
            try:
                select_provider(session, prompt[10:].strip(), settings)
                print("当前连接：" + session.providers.active + " / " + session.client.model)
            except (ValueError, OSError) as exc:
                print("提示：" + str(exc))
            continue
        if prompt == "/models":
            try:
                print("\n".join(session.client.list_models()))
            except (ValueError, OSError, DeepSeekError) as exc:
                print("提示：" + str(exc))
            continue
        if prompt.startswith("/model "):
            try:
                session.client.configure({"model": prompt[7:].strip()})
                session.providers.save_options({"model": session.client.model})
                print("模型：" + session.client.model)
            except ValueError as exc:
                print("提示：" + str(exc))
            continue
        if prompt.startswith("/think "):
            try:
                session.client.configure({"thinking": prompt[7:].strip()})
                session.providers.save_options({"thinking": session.client.thinking})
                print("思考：" + session.client.thinking)
            except ValueError as exc:
                print("提示：" + str(exc))
            continue
        if prompt.startswith("/attach "):
            attachments.append(prompt[8:].strip().strip('"'))
            print("已添加附件；将在下一条消息中发送。")
            continue
        if prompt == "/status":
            print(f"连接 {session.providers.active} | 模型 {session.client.model} | 思考 {session.client.thinking} | 输出上限 {session.client.max_tokens} | 累计 {session.total_usage}")
            continue
        if not prompt:
            continue
        stream_state = {"kind": ""}
        content_buffer: list[str] = []
        progress = {"started": False}

        def event(record: dict) -> None:
            kind = record["event"]
            if kind == "tool_requested":
                stream_state["step_start"] = time.monotonic()
                print(f"\n  [请求工具] 步骤 {record.get('step', '?')} · {record['tool']} " +
                      json.dumps(record["arguments"], ensure_ascii=False), flush=True)
                stream_state["kind"] = "tool"
            elif kind == "approval_requested":
                print(f"  [等待授权] 步骤 {record.get('step', '?')} · {record['tool']}", flush=True)
            elif kind == "tool_result":
                elapsed = time.monotonic() - stream_state.get("step_start", time.monotonic())
                print(f"  [结果] 步骤 {record.get('step', '?')} ({elapsed:.1f}s) " +
                      json.dumps(record.get("data", record.get("error")), ensure_ascii=False), flush=True)
            elif kind == "tool_progress":
                print(f"    [worker] {record.get('text', '')}", flush=True)
                stream_state["kind"] = "tool"
            elif kind == "content_delta":
                # Buffer the reply instead of echoing raw Markdown; the finished
                # text is rendered once below so the user never reads raw '#/**'.
                content_buffer.append(record.get("text", ""))
                stream_state["kind"] = "content"
                if color:
                    if not progress["started"]:
                        sys.stdout.write("\n")  # close any dangling reasoning line
                        progress["started"] = True
                    count = sum(len(piece) for piece in content_buffer)
                    sys.stdout.write(f"\r\033[2m  [生成中… {count} 字]\033[0m\033[K")
                    sys.stdout.flush()
            elif kind == "reasoning_delta":
                if stream_state["kind"] != "reasoning":
                    print("\n  [模型推理] ", end="", flush=True)
                    stream_state["kind"] = "reasoning"
                print(record["text"], end="", flush=True)
        print("  [思考中…]", flush=True)
        session.client.cancel_event.clear()
        try:
            result = session.send(prompt, allow_writes=allow_writes, on_event=event, attachments=attachments, approve=terminal_approval)
        except (ValueError, OSError, DeepSeekError) as exc:
            print("\n提示：" + str(exc))
            continue
        attachments.clear()
        if progress["started"]:
            sys.stdout.write("\r\033[K" if color else "\r")  # erase the live counter
            sys.stdout.flush()
        print()
        print("\033[1;32mAgent >\033[0m" if color else "Agent >")
        print(render_markdown(result["reply"], color=color, width=width))
        print(f"  [用量] 输入 {result['usage']['input']} / 输出 {result['usage']['output']} tokens；耗时 {result['elapsed_seconds']}s")
        print("  [任务] " + result["status"] + " | " + result["run_dir"])


def rpc(settings: Settings) -> int:
    """One JSON request per line; events and results on stdout, no other stdout."""
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    output_lock = threading.Lock()
    def emit(value: dict) -> None:
        with output_lock:
            print(json.dumps(value, ensure_ascii=False), flush=True)

    try:
        session = create_session(settings)
    except Exception as exc:
        emit({"type": "error", "message": str(exc)})
        return 1
    def state() -> dict:
        client = session.client
        return {"model": getattr(client, "model", settings.model), "thinking": getattr(client, "thinking", settings.thinking),
                "max_tokens": getattr(client, "max_tokens", settings.max_tokens), "models": [getattr(client, "model", settings.model)],
                "image_detail": getattr(client, "image_detail", "auto"), "workspace": str(settings.workspace), "session_id": session.session_id,
                **provider_state(session)}

    active: threading.Thread | None = None
    cancel_event = getattr(session.client, "cancel_event", None) or threading.Event()
    if not hasattr(session.client, "cancel_event"):
        session.client.cancel_event = cancel_event
    broker = RequestBroker(emit, cancel_event)
    selections = SelectionStore()
    emit({"type": "ready", **state()})

    def send(request):
        base_runtime = session.runtime
        try:
            writes = request.get("allow_writes", False)
            if type(writes) is not bool:
                raise ValueError("allow_writes 必须为布尔值。")
            host_enabled = request.get("host_tools", False)
            if type(host_enabled) is not bool:
                raise ValueError("host_tools 必须为布尔值。")
            if hasattr(session.client, "available_tools"):
                session.client.available_tools = {name for name, tool in TOOLS.items() if host_enabled or not tool.host}
            session.runtime = HostBridgeRuntime(base_runtime, broker, host_enabled, selections)
            result = session.send(request.get("message"), allow_writes=writes,
                options=request.get("options"), attachments=request.get("attachments"), context=request.get("context", ""),
                approve=lambda proposal: broker.request("approval", proposal),
                on_event=lambda event: emit({"type": "event", "event": event}) if event["event"] != "approval_requested" else None)
            emit({"type": "result", "result": result})
        except Exception as exc:
            emit({"type": "error", "message": str(exc)})
        finally:
            session.runtime = base_runtime

    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("请求必须是 JSON 对象。")
            kind = request.get("type")
            if kind in ("approval_response", "host_result"):
                try:
                    broker.respond("approval" if kind == "approval_response" else "host", request.get("request_id"), request.get("response"))
                    emit({"type": "control_ack", "request_id": request.get("request_id")})
                except (ValueError, TypeError) as exc:
                    emit({"type": "control_error", "message": str(exc)})
                continue
            if kind == "cancel":
                cancel_event.set()
                emit({"type": "cancelling"})
                continue
            if active and active.is_alive():
                raise ValueError("当前任务正在执行，请先停止或等待完成。")
            if kind == "reset":
                selections.clear()
                session.reset()
                emit({"type": "reset", "session_id": session.session_id})
            elif kind == "send":
                cancel_event = getattr(session.client, "cancel_event", None) or threading.Event()
                broker.cancel_event = cancel_event
                cancel_event.clear()
                if hasattr(session.client, "cancel_event"):
                    session.client.cancel_event.clear()
                active = threading.Thread(target=send, args=(request,), name="agent-turn", daemon=True)
                active.start()
            elif kind == "configure":
                options = request.get("options", {})
                session.client.configure(options)
                if hasattr(session, "providers"):
                    session.providers.save_options(options)
                saved = settings.workspace / "ui-options.json"
                saved.parent.mkdir(parents=True, exist_ok=True)
                saved.write_text(json.dumps({"provider": getattr(getattr(session, "providers", None), "active", "deepseek"), **{key: getattr(session.client, key) for key in ("model", "thinking", "max_tokens", "image_detail")}}), encoding="utf-8")
                emit({"type": "configured", **state()})
            elif kind == "providers":
                emit({"type": "providers", **state()})
            elif kind == "provider_save":
                profile = session.providers.save(request.get("profile"), request.get("api_key"))
                if profile["id"] == session.providers.active or request.get("activate", False):
                    select_provider(session, profile["id"], settings)
                emit({"type": "providers", **state()})
            elif kind == "provider_select":
                select_provider(session, request.get("provider"), settings)
                emit({"type": "providers", **state()})
            elif kind == "provider_delete":
                session.providers.delete(request.get("provider"))
                emit({"type": "providers", **state()})
            elif kind == "models":
                emit({"type": "models", "models": session.client.list_models()})
            elif kind == "history":
                emit({"type": "history", "chats": session.list_chats()})
            elif kind == "load":
                loaded = session.load(request.get("session_id"))
                selections.clear()
                emit({"type": "loaded", **loaded})
            else:
                raise ValueError("未知请求类型。")
        except (ValueError, TypeError, OSError, DeepSeekError) as exc:
            emit({"type": "error", "message": str(exc)})
    broker.close()
    if active and active.is_alive():
        cancel_event.set()
        active.join(timeout=5)
    return 0
