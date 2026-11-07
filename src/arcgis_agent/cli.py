from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import Agent, PlanPlanner, validate_plan
from .config import Settings, discover_python
from .runtime import ArcPyRuntime, MockRuntime
from .tools import TOOLS


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ArcGIS Pro 3.5.2 Agent foundation")
    parser.add_argument("--config", type=Path, help="TOML configuration path")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Verify licensed ArcPy and exact target version")
    commands.add_parser("tools", help="List the structured tool contract")
    commands.add_parser("skills", help="List declarative multi-step skill packs")
    commands.add_parser("demo", help="Run an explicitly simulated task without ArcGIS or an API key")
    key = commands.add_parser("setup-key", help="Securely enter a provider key in a local ignored file")
    key.add_argument("--provider", default="deepseek", help="Connection ID; defaults to deepseek")
    providers = commands.add_parser("providers", help="Manage model connections without exposing credentials")
    actions = providers.add_subparsers(dest="provider_action", required=True)
    actions.add_parser("list", help="List connections and key presence (never key values)")
    actions.add_parser("presets", help="List editable provider templates")
    save = actions.add_parser("save", help="Save a JSON profile (credentials excluded)")
    save.add_argument("profile", type=Path)
    for verb in ("use", "delete"):
        item = actions.add_parser(verb)
        item.add_argument("provider")
    chat = commands.add_parser("chat", help="Interactive multi-provider GIS assistant")
    chat.add_argument("--allow-writes", action="store_true")
    chat.add_argument("--approval-mode", choices=("ask", "full"), help="Tool authorization: ask (default) or full (automatic)")
    commands.add_parser("rpc", help=argparse.SUPPRESS)
    run = commands.add_parser("run", help="Execute a versioned JSON plan; no natural-language model yet")
    run.add_argument("plan", type=Path)
    run.add_argument("--mock", action="store_true")
    run.add_argument("--allow-writes", action="store_true")
    run.add_argument("--approval-mode", choices=("ask", "full"), help="Tool authorization: ask (default) or full (automatic)")
    args = parser.parse_args(argv)
    try:
        local_config = Path(__file__).resolve().parents[2] / "config" / "local.toml"
        settings = Settings.load(args.config or (local_config if local_config.is_file() else None))
        if args.command == "providers":
            from .interfaces import ROOT
            from .providers import PRESETS, ProviderStore, preset_profile
            store = ProviderStore(ROOT, settings.workspace, settings)
            if args.provider_action == "presets":
                print_json([preset_profile(p) for p in PRESETS])
            elif args.provider_action == "list":
                print_json({"active": store.active, "profiles": store.public()})
            elif args.provider_action == "save":
                print_json(store.save(json.loads(args.profile.read_text(encoding="utf-8-sig"))))
            elif args.provider_action == "use":
                store.select(args.provider)
                print_json({"active": store.active})
            else:
                store.delete(args.provider)
                print_json({"deleted": args.provider})
            return 0
        if args.command in ("setup-key", "chat", "rpc"):
            from .interfaces import interactive, rpc, setup_key
            if args.command == "setup-key":
                return setup_key(args.provider)
            if args.command == "chat":
                return interactive(settings, args.allow_writes, args.approval_mode)
            return rpc(settings)
        if args.command == "skills":
            from .skills import list_packs
            print_json(list_packs(settings.skills_dir))
            return 0
        if args.command == "tools":
            print_json({name: {"description": t.description, "required": list(t.required), "parameters": t.schema(),
                               "writes": t.writes, "host": t.host, "arcgis_tool": t.arcgis_tool} for name, t in TOOLS.items()})
            return 0
        if args.command == "demo":
            steps = [{"tool": "environment", "arguments": {}},
                     {"tool": "describe", "arguments": {"dataset": "SIMULATED_DATASET"}}]
            from .approvals import terminal_approval
            result = Agent(MockRuntime(), settings.workspace, settings.max_steps).run(
                PlanPlanner(steps), approve=terminal_approval, approval_mode=settings.approval_mode)
        elif args.command == "doctor":
            python = discover_python(settings.python)
            probe = ArcPyRuntime(python, settings.timeout_seconds).call("environment", {}, settings.workspace)
            actual = probe.get("data", {}).get("version")
            compatible = actual == settings.expected_version
            licensed = probe.get("data", {}).get("product") not in (None, "NotInitialized", "Unavailable")
            healthy = probe["ok"] and compatible and licensed
            print_json({"ok": healthy, "python": str(python), "expected_version": settings.expected_version,
                        "version_matches": compatible, "licensed": licensed, "probe": probe})
            return 0 if healthy else 1
        else:
            plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
            steps = validate_plan(plan, settings.max_steps)
            from .memory import MemoryStore
            memory = MemoryStore(settings.workspace / "memory.json")
            runtime = MockRuntime(settings.skills_dir, memory) if args.mock else ArcPyRuntime(discover_python(settings.python), settings.timeout_seconds, settings.skills_dir, data_roots=settings.data_roots, memory=memory)
            from .approvals import terminal_approval
            result = Agent(runtime, settings.workspace, settings.max_steps).run(
                PlanPlanner(steps), allow_writes=args.allow_writes, approve=terminal_approval,
                approval_mode=args.approval_mode or settings.approval_mode)
        print_json(result)
        return 0 if result["status"] == "completed" else 1
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print_json({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
