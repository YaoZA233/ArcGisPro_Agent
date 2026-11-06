from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    workspace: Path
    python: Path | None = None
    expected_version: str = "3.5.2"
    timeout_seconds: int = 180
    max_steps: int = 16
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    model_timeout_seconds: int = 120
    thinking: str = "high"
    max_tokens: int = 8192
    provider: str = "deepseek"
    skills_dir: Path | None = None
    data_roots: tuple[Path, ...] = ()
    failure_budget: int = 3

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        base = path.resolve().parent if path else Path.cwd()
        data = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path else {}
        arcgis, agent, model = data.get("arcgis", {}), data.get("agent", {}), data.get("model", {})

        def resolve(value: str) -> Path:
            p = Path(value).expanduser()
            return (base / p).resolve() if not p.is_absolute() else p.resolve()

        executable = os.environ.get("ARCGIS_PRO_PYTHON") or arcgis.get("python")
        timeout = arcgis.get("timeout_seconds", 180)
        steps = agent.get("max_steps", 16)
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise ValueError("timeout_seconds must be an integer between 1 and 3600")
        if type(steps) is not int or not 1 <= steps <= 100:
            raise ValueError("max_steps must be an integer between 1 and 100")
        failure_budget = agent.get("failure_budget", 3)
        if type(failure_budget) is not int or not 1 <= failure_budget <= 20:
            raise ValueError("failure_budget must be an integer between 1 and 20")
        model_timeout = model.get("timeout_seconds", 120)
        if type(model_timeout) is not int or not 1 <= model_timeout <= 600:
            raise ValueError("model timeout_seconds must be an integer between 1 and 600")
        name, base_url = model.get("name", "deepseek-flash"), model.get("base_url", "https://api.deepseek.com")
        thinking, max_tokens = model.get("thinking", "high"), model.get("max_tokens", 8192)
        if thinking not in ("off", "low", "high", "max"):
            raise ValueError("thinking must be off, low, high or max")
        if type(max_tokens) is not int or not 256 <= max_tokens <= 65536:
            raise ValueError("max_tokens must be between 256 and 65536")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("model name must be nonempty")
        roots = agent.get("data_roots", [])
        if not isinstance(roots, list) or len(roots) > 16 or any(not isinstance(item, str) or not item.strip() for item in roots):
            raise ValueError("data_roots must be a list of at most 16 nonempty directory paths")
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("model base_url must be an HTTPS URL without credentials, query or fragment")
        return cls(resolve(agent.get("workspace", "workspace")),
                   resolve(executable) if executable else None,
                   arcgis.get("expected_version", "3.5.2"), timeout, steps,
                   name, base_url.rstrip("/"), model_timeout, thinking, max_tokens, model.get("provider", "deepseek"),
                   resolve(agent["skills_dir"]) if agent.get("skills_dir") else None,
                   tuple(resolve(item) for item in roots), failure_budget)


def discover_python(explicit: Path | None = None) -> Path:
    """Explicit paths fail fast; never silently run against another installation."""
    if explicit is not None:
        if not explicit.is_file() or explicit.name.lower() != "python.exe":
            raise ValueError(f"ArcGIS Python executable not found: {explicit}")
        return explicit.resolve()
    candidates: list[Path] = []
    if sys.platform == "win32":
        import winreg

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\ESRI\ArcGISPro", 0,
                                        winreg.KEY_READ | view) as key:
                        root = Path(winreg.QueryValueEx(key, "InstallDir")[0])
                        candidates.append(root / "bin/Python/envs/arcgispro-py3/python.exe")
                except OSError:
                    continue
        for root in (os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if root:
                candidates.append(Path(root) / "ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError("ArcGIS Pro Python not found. Set ARCGIS_PRO_PYTHON to a licensed Pro/clone python.exe.")
