"""Opt-in live model -> per-tool fixture approvals -> ArcPy pipeline.

Run with Pro Python and a fixture root created by smoke_gis_tools.py.
Only four named tools and paths under that workspace/smoke root are approved.
Uses the configured DeepSeek key, does not change the user's active provider.
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from arcgis_agent.config import Settings
from arcgis_agent.interfaces import create_session
from arcgis_agent.providers import CompatibleClient, get_key, preset_profile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    root = args.fixture.resolve(strict=True)
    if not root.is_relative_to((REPO / "workspace/smoke").resolve()) or not (root / "tool-validation.json").is_file():
        raise ValueError("Use a verified synthetic fixture root under workspace/smoke")
    import arcpy
    source = str(root / "input.gdb/points")
    settings = Settings(workspace=root, python=Path(sys.executable))
    session = create_session(settings)
    session.switch_client(CompatibleClient(get_key(REPO, "deepseek"), preset_profile("deepseek")))
    approvals = []

    def approve_fixture(request):
        allowed = request["tool"] in ("describe", "select_features", "buffer", "count")
        for key, value in request["arguments"].items():
            if key == "dataset":
                allowed &= Path(value).resolve().is_relative_to(root)
        allowed &= all(Path(path).resolve().is_relative_to(root) for path in request["expected_outputs"])
        approvals.append({"tool": request["tool"], "request_id": request["request_id"], "allowed": bool(allowed)})
        print(json.dumps({"fixture_approval": approvals[-1], "arguments": request["arguments"]}, ensure_ascii=False), flush=True)
        return {"decision": "allow" if allowed else "deny", "allow_writes": bool(allowed and request["writes"])}

    print("Running a live natural-language pipeline on synthetic fixtures only…", flush=True)
    result = session.send("请实际调用工具完成这四步：先 describe 检查 " + source +
        "；再 select_features 筛选 group_name 等于 A，保存为 selected_A；再对筛选输出创建 50 Meters 测地线缓冲区 buffers_A；最后 count 统计缓冲区记录数。"
        "严格按步骤使用真实工具返回的路径，每次操作会由测试程序单独核准。简短报告最终路径与数量。",
        options={"thinking": "off", "max_tokens": 2048}, approve=approve_fixture,
        on_event=lambda event: print(json.dumps({"event": event["event"], "tool": event.get("tool", "")}), flush=True) if event["event"] in ("model_started", "tool_executing", "tool_result") else None)
    calls = result["observations"]
    names = [observation["tool"] for observation in calls]
    output = next((observation["data"]["output"] for observation in calls if observation["tool"] == "buffer" and observation["ok"]), None)
    passed = result["status"] == "completed" and names == ["describe", "select_features", "buffer", "count"] and len(approvals) == len(calls)
    areas = []
    if output and arcpy.Exists(output):
        areas = [row[0].getArea("GEODESIC", "SQUAREMETERS") for row in arcpy.da.SearchCursor(output, ["SHAPE@"])]
        passed &= int(arcpy.management.GetCount(output)[0]) == 2 and len(areas) == 2 and all(7000 < area < 8500 for area in areas)
    else:
        passed = False
    passed &= int(arcpy.management.GetCount(source)[0]) == 3
    summary = {"ok": bool(passed), "fixture_root": str(root), "model": result["model"], "status": result["status"],
               "tools": names, "approvals": approvals, "reply": result["reply"], "usage": result["usage"], "run_dir": result["run_dir"],
               "output": output, "areas_m2": areas, "simulated": result["simulated"]}
    (root / "live-agent-validation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
