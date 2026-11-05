"""File protocol worker, invoked with ArcGIS Pro's own Python executable."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Works without installing this package into Esri's managed environment.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arcgis_agent.tools import execute
from arcgis_agent.hints import hint_for


def write_result(result_path: Path, result: dict) -> int:
    try:
        text = json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        # An unserialisable payload cannot be trusted as a success; the caller must see failure.
        result = {"ok": False, "error": {"type": "SerializationError",
                                         "message": "工具结果无法序列化为 JSON，按失败处理；返回内容可能包含 ArcPy 对象。"}}
        text = json.dumps(result, ensure_ascii=False)
    result_path.write_text(text, encoding="utf-8")
    return 0 if result["ok"] else 1


def main() -> int:
    request_path, result_path = map(Path, sys.argv[1:3])
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        # stderr survives a timeout kill (runtime.py keeps its tail), so the next
        # stall can be diagnosed down to the tool that was running.
        print(f"[worker] tool={request['tool']} starting", file=sys.stderr, flush=True)
        print(f"[worker] env ESRI_SOFTWARE_CLASS={os.environ.get('ESRI_SOFTWARE_CLASS')!r} "
              f"PYTHONHOME={os.environ.get('PYTHONHOME')!r} CONDA_PREFIX={os.environ.get('CONDA_PREFIX')!r}",
              file=sys.stderr, flush=True)
        result = {"ok": True, "data": execute(request["tool"], request["arguments"], Path(request["output_dir"]),
                                            selection_snapshot=request.get("selection_snapshot"))}
    except Exception as exc:
        result = {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
        arcpy = sys.modules.get("arcpy")
        messages = ""
        if arcpy is not None:
            messages = arcpy.GetMessages(2)[-4000:]
            result["error"]["arcgis_messages"] = messages
        hint = hint_for(str(exc) + "\n" + messages)
        if hint:
            result["error"]["hint"] = hint
    return write_result(result_path, result)


if __name__ == "__main__":
    raise SystemExit(main())
