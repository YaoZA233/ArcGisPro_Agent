"""Worker-protocol reliability: a killed or badly-behaved worker must not lose its evidence."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from arcgis_agent.chat import ChatSession
from arcgis_agent.runtime import ArcPyRuntime, STALL_HINT, TIMEOUT_HINT
from arcgis_agent import worker as worker_module

ARGUMENTS = {"dataset": "C:/data/missing.shp"}

HANG_AFTER_RESULT = """
import json, sys, time
from pathlib import Path
Path(sys.argv[2]).write_text(json.dumps({"ok": True, "data": {"x": 1}}), encoding="utf-8")
print("worker still holding the geoprocessor", file=sys.stderr, flush=True)
time.sleep(30)
"""

HANG_WITHOUT_RESULT = """
import sys, time
print("ERROR 000732: Dataset does not exist or is inaccessible", file=sys.stderr, flush=True)
time.sleep(30)
"""

HANG_BEFORE_READY = """
import sys, time
print("[worker] tool=describe starting", file=sys.stderr, flush=True)
time.sleep(30)
"""

PROGRESS_THEN_SUCCESS = """
import json, sys
from pathlib import Path
print("[worker] tool=describe starting", file=sys.stderr, flush=True)
print("[tools] importing arcpy (ArcPy runtime startup; may wait while Pro is busy)", file=sys.stderr, flush=True)
print("[tools] arcpy ready in 0.1s", file=sys.stderr, flush=True)
Path(sys.argv[2]).write_text(json.dumps({"ok": True, "data": {"x": 3}}), encoding="utf-8")
"""

IMMEDIATE_SUCCESS = """
import json, sys
from pathlib import Path
Path(sys.argv[2]).write_text(json.dumps({"ok": True, "data": {"x": 2}}), encoding="utf-8")
"""

READ_STDIN_THEN_SUCCESS = """
import json, sys
from pathlib import Path
content = sys.stdin.read()
Path(sys.argv[2]).write_text(json.dumps({"ok": True, "data": {"stdin": content}}), encoding="utf-8")
"""


class TerminatedWorkerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="arcgis_agent_stub_"))

    def runtime(self, source, timeout=1, cancel=False):
        script = self.root / "worker_stub.py"
        script.write_text(source, encoding="utf-8")
        runtime = ArcPyRuntime(Path(sys.executable), timeout)
        runtime.worker = script
        if cancel:
            runtime.cancel_event = threading.Event()
        return runtime

    def test_result_written_before_a_timeout_is_still_returned(self):
        result = self.runtime(HANG_AFTER_RESULT).call("describe", ARGUMENTS, self.root)
        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("data"), {"x": 1})
        self.assertEqual(result.get("worker_terminated"), True)

    def test_timeout_keeps_worker_stderr_and_specific_hint(self):
        result = self.runtime(HANG_WITHOUT_RESULT).call("describe", ARGUMENTS, self.root)
        self.assertEqual(result["error"]["type"], "Timeout")
        self.assertIn("000732", result["error"]["stderr"])
        self.assertNotEqual(result["error"]["hint"], TIMEOUT_HINT)

    def test_cancel_is_reported_as_cancel_not_failure(self):
        runtime = self.runtime(HANG_WITHOUT_RESULT, timeout=30, cancel=True)
        timer = threading.Timer(1.0, runtime.cancel_event.set)
        timer.start()
        try:
            result = runtime.call("describe", ARGUMENTS, self.root)
        finally:
            timer.cancel()
        self.assertEqual(result["error"]["type"], "Cancelled")
        self.assertIn("停止", result["error"]["message"])
        self.assertIn("000732", result["error"]["stderr"])

    def test_completed_process_is_not_reported_as_terminated(self):
        result = self.runtime(IMMEDIATE_SUCCESS).call("describe", ARGUMENTS, self.root)
        self.assertEqual(result.get("data"), {"x": 2})
        self.assertNotIn("worker_terminated", result)

    def test_worker_stdin_is_closed_for_both_execution_paths(self):
        for cancel in (False, True):
            with self.subTest(streaming=cancel):
                runtime = self.runtime(READ_STDIN_THEN_SUCCESS, timeout=5, cancel=cancel)
                result = runtime.call("describe", ARGUMENTS, self.root)
                self.assertEqual(result.get("data"), {"stdin": ""})

    def test_worker_does_not_wait_for_open_rpc_input_pipe(self):
        script = self.root / "stdin_worker.py"
        script.write_text(READ_STDIN_THEN_SUCCESS, encoding="utf-8")
        parent = """
import json, sys, threading
from pathlib import Path
from arcgis_agent.runtime import ArcPyRuntime
root = Path(sys.argv[1])
runtime = ArcPyRuntime(Path(sys.executable), timeout=4)
runtime.worker = root / 'stdin_worker.py'
runtime.cancel_event = threading.Event()
result = runtime.call('environment', {}, root)
print(json.dumps(result), flush=True)
"""
        process = subprocess.Popen([sys.executable, "-c", parent, str(self.root)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8")
        try:
            # Keep the parent's control pipe open and empty until its worker exits.
            process.wait(timeout=8)
            result = json.loads(process.stdout.read())
            self.assertEqual(result.get("data"), {"stdin": ""})
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()

    def test_startup_stall_is_cut_short_with_live_stderr(self):
        runtime = self.runtime(HANG_BEFORE_READY, timeout=30, cancel=True)
        runtime.stall_seconds = 1
        result = runtime.call("describe", ARGUMENTS, self.root)
        self.assertEqual(result["error"]["type"], "Stalled")
        self.assertIn("未就绪", result["error"]["message"])
        self.assertIn("starting", result["error"]["stderr"])
        self.assertEqual(result["error"]["hint"], STALL_HINT)

    def test_worker_progress_is_streamed_to_caller(self):
        progress = []
        runtime = self.runtime(PROGRESS_THEN_SUCCESS, timeout=10, cancel=True)
        result = runtime.call("describe", ARGUMENTS, self.root, on_progress=progress.append)
        self.assertEqual(result.get("data"), {"x": 3})
        self.assertTrue(any("arcpy ready" in line for line in progress))
        self.assertTrue(any("importing arcpy" in line for line in progress))

    def test_progress_arrives_before_a_hung_worker_is_cancelled(self):
        runtime = self.runtime(HANG_BEFORE_READY, timeout=30, cancel=True)
        received = threading.Event()
        result = []
        thread = threading.Thread(target=lambda: result.append(runtime.call(
            "describe", ARGUMENTS, self.root,
            on_progress=lambda line: received.set() if "starting" in line and "[worker]" in line else None)))
        thread.start()
        try:
            self.assertTrue(received.wait(5), "worker output must arrive while the worker is still running")
            self.assertTrue(thread.is_alive())
        finally:
            runtime.cancel_event.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0]["error"]["type"], "Cancelled")

    def test_worker_environment_drops_pro_app_context(self):
        runtime = ArcPyRuntime(Path(sys.executable), 10)
        with patch.dict(os.environ, {"ESRI_SOFTWARE_CLASS": "Professional"}):
            environment = runtime._worker_environment()
        self.assertNotIn("ESRI_SOFTWARE_CLASS", environment)
        self.assertEqual(environment["PYTHONIOENCODING"], "utf-8")


class TempCleanupTests(unittest.TestCase):
    def test_locked_protocol_directory_does_not_swallow_success(self):
        root = Path(tempfile.mkdtemp(prefix="arcgis_agent_stub_"))
        script = root / "worker_stub.py"
        script.write_text(IMMEDIATE_SUCCESS, encoding="utf-8")
        runtime = ArcPyRuntime(Path(sys.executable))
        runtime.worker = script
        with patch("arcgis_agent.runtime.shutil.rmtree", side_effect=PermissionError("locked")) as remove:
            result = runtime.call("describe", ARGUMENTS, root)
        self.assertEqual(result.get("data"), {"x": 2})
        self.assertEqual(remove.call_count, 1)


class WorkerSerializationTests(unittest.TestCase):
    def run_worker(self, root, **changes):
        request, result = root / "request.json", root / "result.json"
        request.write_text(json.dumps({"tool": "describe", "arguments": ARGUMENTS, "output_dir": str(root)}), encoding="utf-8")
        with patch.object(worker_module, "execute", **changes), patch.object(sys, "argv", ["worker", str(request), str(result)]):
            code = worker_module.main()
        return code, json.loads(result.read_text(encoding="utf-8"))

    def test_unserializable_success_becomes_an_explicit_failure(self):
        root = Path(tempfile.mkdtemp(prefix="arcgis_agent_stub_"))
        code, payload = self.run_worker(root, return_value={"handle": object()})
        self.assertEqual(code, 1)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["error"]["type"], "SerializationError")

    def test_exception_with_unserializable_detail_still_writes_a_result(self):
        root = Path(tempfile.mkdtemp(prefix="arcgis_agent_stub_"))
        code, payload = self.run_worker(root, side_effect=RuntimeError({"raw": object()}))
        self.assertEqual(code, 1)
        self.assertEqual(payload["ok"], False)

    def test_worker_announces_tool_on_stderr_before_execution(self):
        root = Path(tempfile.mkdtemp(prefix="arcgis_agent_stub_"))
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code, payload = self.run_worker(root, return_value={"ok": True})
        self.assertEqual(code, 0)
        self.assertEqual(payload["ok"], True)
        self.assertIn("[worker] tool=describe starting", buf.getvalue())


class ChatRestoreTests(unittest.TestCase):
    def test_foreign_history_shape_is_rejected_on_load(self):
        root = Path(tempfile.mkdtemp(prefix="arcgis_agent_stub_"))
        session = ChatSession(object(), None, root)
        folder = root / "chats"
        folder.mkdir(parents=True)
        (folder / (session.session_id + ".json")).write_text(json.dumps(
            {"version": 1, "session_id": session.session_id, "title": "t", "updated": "now",
             "turns": [], "history": ["not-a-message"], "total_usage": session.total_usage}), encoding="utf-8")
        with self.assertRaises(ValueError) as refused:
            session.load(session.session_id)
        self.assertIn("历史", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
