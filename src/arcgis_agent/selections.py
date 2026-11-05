"""Bounded local selection handles; bulk object IDs never enter model history."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

MAX_SELECTION = 100000


def validate_snapshot(value: dict, token: str | None = None, *, allow_empty=False) -> dict:
    if not isinstance(value, dict) or set(value) != {"selection_token", "dataset", "object_ids"}:
        raise ValueError("Invalid private selection snapshot")
    if not isinstance(value["selection_token"], str) or len(value["selection_token"]) != 32 or any(c not in "0123456789abcdef" for c in value["selection_token"]):
        raise ValueError("Invalid selection token")
    if token is not None and value["selection_token"] != token:
        raise ValueError("Selection token does not match the approved snapshot")
    dataset, ids = value["dataset"], value["object_ids"]
    if not isinstance(dataset, str) or len(dataset) > 4096 or any(ord(c) < 32 for c in dataset) or not (Path(dataset).is_absolute() or PureWindowsPath(dataset).is_absolute()):
        raise ValueError("Selection source must be an absolute local dataset path")
    if not isinstance(ids, (list, tuple)) or not (0 if allow_empty else 1) <= len(ids) <= MAX_SELECTION:
        raise ValueError(f"Selection must contain 1..{MAX_SELECTION} object IDs; an empty selection never means all features")
    if any(type(oid) is not int or not 0 <= oid <= 2**63 - 1 for oid in ids) or len(set(ids)) != len(ids):
        raise ValueError("Selection contains invalid or duplicate object IDs")
    return {"selection_token": value["selection_token"], "dataset": dataset, "object_ids": tuple(sorted(ids))}


@dataclass(frozen=True)
class SelectionSnapshot:
    token: str
    dataset: str
    object_ids: tuple[int, ...]
    map_uri: str
    layer_uri: str
    layer_name: str
    captured_at: str
    expires: float

    def payload(self):
        return {"selection_token": self.token, "dataset": self.dataset, "object_ids": list(self.object_ids)}

    def public(self):
        return {"selection_token": self.token, "dataset": self.dataset, "selected_count": len(self.object_ids),
                "map_uri": self.map_uri, "layer_uri": self.layer_uri, "layer_name": self.layer_name,
                "captured_at": self.captured_at, "object_id_digest": hashlib.sha256(json.dumps(self.object_ids).encode()).hexdigest(),
                "semantics": "冻结捕获时的所选 ID；导出读取已保存的数据，不包含未提交编辑。有效期 30 分钟，最多保留 8 份。"}


class SelectionStore:
    """Owned by one serial RPC session; reset/load invalidates old handles."""
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.snapshots: dict[str, SelectionSnapshot] = {}

    def clear(self):
        self.snapshots.clear()

    def capture(self, data: dict, arguments: dict):
        if not isinstance(data, dict) or set(data) != {"dataset", "object_ids", "layer_name", "map_uri", "layer_uri"}:
            raise ValueError("Invalid Pro selection response")
        if any(not isinstance(data[k], str) or len(data[k]) > 4096 for k in ("layer_name", "map_uri", "layer_uri")):
            raise ValueError("Invalid selection layer metadata")
        if any(data[key] != arguments[key] for key in ("map_uri", "layer_uri")):
            raise ValueError("Pro returned a selection from a different map or layer")
        token = uuid.uuid4().hex
        checked = validate_snapshot({"selection_token": token, "dataset": data["dataset"], "object_ids": data["object_ids"]}, allow_empty=True)
        if not checked["object_ids"]:
            return {"selected_count": 0, "selection_token": None, "dataset": data["dataset"], "layer_name": data["layer_name"],
                    "message": "当前图层没有选中要素；不会自动导出整层，请先在 Pro 中选择。"}
        now = self.clock()
        self.snapshots = {key: value for key, value in self.snapshots.items() if value.expires > now}
        while len(self.snapshots) >= 8:
            self.snapshots.pop(next(iter(self.snapshots)))
        snapshot = SelectionSnapshot(token, checked["dataset"], checked["object_ids"], data["map_uri"], data["layer_uri"],
                                     data["layer_name"], datetime.now(timezone.utc).isoformat(), now + 1800)
        self.snapshots[token] = snapshot
        return snapshot.public()

    def get(self, token):
        snapshot = self.snapshots.get(token)
        if snapshot is None or self.clock() >= snapshot.expires:
            self.snapshots.pop(token, None)
            raise ValueError("选择集快照已过期或不属于本会话，请重新调用 get_selection。")
        return snapshot
