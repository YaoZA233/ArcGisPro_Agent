"""Opt-in live DeepSeek check: synthetic image, returned reasoning, real GIS tool.

Loads the configured local credential without printing it. May incur API charges.
"""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arcgis_agent.config import Settings
from arcgis_agent.interfaces import create_session
from arcgis_agent.providers import CompatibleClient, get_key, preset_profile
from arcgis_agent.approvals import terminal_approval


def main():
    local = ROOT / "config/local.toml"
    settings = Settings.load(local) if local.is_file() else Settings(workspace=ROOT / "workspace")
    session = create_session(settings)
    # This opt-in check targets DeepSeek without changing the user's active connection.
    session.switch_client(CompatibleClient(get_key(ROOT, "deepseek"), preset_profile("deepseek"), settings.model_timeout_seconds))
    checks = []
    models = session.client.list_models()
    print(json.dumps({"models": models}, ensure_ascii=False), flush=True)
    fixture = ROOT / "workspace/vision-test.png"
    cases = [("thinking", "只计算 13+29，并简短回答。", {"thinking": "low", "max_tokens": 1024}, []),
             ("tool", "请务必调用 environment 工具，检查真实 ArcGIS Pro 版本，然后用一句中文报告版本号。", {"thinking": "off", "max_tokens": 512}, [])]
    if fixture.is_file():
        cases.insert(0, ("vision", "图片里的圆形是什么颜色？用一句话回答。", {"thinking": "off", "max_tokens": 512}, [str(fixture)]))
    for name, prompt, options, attachments in cases:
        events = []
        result = session.send(prompt, options={"model": "deepseek-flash", **options}, attachments=attachments, on_event=events.append, approve=terminal_approval)
        passed = result["status"] == "completed" and result["usage_available"]
        if name == "thinking": passed &= any(e["event"] == "reasoning_delta" for e in events)
        if name == "tool": passed &= any(o["tool"] == "environment" and o["ok"] and o.get("data", {}).get("version") == "3.5.2" for o in result["observations"])
        check = {"check": name, "passed": passed, "model": result["model"], "reply": result["reply"],
                 "usage": result["usage"], "reasoning_chars": sum(len(e["text"]) for e in events if e["event"] == "reasoning_delta"),
                 "elapsed_seconds": result["elapsed_seconds"], "run_dir": result["run_dir"]}
        checks.append(check)
        print(json.dumps(check, ensure_ascii=False), flush=True)
        session.reset()
    target = ROOT / "workspace/chat-validation.json"
    target.write_text(json.dumps({"models": models, "checks": checks}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(c["passed"] for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
