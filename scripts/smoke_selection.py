"""Real ArcPy selection export on synthetic data; does not test Pro GUI capture.

Run with licensed Pro Python. All fixtures and outputs stay under workspace/smoke.
No provider API calls, user datasets, package installs or existing project edits.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from arcgis_agent.agent import Agent, PlanPlanner
from arcgis_agent.runtime import ArcPyRuntime, HostBridgeRuntime
from arcgis_agent.selections import SelectionStore


def main():
    print("Loading ArcPy for synthetic selection export verification…", flush=True)
    import arcpy

    root = (REPO / "workspace/smoke" / ("selection_" + uuid.uuid4().hex)).resolve()
    root.mkdir(parents=True, exist_ok=False)
    gdb = arcpy.management.CreateFileGDB(str(root), "input.gdb")[0]
    source = arcpy.management.CreateFeatureclass(gdb, "points", "POINT", spatial_reference=arcpy.SpatialReference(3857))[0]
    arcpy.management.AddField(source, "source_index", "LONG")
    with arcpy.da.InsertCursor(source, ["SHAPE@XY", "source_index"]) as cursor:
        for i in range(2105):
            cursor.insertRow([(i * 10, i % 17), i])
    with arcpy.da.SearchCursor(source, ["OID@", "source_index", "SHAPE@WKB"]) as cursor:
        original = {row[0]: (row[1], bytes(row[2])) for row in cursor}
    chosen = [oid for oid, (index, _) in original.items() if index % 2 == 0]
    assert len(chosen) > 900
    store = SelectionStore()
    public = store.capture({"dataset": source, "object_ids": chosen, "map_uri": "fixture-map", "layer_uri": "fixture-layer",
                            "layer_name": "Synthetic points"}, {"map_uri": "fixture-map", "layer_uri": "fixture-layer"})
    # Selection membership changes after capture must not change the frozen handle.
    chosen.append(next(oid for oid in original if oid not in chosen))
    runtime = HostBridgeRuntime(ArcPyRuntime(Path(sys.executable)), None, True, store)
    approvals = []

    def approve_fixture(request):
        allowed = request["tool"] == "export_selection" and Path(request["selection"]["dataset"]).resolve().is_relative_to(root)
        allowed &= bool(request["expected_outputs"]) and all(Path(path).resolve().is_relative_to(root) for path in request["expected_outputs"])
        approvals.append({"request_id": request["request_id"], "tool": request["tool"], "allowed": bool(allowed),
                          "selected_count": request.get("selection", {}).get("selected_count")})
        print(json.dumps({"fixture_approval": approvals[-1]}), flush=True)
        return {"decision": "allow" if allowed else "deny", "allow_writes": bool(allowed)}

    result = Agent(runtime, root).run(PlanPlanner([{"tool": "export_selection", "arguments": {
        "selection_token": public["selection_token"], "output_name": "chosen_points"}}]), approve=approve_fixture)
    if result["status"] != "completed":
        raise AssertionError(json.dumps(result, ensure_ascii=False))
    output = result["observations"][0]["data"]["output"]
    with arcpy.da.SearchCursor(output, ["source_index", "SHAPE@WKB"]) as cursor:
        actual = {row[0]: bytes(row[1]) for row in cursor}
    expected = {index: geometry for index, geometry in original.values() if index % 2 == 0}
    with arcpy.da.SearchCursor(source, ["OID@", "source_index", "SHAPE@WKB"]) as cursor:
        unchanged = original == {row[0]: (row[1], bytes(row[2])) for row in cursor}
    checks = {"exact_selected_attributes_and_geometry": actual == expected, "crossed_900_id_boundary": len(expected) > 900,
              "source_unchanged": unchanged, "no_bulk_ids_in_audit": '"object_ids"' not in (Path(result["run_dir"]) / "events.jsonl").read_text(encoding="utf-8")}

    # A deleted/unsaved ID must fail before output creation; never export all rows.
    missing = store.capture({"dataset": source, "object_ids": [min(original), max(original) + 1000],
        "map_uri": "fixture-map", "layer_uri": "fixture-layer", "layer_name": "Missing-ID fixture"},
        {"map_uri": "fixture-map", "layer_uri": "fixture-layer"})
    failure = Agent(runtime, root).run(PlanPlanner([{"tool": "export_selection", "arguments": {
        "selection_token": missing["selection_token"], "output_name": "must_not_exist"}}]), approve=approve_fixture)
    checks["missing_id_refused_without_gdb"] = failure["status"] == "failed" and not (Path(failure["run_dir"]) / "outputs/results.gdb").exists()
    # The same dataset name within the same task cannot be reused.
    repeated = runtime.call("export_selection", {"selection_token": public["selection_token"], "output_name": "chosen_points"}, Path(result["run_dir"]) / "outputs")
    checks["existing_output_not_overwritten"] = not repeated["ok"] and int(arcpy.management.GetCount(output)[0]) == len(expected)
    summary = {"ok": all(checks.values()), "fixture_root": str(root), "capture_mode": "Synthetic snapshot; Pro GUI capture not exercised",
               "checks": checks, "selected_count": len(expected), "source_count": len(original), "approvals": approvals,
               "run_dir": result["run_dir"], "output": output, "simulated": result["simulated"]}
    (root / "selection-validation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
