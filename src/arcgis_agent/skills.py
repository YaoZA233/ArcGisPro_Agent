"""Declarative skill packs: verified multi-step method templates read by the model.

A skill is data, not an executor. The model discovers packs with list_skills, reads one
with read_skill, then issues ordinary tool calls instantiated from the templates; every
call still requires its own user approval. No eval/exec, no generated Python, no shell.

Placeholder grammar inside step arguments:
  {{params.<name>}}              a declared skill parameter the model fills from the request
  {{steps.<output_name>.<field>}} a field from an earlier step's real result path
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .catalog import TOOLS

BUILTIN_DIR = Path(__file__).with_name("skills")
SKILL_TOOLS = frozenset({"list_skills", "read_skill"})

NAME_RE = re.compile(r"[a-z][a-z0-9_]{1,63}")
PLACEHOLDER_RE = re.compile(r"\{\{(.*?)\}\}")
PARAM_REF_RE = re.compile(r"params\.([a-z][a-z0-9_]*)")
STEP_REF_RE = re.compile(r"steps\.([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)")
MAX_STEPS = 12
MAX_PARAMS = 12
LIMITS = {"title": 120, "summary": 400, "when_to_use": 400, "verification": 600,
          "param_description": 300, "note": 300, "pitfall": 300, "requires": 64}


def _text(value, label, limit):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: must be a non-empty string")
    if len(value) > limit:
        raise ValueError(f"{label}: longer than {limit} characters")
    return value.strip()


def _string_placeholders(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_placeholders(item)
    elif isinstance(value, list):
        for item in value:
            yield from _string_placeholders(item)


def _check_placeholders(value, label, param_names, prior_outputs):
    for text in _string_placeholders(value):
        for inner in PLACEHOLDER_RE.findall(text):
            inner = inner.strip()
            match = PARAM_REF_RE.fullmatch(inner)
            if match:
                if match.group(1) not in param_names:
                    raise ValueError(f"{label}: placeholder {{{{{inner}}}}} references undeclared parameter")
                continue
            match = STEP_REF_RE.fullmatch(inner)
            if match:
                if match.group(1) not in prior_outputs:
                    raise ValueError(f"{label}: placeholder {{{{{inner}}}}} references an earlier step output_name that does not exist")
                continue
            raise ValueError(f"{label}: unsupported placeholder {{{{{inner}}}}}; use params.<name> or steps.<output_name>.<field>")


def _validate_step(step, index, param_names, prior_outputs):
    label = f"steps[{index}]"
    if not isinstance(step, dict):
        raise ValueError(f"{label}: must be an object")
    extra = set(step) - {"tool", "arguments", "note"}
    if extra:
        raise ValueError(f"{label}: unknown keys {sorted(extra)}")
    tool_name = step.get("tool")
    if tool_name not in TOOLS:
        raise ValueError(f"{label}: unknown tool {tool_name!r}")
    if TOOLS[tool_name].host:
        raise ValueError(f"{label}: host-only tool {tool_name!r} cannot appear in a skill")
    arguments = step.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError(f"{label}: arguments must be an object")
    properties = TOOLS[tool_name].schema()["properties"]
    unknown = set(arguments) - set(properties)
    if unknown:
        raise ValueError(f"{label}: tool {tool_name!r} has no arguments {sorted(unknown)}")
    missing = set(TOOLS[tool_name].required) - set(arguments)
    if missing:
        raise ValueError(f"{label}: tool {tool_name!r} requires arguments {sorted(missing)}")
    for key, value in arguments.items():
        _check_placeholders(value, f"{label}.{key}", param_names, prior_outputs)
        schema = properties[key]
        if isinstance(value, str) and not PLACEHOLDER_RE.search(value):
            if "enum" in schema and value not in schema["enum"]:
                raise ValueError(f"{label}.{key}: {value!r} is not one of {schema['enum']}")
            if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
                raise ValueError(f"{label}.{key}: {value!r} does not match the required pattern")
    if "note" in step:
        _text(step["note"], f"{label}.note", LIMITS["note"])
    output = arguments.get("output_name")
    if isinstance(output, str) and not PLACEHOLDER_RE.search(output):
        prior_outputs.add(output)


def validate_skill(payload):
    """Fail fast on any drift between a pack and the live tool catalog."""
    if not isinstance(payload, dict):
        raise ValueError("skill: must be a JSON object")
    extra = set(payload) - {"name", "title", "summary", "when_to_use", "requires", "params", "steps", "verification", "pitfalls"}
    if extra:
        raise ValueError(f"skill: unknown keys {sorted(extra)}")
    name = payload.get("name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError("skill.name: must match [a-z][a-z0-9_]{1,63}")
    _text(payload.get("title"), f"{name}.title", LIMITS["title"])
    _text(payload.get("summary"), f"{name}.summary", LIMITS["summary"])
    _text(payload.get("when_to_use"), f"{name}.when_to_use", LIMITS["when_to_use"])

    requires = payload.get("requires", [])
    if not isinstance(requires, list) or any(not isinstance(item, str) or len(item) > LIMITS["requires"] for item in requires):
        raise ValueError(f"{name}.requires: must be a list of short strings")

    params = payload.get("params", [])
    if not isinstance(params, list) or not 0 <= len(params) <= MAX_PARAMS:
        raise ValueError(f"{name}.params: must be a list of at most {MAX_PARAMS} items")
    param_names = set()
    for index, param in enumerate(params):
        if not isinstance(param, dict) or set(param) - {"name", "description"}:
            raise ValueError(f"{name}.params[{index}]: must have exactly name and description")
        if not isinstance(param.get("name"), str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", param["name"]):
            raise ValueError(f"{name}.params[{index}].name: must match [a-z][a-z0-9_]{{0,63}}")
        if param["name"] in param_names:
            raise ValueError(f"{name}.params[{index}].name: duplicate parameter {param['name']!r}")
        param_names.add(param["name"])
        _text(param.get("description"), f"{name}.params[{index}].description", LIMITS["param_description"])

    steps = payload.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f"{name}.steps: must be a list of 1-{MAX_STEPS} steps")
    prior_outputs: set[str] = set()
    for index, step in enumerate(steps):
        _validate_step(step, index, param_names, prior_outputs)

    if "verification" in payload:
        _text(payload["verification"], f"{name}.verification", LIMITS["verification"])
    pitfalls = payload.get("pitfalls", [])
    if not isinstance(pitfalls, list) or len(pitfalls) > 8:
        raise ValueError(f"{name}.pitfalls: must be a list of at most 8 strings")
    for index, pitfall in enumerate(pitfalls):
        _text(pitfall, f"{name}.pitfalls[{index}]", LIMITS["pitfall"])
    return payload


def _load_dir(directory: Path, source: str, skills: dict):
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError) as exc:
            raise ValueError(f"Skill pack {path} is unreadable: {exc}") from None
        try:
            validate_skill(payload)
        except ValueError as exc:
            raise ValueError(f"Skill pack {path} is invalid: {exc}") from None
        name = payload["name"]
        if name in skills and skills[name]["source"] == "user":
            raise ValueError(f"Skill pack {path}: duplicate user skill name {name!r}")
        payload["source"] = source
        skills[name] = payload


def load_skills(user_dir: Path | None = None) -> dict:
    skills: dict = {}
    _load_dir(BUILTIN_DIR, "builtin", skills)
    if user_dir is not None:
        _load_dir(Path(user_dir), "user", skills)
    return skills


def _summary(record):
    return {"name": record["name"], "title": record["title"], "summary": record["summary"],
            "when_to_use": record["when_to_use"], "requires": record.get("requires", []),
            "params": [{"name": p["name"], "description": p["description"]} for p in record.get("params", [])],
            "step_count": len(record["steps"]), "source": record["source"]}


def list_packs(user_dir: Path | None = None) -> dict:
    return execute_skill_tool("list_skills", {}, user_dir)


def execute_skill_tool(name: str, arguments: dict, user_dir: Path | None = None) -> dict:
    skills = load_skills(user_dir)
    if name == "list_skills":
        records = [_summary(record) for record in skills.values()]
        return {"skills": records, "count": len(records),
                "usage": "选择匹配任务的技能后用 read_skill 读取完整步骤模板；模板中的占位符须替换为真实路径、参数和上一步返回的输出，再逐个调用工具，每次调用仍需用户授权。"}
    requested = arguments["name"]
    if requested not in skills:
        raise ValueError(f"Unknown skill: {requested}; call list_skills for available names")
    return dict(skills[requested])
