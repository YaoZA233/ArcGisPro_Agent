"""Single-use, fail-closed approvals and bounded RPC rendezvous."""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid

from .deepseek import Cancelled

APPROVAL_MODES = ("ask", "full")


def validate_approval_mode(value: str) -> str:
    if value not in APPROVAL_MODES:
        raise ValueError("approval_mode must be 'ask' or 'full'")
    return value


def full_access_approval(request: dict) -> dict:
    """Automatically grant this operation while retaining the normal audit payload."""
    return {"decision": "allow", "allow_writes": bool(request.get("writes"))}


def terminal_approval(request: dict) -> dict:
    print("\n┌─ 本次操作需要确认 ─────────────────────", flush=True)
    print("  操作：" + request["title"], flush=True)
    print("  影响：" + request["effect"], flush=True)
    print("  参数：", flush=True)
    for line in json.dumps(request["arguments"], ensure_ascii=False, indent=2).splitlines():
        print("    " + line, flush=True)
    if request.get("selection"):
        print("  选择集：", flush=True)
        for line in json.dumps(request["selection"], ensure_ascii=False, indent=2).splitlines():
            print("    " + line, flush=True)
    if request.get("expected_outputs"):
        print("  预计输出：", flush=True)
        for path in request["expected_outputs"]:
            print("    " + str(path), flush=True)
    print("└────────────────────────────────────────", flush=True)
    try:
        answer = input("允许本次操作？ [y 允许 / n 拒绝 / c 取消；回车拒绝] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return {"decision": "cancel"}
    return {"decision": "allow" if answer in ("y", "yes") else "cancel" if answer == "c" else "deny",
            "allow_writes": request["writes"] and answer in ("y", "yes")}


def toolbox_approval(request: dict) -> dict:
    """A native Windows confirmation per tool; default focus is on No."""
    import ctypes
    text = request["effect"] + "\n\n" + json.dumps(request["arguments"], ensure_ascii=False, indent=2)
    if request.get("selection"):
        text += "\n\n选择集：" + json.dumps(request["selection"], ensure_ascii=False, indent=2)
    if request.get("expected_outputs"):
        text += "\n\n输出：\n" + "\n".join(request["expected_outputs"])
    result = ctypes.windll.user32.MessageBoxW(None, text, "GIS Agent · " + request["title"], 0x00000003 | 0x00000020 | 0x00000100)
    return {"decision": {6: "allow", 7: "deny"}.get(result, "cancel"), "allow_writes": result == 6 and request["writes"]}


class RequestBroker:
    def __init__(self, emit, cancel_event: threading.Event):
        self.emit, self.cancel_event = emit, cancel_event
        self.lock = threading.Lock()
        self.pending: dict[str, dict] = {}
        self.closed = False

    def request(self, kind: str, payload: dict, timeout: float = 600):
        ident = payload.get("request_id") or uuid.uuid4().hex
        item = {"kind": kind, "ready": threading.Event(), "response": None}
        with self.lock:
            if self.closed or self.cancel_event.is_set():
                raise Cancelled("连接已关闭或任务已取消。")
            self.pending[ident] = item
        try:
            self.emit({"type": "event", "event": {"event": kind + "_requested", **copy.deepcopy(payload), "request_id": ident}})
            deadline = time.monotonic() + timeout
            while not item["ready"].wait(0.1):
                if self.cancel_event.is_set() or self.closed:
                    raise Cancelled("等待用户授权或 Pro 响应时任务已取消。")
                if time.monotonic() >= deadline:
                    if kind == "approval":
                        return {"decision": "expired"}
                    raise TimeoutError("ArcGIS Pro 未在规定时间内响应工具请求。")
            if self.cancel_event.is_set() or self.closed:
                raise Cancelled("任务已取消，未继续执行。")
            return copy.deepcopy(item["response"])
        finally:
            with self.lock:
                self.pending.pop(ident, None)

    def respond(self, kind: str, ident: str, response: dict):
        if not isinstance(ident, str) or not isinstance(response, dict):
            raise ValueError("响应格式无效。")
        if kind == "approval":
            if set(response) - {"decision", "allow_writes"} or response.get("decision") not in ("allow", "deny", "cancel"):
                raise ValueError("授权决定无效。")
            if type(response.get("allow_writes", False)) is not bool:
                raise ValueError("allow_writes 必须为布尔值。")
        elif kind == "host" and (type(response.get("ok")) is not bool or set(response) - {"ok", "data", "error"}):
            raise ValueError("Pro 工具响应无效。")
        with self.lock:
            item = self.pending.get(ident)
            if item is None or item["kind"] != kind or item["ready"].is_set() or self.closed or self.cancel_event.is_set():
                raise ValueError("请求已过期、已答复或不属于当前操作。")
            item["response"] = copy.deepcopy(response)
            item["ready"].set()

    def close(self):
        with self.lock:
            self.closed = True
            for item in self.pending.values():
                item["ready"].set()
