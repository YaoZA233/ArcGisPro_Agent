from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Protocol

from .tools import TOOLS, execute, validate_call
from .selections import SelectionStore
from .hints import STALL_HINT, TIMEOUT_HINT, hint_for


class Runtime(Protocol):
    def call(self, name: str, arguments: dict, output_dir: Path, on_progress=None) -> dict: ...


class WorkerTerminated(RuntimeError):
    """The worker had to be killed. Its stderr is kept so the failure stays diagnosable."""

    def __init__(self, cancelled: bool, stderr, stalled: bool = False):
        super().__init__("cancelled" if cancelled else "timeout")
        self.cancelled = cancelled
        self.stalled = stalled
        self.stderr = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else (stderr or "")


class ArcPyRuntime:
    def __init__(self, python: Path, timeout: int = 180, skills_dir: Path | None = None, stall_seconds: int = 180, data_roots=(), memory=None):
        self.python, self.timeout = python, timeout
        self.skills_dir = skills_dir
        self.stall_seconds = stall_seconds
        self.data_roots = tuple(str(Path(item)) for item in data_roots)
        self.memory = memory
        self.worker = Path(__file__).with_name("worker.py")
        self.cancel_event = None

    def _worker_environment(self) -> dict:
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        # Keep Pro's in-process context out of the standalone worker. This is a
        # precaution; the cause of the intermittent startup delay is not established.
        environment.pop("ESRI_SOFTWARE_CLASS", None)
        for key in ("ARCGISHOME", "ARCGIS_PRODUCT_NAME", "ARCGIS_VERSION"):
            environment.pop(key, None)
        return environment

    def _run(self, command: list[str], environment: dict, on_progress=None) -> subprocess.CompletedProcess:
        """Polled wait with live stderr forwarding and a startup-stall watchdog.

        The worker's phase lines go to stderr. They are streamed to on_progress so the
        panel/CLI can show live progress, and the first stall (no "arcpy ready" marker)
        is cut short instead of waiting for the full timeout.
        """
        start = time.monotonic()
        ready_seen = False
        stderr_parts: list[str] = []
        stdout_parts: list[str] = []

        def forward(line: str) -> None:
            nonlocal ready_seen
            if "arcpy ready" in line:
                ready_seen = True
            if on_progress is not None:
                on_progress(line)

        def pump(stream, parts, sink):
            try:
                for line in stream:
                    parts.append(line)
                    text = line.rstrip()
                    if sink is not None and text:
                        sink(text)
            except (OSError, ValueError):
                pass

        if on_progress is not None:
            on_progress("[runtime] starting ArcPy worker process")
        # The RPC process reads control messages from stdin. Workers use a file
        # protocol and must never inherit or compete for that same input pipe.
        with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", errors="replace", env=environment, shell=False) as process:
            readers = [threading.Thread(target=pump, args=(process.stdout, stdout_parts, None), daemon=True),
                       threading.Thread(target=pump, args=(process.stderr, stderr_parts, forward), daemon=True)]
            for reader in readers:
                reader.start()
            try:
                while True:
                    cancelled = self.cancel_event is not None and self.cancel_event.is_set()
                    elapsed = time.monotonic() - start
                    stalled = not ready_seen and elapsed > self.stall_seconds
                    if cancelled or elapsed > self.timeout or stalled:
                        try:
                            process.kill()
                        except OSError:
                            pass
                        # Reap readers first so the stderr tail includes every line the worker printed.
                        for reader in readers:
                            reader.join(timeout=5)
                        raise WorkerTerminated(cancelled, "".join(stderr_parts), stalled=stalled and not cancelled)
                    if process.poll() is not None:
                        for reader in readers:
                            reader.join(timeout=5)
                        return subprocess.CompletedProcess(command, process.returncode,
                                                           "".join(stdout_parts), "".join(stderr_parts))
                    time.sleep(0.2)
            finally:
                for reader in readers:
                    reader.join(timeout=5)

    def call(self, name: str, arguments: dict, output_dir: Path, on_progress=None) -> dict:
        from .skills import SKILL_TOOLS, execute_skill_tool
        if name in SKILL_TOOLS:
            # Local skill reads run in process; spawning an ArcPy worker would waste ~10s per call.
            validate_call(name, arguments)
            try:
                return {"ok": True, "data": execute_skill_tool(name, arguments, self.skills_dir)}
            except (ValueError, OSError) as exc:
                return {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
        from .memory import MEMORY_TOOLS, execute_memory_tool
        if name in MEMORY_TOOLS:
            # Memory writes are local agent state, handled in process like skill reads.
            validate_call(name, arguments)
            try:
                return {"ok": True, "data": execute_memory_tool(name, arguments, self.memory)}
            except (ValueError, OSError) as exc:
                return {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
        return self._call(name, arguments, output_dir, on_progress=on_progress)

    def _spawn(self, command: list[str], environment: dict):
        try:
            return subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=self.timeout, env=environment, shell=False)
        except subprocess.TimeoutExpired as expired:
            raise WorkerTerminated(False, expired.stderr) from None

    def export_selection(self, snapshot, arguments, output_dir, on_progress=None):
        return self._call("export_selection", arguments, output_dir, snapshot.payload(), on_progress=on_progress)

    @staticmethod
    def _complete(result_path: Path):
        """A result file counts only when it parses whole; a torn write is treated as absent."""
        if not result_path.is_file():
            return None
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        return payload if isinstance(payload, dict) and type(payload.get("ok")) is bool else None

    def _termination_error(self, killed: WorkerTerminated) -> dict:
        tail = killed.stderr[-3000:]
        if killed.cancelled:
            error = {"type": "Cancelled", "message": "ArcPy 执行已停止，部分成果可能保留；请检查任务目录后再重试。"}
            hint = hint_for(tail)
        elif killed.stalled:
            error = {"type": "Stalled", "message":
                     f"ArcPy worker 启动超过 {self.stall_seconds}s 未就绪，已终止运行；请查看运行日志后重试。"}
            hint = hint_for(tail) or STALL_HINT
        else:
            error = {"type": "Timeout", "message":
                     f"ArcPy exceeded {self.timeout}s; worker terminated. Partial outputs may remain; inspect before retrying."}
            hint = hint_for(tail) or TIMEOUT_HINT
        if tail:
            error["stderr"] = tail
        if hint:
            error["hint"] = hint
        return error

    def _call(self, name, arguments, output_dir, selection_snapshot=None, on_progress=None):
        validate_call(name, arguments)
        directory = Path(tempfile.mkdtemp(prefix="arcgis_agent_"))
        try:
            request, result = directory / "request.json", directory / "result.json"
            request.write_text(json.dumps({"tool": name, "arguments": arguments,
                                           "output_dir": str(output_dir.resolve()), "selection_snapshot": selection_snapshot,
                                           "data_roots": list(self.data_roots)}, ensure_ascii=False), encoding="utf-8")
            environment = self._worker_environment()
            command = [str(self.python), str(self.worker), str(request), str(result)]
            try:
                process = self._spawn(command, environment) if self.cancel_event is None else self._run(command, environment, on_progress)
            except WorkerTerminated as killed:
                payload = self._complete(result)
                if payload is None:
                    return {"ok": False, "error": self._termination_error(killed)}
                payload["worker_terminated"] = True
                return payload
            if not result.is_file():
                error = {"type": "WorkerFailure", "message":
                         f"ArcPy worker exited {process.returncode}: {process.stderr[-3000:]}"}
                hint = hint_for(process.stderr or "")
                if hint:
                    error["hint"] = hint
                return {"ok": False, "error": error}
            try:
                payload = json.loads(result.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or type(payload.get("ok")) is not bool:
                    raise ValueError("Missing boolean ok field")
                if process.returncode != 0 and payload["ok"]:
                    raise ValueError("Worker exit status contradicts success result")
                return payload
            except (ValueError, OSError) as exc:
                return {"ok": False, "error": {"type": "ProtocolError", "message": str(exc)}}
        finally:
            try:
                shutil.rmtree(directory)
            except OSError:
                # A locked protocol folder must not swallow a completed tool result.
                pass


class InProcessRuntime:
    """For Pro's toolbox only: permits CURRENT and preserves the active session."""
    def __init__(self, skills_dir: Path | None = None, data_roots=(), memory=None):
        self.skills_dir = skills_dir
        self.data_roots = tuple(str(Path(item)) for item in data_roots)
        self.memory = memory

    def call(self, name: str, arguments: dict, output_dir: Path, on_progress=None) -> dict:
        try:
            return {"ok": True, "data": execute(name, arguments, output_dir, allow_current=True, skills_dir=self.skills_dir,
                                                data_roots=self.data_roots, memory=self.memory)}
        except Exception as exc:
            return {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}

    def export_selection(self, snapshot, arguments, output_dir, on_progress=None):
        try:
            return {"ok": True, "data": execute("export_selection", arguments, output_dir, selection_snapshot=snapshot.payload())}
        except Exception as exc:
            return {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}


class MockRuntime:
    """Explicit offline simulation; never pretends to create GIS deliverables."""
    def __init__(self, skills_dir: Path | None = None, memory=None):
        self.skills_dir = skills_dir
        self.memory = memory

    def call(self, name: str, arguments: dict, output_dir: Path, on_progress=None) -> dict:
        validate_call(name, arguments)
        from .skills import SKILL_TOOLS, execute_skill_tool
        if name in SKILL_TOOLS:
            # Skill packs are real local data, not simulated deliverables.
            return {"ok": True, "data": execute_skill_tool(name, arguments, self.skills_dir)}
        from .memory import MEMORY_TOOLS, execute_memory_tool
        if name in MEMORY_TOOLS:
            # Memory is real local agent state, not a simulated GIS deliverable.
            try:
                return {"ok": True, "data": execute_memory_tool(name, arguments, self.memory)}
            except (ValueError, OSError) as exc:
                return {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
        return {"ok": True, "simulated": True, "data": {"tool": name, "arguments": arguments,
                "message": "Offline simulation only; no ArcPy execution or GIS output."}}

    def export_selection(self, snapshot, arguments, output_dir, on_progress=None):
        return self.call("export_selection", arguments, output_dir)


class HostBridgeRuntime:
    def __init__(self, base, broker, enabled=False, selections=None):
        self.base, self.broker, self.enabled = base, broker, enabled
        self.selections = selections if selections is not None else SelectionStore()

    def approval_context(self, name, arguments):
        if name == "export_selection":
            if not self.enabled:
                raise ValueError("选择集导出仅在 Pro 面板会话可用。")
            return self.selections.get(arguments["selection_token"]).public()
        return None

    @property
    def cancel_event(self):
        return getattr(self.base, "cancel_event", None)

    @cancel_event.setter
    def cancel_event(self, value):
        if hasattr(self.base, "cancel_event"):
            self.base.cancel_event = value

    def call(self, name, arguments, output_dir, on_progress=None):
        validate_call(name, arguments)
        if TOOLS[name].host:
            if not self.enabled:
                return {"ok": False, "error": {"type": "HostUnavailable", "message": "此工具仅在 ArcGIS Pro 聊天面板可用。"}}
            if name == "export_selection":
                snapshot = self.selections.get(arguments["selection_token"])
                return self.base.export_selection(snapshot, arguments, output_dir)
            result = self.broker.request("host", {"tool": name, "arguments": arguments}, timeout=60)
            if name == "get_selection" and result["ok"]:
                # Strip bulk IDs before any model, UI history or audit observation.
                return {"ok": True, "data": self.selections.capture(result.get("data"), arguments)}
            return result
        return self.base.call(name, arguments, output_dir, on_progress=on_progress)
