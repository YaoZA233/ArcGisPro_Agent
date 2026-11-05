"""Cross-session long-term memory: a small, human-editable store of stable facts.

The store is a JSON file under the workspace (default ``memory.json``). Entries
are short text lines recorded through the allowlisted ``remember`` tool or
managed by the user in the terminal. Every new chat session injects the rendered
memory into its first model message; later turns rely on normal chat history.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

MEMORY_TOOLS = ("remember",)
MAX_ENTRIES = 200
MAX_TEXT_LENGTH = 500
RENDER_LIMIT = 6000
TRUNCATION_NOTE = "- （更早的记忆已截断，完整内容见 workspace/memory.json）"
INJECTION_HEADER = ("[长期记忆] 以下是此前会话记录的稳定事实与用户偏好，仅供参考；"
                    "与当前指令冲突时以当前指令为准：")


class MemoryStore:
    """Stateless per operation: always reads the file so CLI, panel and toolbox agree."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def entries(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("entries") if isinstance(data, dict) else None
        return [item for item in items or []
                if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip()]

    def add(self, text: str) -> dict:
        if not isinstance(text, str):
            raise ValueError("记忆内容必须是文本。")
        text = text.strip()
        if not text:
            raise ValueError("记忆内容不能为空。")
        if len(text) > MAX_TEXT_LENGTH:
            raise ValueError(f"单条记忆不能超过 {MAX_TEXT_LENGTH} 字符；请拆分为更短的事实。")
        entries = self.entries()
        if any(item["text"] == text for item in entries):
            return {"status": "duplicate", "count": len(entries), "text": text}
        entries.append({"text": text, "created": datetime.now(timezone.utc).isoformat()})
        dropped = max(0, len(entries) - MAX_ENTRIES)
        if dropped:
            entries = entries[dropped:]
        self._save(entries)
        return {"status": "added", "count": len(entries), "dropped_oldest": dropped, "text": text}

    def remove(self, number: int) -> dict:
        if type(number) is not int:
            raise ValueError("序号必须是整数。")
        entries = self.entries()
        if not 1 <= number <= len(entries):
            raise ValueError(f"序号超出范围：当前共 {len(entries)} 条记忆。")
        removed = entries.pop(number - 1)
        self._save(entries)
        return {"removed": removed["text"], "count": len(entries)}

    def clear(self) -> dict:
        count = len(self.entries())
        self._save([])
        return {"cleared": count}

    def render(self, limit: int = RENDER_LIMIT) -> str:
        """Oldest-to-newest bullet list; the newest entries win the character budget."""
        kept, used, truncated = [], 0, False
        for item in reversed(self.entries()):
            line = "- " + item["text"]
            if used + len(line) > limit:
                truncated = True
                break
            kept.append(line)
            used += len(line)
        kept.reverse()
        if truncated:
            kept.insert(0, TRUNCATION_NOTE)
        return "\n".join(kept)

    def _save(self, entries: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)


def execute_memory_tool(name: str, arguments: dict, store: MemoryStore | None) -> dict:
    if store is None:
        raise ValueError("记忆功能未配置：当前入口没有提供记忆存储。")
    if name == "remember":
        return store.add(arguments["text"])
    raise ValueError(f"Unknown memory tool: {name}")


def memory_block(store: MemoryStore | None) -> str:
    """Injection text for the first message of a session; empty when there is nothing."""
    if store is None:
        return ""
    body = store.render()
    return INJECTION_HEADER + "\n" + body if body else ""
