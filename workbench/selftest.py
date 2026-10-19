"""One bounded, isolated diagnostic run; no business data or account access."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import NotRequired, TypedDict

import psutil

from .database import Conflict, Missing, now
from .paths import ROOT
from .queue import kill_tree
from .process_scope import ProcessScope

STEPS = [
    ("runtime", "运行环境"), ("host", "后台与页面"),
    ("queue", "任务队列"), ("skill", "Skill 连接"),
    ("data", "数据与导出"), ("recovery", "重开恢复"),
    ("network", "免登录联网试采"),
]
ACTIVE = {"running", "cancelling"}


class StageTiming(TypedDict):
    state: str
    elapsed_seconds: float
    reason: NotRequired[str]


class PhaseProgress(StageTiming):
    budget_seconds: float
    stopped: NotRequired[bool]
    error: NotRequired[str]
    exit_code: NotRequired[int | None]


class DiagnosticStep(StageTiming):
    id: str
    name: str
    message: str
    attempted: bool


class DiagnosticResult(TypedDict):
    id: str
    state: str
    started: str
    finished: str | None
    elapsed_seconds: float
    include_network: bool
    message: str
    steps: list[DiagnosticStep]
    phases: dict[str, PhaseProgress]
    timings: dict[str, dict[str, StageTiming]]
    cleanup_complete: NotRequired[bool]
    local_state: NotRequired[str]
    network_state: NotRequired[str]
    recovery_pending: NotRequired[bool]


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    try:
        # Windows readers can briefly deny replacement of an open result file.
        # Retain the previous complete JSON and retry only this transient error.
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def local_result(steps):
    states = [s["state"] for s in steps if s["id"] != "network"]
    if "failed" in states:
        return "failed"
    return "passed" if states and all(s == "passed" for s in states) else "incomplete"


class SelfTests:
    def __init__(self, root, *, budget=180):
        self.directory = Path(root).resolve() / "diagnostics" / "self-test"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.file = self.directory / "latest.json"
        self.workspace = self.directory / "workspace"
        self.lock = threading.RLock()
        self.thread = None
        self.cancel_event = threading.Event()
        self.budget = budget
        self.closed = False
        self.owner_file = self.directory / "owners.json"
        self.cleanup_thread = None
        self.result = read_json(self.file)
        self._recover()

    def _clean(self):
        if self.workspace.exists():
            if self.workspace.is_symlink() or self.workspace.resolve().parent != self.directory.resolve():
                raise RuntimeError("Unexpected diagnostic workspace")
            shutil.rmtree(self.workspace)

    def _recover(self):
        manifest = read_json(self.owner_file, {})
        owners = manifest.get("workers", []) if isinstance(manifest, dict) else []
        owners = [owner for owner in owners if isinstance(owner, dict)] if isinstance(owners, list) else []
        legacy = read_json(self.workspace / "owner.json", {})
        if isinstance(legacy, dict) and legacy:
            owners.append({**legacy, "directory": str(self.workspace)})
        for owner in owners:
            try:
                directory = Path(owner["directory"]).resolve()
                if directory != self.workspace and directory.parent != self.workspace:
                    continue
                p = psutil.Process(owner["pid"])
                if (abs(p.create_time() - owner["created"]) < 0.01
                        and "workbench.selftest_runner" in p.cmdline()
                        and str(directory) in p.cmdline()):
                    kill_tree(p.pid, owner["created"])
            except (psutil.Error, KeyError, TypeError, ValueError):
                pass
        if self.result and self.result["state"] in ACTIVE:
            self.result.update(state="interrupted", finished=now(), message="软件已重开，上次检查中断。")
            for step in self.result["steps"]:
                if step["state"] == "running":
                    step.update(state="interrupted", message="检查中断。", reason="host_restart")
                elif step["state"] == "pending":
                    step.update(state="skipped", attempted=False, reason="host_restart", message="软件重开，此项未执行。")
            for phase in self.result.get("phases", {}).values():
                if phase["state"] == "running":
                    phase.update(state="interrupted", reason="host_restart")
            for phase in self.result.get("timings", {}).values():
                for timing in phase.values():
                    if timing["state"] == "running":
                        timing.update(state="interrupted", reason="host_restart")
            atomic_json(self.file, self.result)
        try:
            self._clean()
            self.owner_file.unlink(missing_ok=True)
        except (OSError, RuntimeError):
            # Never erase outside the owned workspace; retry before the next run.
            pass

    def _save(self):
        atomic_json(self.file, self.result)

    def active(self):
        with self.lock:
            return bool(self.result and self.result["state"] in ACTIVE)

    def view(self, identity: str | None = None) -> DiagnosticResult | None:
        with self.lock:
            if identity and (not self.result or self.result["id"] != identity):
                raise Missing(identity)
            if not self.result:
                return None
            value = json.loads(json.dumps(self.result))
            value["local_state"] = local_result(value["steps"])
            value["network_state"] = value["steps"][-1]["state"]
            value["recovery_pending"] = value["state"] not in ACTIVE and (
                self.workspace.exists() or self.owner_file.exists())
            return value

    def start(self, include_network: bool = True) -> DiagnosticResult:
        with self.lock:
            if self.closed:
                raise Conflict("软件正在退出或更新，请稍后重试。")
            if self.active():
                return self.view()
            if self.cleanup_thread and self.cleanup_thread.is_alive():
                raise Conflict("上次检查仍在清理临时内容，请稍后重试。")
            try:
                self._clean()
                self.owner_file.unlink(missing_ok=True)
            except (OSError, RuntimeError):
                raise Conflict("上次检查的临时内容尚未清理，请稍后重试。") from None
            self.workspace.mkdir()
            self.cancel_event.clear()
            self.result = {
                "id": uuid.uuid4().hex, "state": "running", "started": now(),
                "finished": None, "elapsed_seconds": 0, "include_network": include_network,
                "message": "正在独立环境中检查，不会改动正式数据。",
                "steps": [dict(id=k, name=n, state="pending", message="", elapsed_seconds=0,
                               attempted=False, reason="") for k, n in STEPS],
                "phases": {}, "timings": {},
            }
            if not include_network:
                self.result["steps"][-1].update(state="skipped", message="本次仅检查本机。", reason="disabled")
            self._save()
            self.thread = threading.Thread(target=self._run, name="workbench-self-test", daemon=True)
            self.thread.start()
            return self.view()

    def cancel(self, identity: str) -> DiagnosticResult:
        with self.lock:
            self.view(identity)
            if self.active():
                self.result.update(state="cancelling", message="正在停止检查并清理临时内容…")
                self.cancel_event.set()
                self._save()
            return self.view()

    def _progress(self, directory: Path, phase: str, started: float) -> None:
        progress = read_json(directory / "progress.json", {})
        with self.lock:
            for step in self.result["steps"]:
                if (step["id"] == "network") == (phase == "network") and step["id"] in progress:
                    update = progress[step["id"]]
                    step.update(update)
                    step["attempted"] = update.get("attempted", update.get("state") != "pending")
            timings = self.result["timings"].setdefault(phase, {})
            for key, timing in progress.get("timings", {}).items():
                timings[key] = {k: v for k, v in timing.items() if not k.startswith("_")}
                if timing.get("state") == "running" and "_started" in timing:
                    timings[key]["elapsed_seconds"] = round(time.monotonic() - timing["_started"], 3)
            self.result["elapsed_seconds"] = round(time.monotonic() - started, 1)
            self._save()

    def _stop_phase(self, process: subprocess.Popen | None, scope: ProcessScope | None, deadline: float) -> bool:
        """Close this phase's job before the next phase is allowed to start."""
        descendants = []
        if process:
            try:
                descendants = psutil.Process(process.pid).children(recursive=True)
            except psutil.Error:
                pass
        if scope:
            try:
                scope.close()
            except OSError:
                # Still attempt to stop the exact children observed above.
                pass
        # On POSIX there is no Windows job; also reap any redirector descendants.
        for child in descendants:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error:
                return False
        if process and process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            except OSError:
                return False
        if process:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except (subprocess.TimeoutExpired, OSError):
                return False
        if descendants:
            _, alive = psutil.wait_procs(descendants, timeout=max(0, deadline - time.monotonic()))
            return not alive
        return True

    def _phase(self, name: str, seconds: float, work_deadline: float, started: float) -> tuple[str, bool]:
        phase_start = time.monotonic()
        deadline = min(work_deadline, phase_start + seconds)
        reserve = min(2, max(0.005, seconds * 0.1))
        execution_deadline = deadline - reserve
        directory = self.workspace / name
        directory.mkdir()
        temporary = directory / "temp"
        temporary.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("MEDIAWORKBENCH_")
               and k not in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}}
        env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONPATH=str(ROOT),
                   TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary))
        runtime = ROOT / "runtime"
        if runtime.exists():
            env["PATH"] = str(runtime / "node") + os.pathsep + os.environ.get("SystemRoot", "C:\\Windows") + "\\System32"
            env["PLAYWRIGHT_BROWSERS_PATH"] = str(runtime / "browsers")
        process = scope = None
        state = "failed"
        error = ""
        stopped = True
        with self.lock:
            self.result["phases"][name] = dict(state="running", budget_seconds=round(seconds, 2), elapsed_seconds=0)
            self._save()
        try:
            if time.monotonic() >= execution_deadline:
                state = "timed_out"
            else:
                scope = ProcessScope()
                command = [sys.executable, "-m", "workbench.selftest_runner", str(directory),
                           "--phase", name, "--deadline", str(deadline),
                           "--parent", str(os.getpid()), "--parent-created", str(psutil.Process().create_time())]
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                created = psutil.Process(process.pid).create_time()
                scope.assign(process)
                owner = {"pid": process.pid, "created": created, "directory": str(directory), "phase": name}
                owners = read_json(self.owner_file, {"workers": []})
                owners["workers"].append(owner)
                atomic_json(self.owner_file, owners)
                atomic_json(directory / "owner.json", owner)
                with self.lock:
                    self.result["timings"].setdefault(name, {})["worker_spawn"] = {
                        "state": "passed", "elapsed_seconds": round(time.monotonic() - phase_start, 3)}
                while True:
                    self._progress(directory, name, started)
                    if self.cancel_event.is_set():
                        state = "cancelled"
                        break
                    if process.poll() is not None:
                        state = "completed" if process.returncode == 0 else "failed"
                        break
                    if time.monotonic() >= execution_deadline:
                        state = "timed_out"
                        break
                    self.cancel_event.wait(min(0.1, max(0, execution_deadline - time.monotonic())))
        except Exception as exc:
            state = "failed"
            error = type(exc).__name__
        finally:
            stopped = self._stop_phase(process, scope, deadline)
            self._progress(directory, name, started)
            with self.lock:
                for step in self.result["steps"]:
                    if (step["id"] == "network") != (name == "network"):
                        continue
                    if step["state"] == "running":
                        step.update(
                            state="unconfirmed" if name == "network" and state == "timed_out"
                            else "failed" if state == "failed" else "interrupted",
                            reason="phase_timeout" if state == "timed_out" else "phase_" + state,
                            message="本阶段达到时限，未完成检查。" if state == "timed_out" else "本阶段未完成。",
                            attempted=True)
                    elif step["state"] == "pending":
                        step.update(state="skipped", attempted=False,
                                    reason="phase_budget_exhausted" if state == "timed_out"
                                    else "cancelled" if state == "cancelled" else "prerequisite_failed",
                                    message="本阶段未执行此项检查。")
                self.result["phases"][name].update(
                    state=state, elapsed_seconds=round(time.monotonic() - phase_start, 3), stopped=stopped)
                if error:
                    self.result["phases"][name]["error"] = error
                if process:
                    self.result["phases"][name]["exit_code"] = process.returncode
                for timing in self.result["timings"].get(name, {}).values():
                    if timing["state"] == "running":
                        timing.update(state="failed" if state == "failed" else "interrupted",
                                      reason="phase_timeout" if state == "timed_out" else "phase_" + state)
                self._save()
        return state, stopped

    def _bounded_clean(self, deadline: float) -> bool:
        outcome = {"ok": False}

        def clean():
            while time.monotonic() < deadline:
                try:
                    self._clean()
                    self.owner_file.unlink(missing_ok=True)
                    outcome["ok"] = True
                    return
                except (OSError, RuntimeError):
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))

        self.cleanup_thread = threading.Thread(target=clean, name="workbench-diagnostic-cleanup", daemon=True)
        self.cleanup_thread.start()
        self.cleanup_thread.join(timeout=max(0, deadline - time.monotonic()))
        return outcome["ok"]

    def _run(self):
        started = time.monotonic()
        scale = self.budget / 180
        cleanup_seconds = 10 * scale
        global_deadline = started + self.budget
        work_deadline = global_deadline - cleanup_seconds
        include_network = self.result["include_network"]
        terminal, message = "completed", "检查完成。"
        all_stopped = True
        try:
            local, all_stopped = self._phase("local", (125 if include_network else 170) * scale,
                                             work_deadline, started)
            if local == "cancelled" or self.cancel_event.is_set():
                terminal, message = "cancelled", "检查已取消。"
            else:
                if local == "timed_out":
                    terminal, message = "timed_out", "本机阶段达到时限，已保留完成项。"
                elif local == "failed":
                    terminal, message = "failed", "本机检查进程未正常完成。"
                if include_network and all_stopped:
                    network, all_stopped = self._phase("network", 45 * scale, work_deadline, started)
                    if network == "cancelled" or self.cancel_event.is_set():
                        terminal, message = "cancelled", "检查已取消。"
                    elif network == "timed_out" and terminal == "completed":
                        terminal, message = "timed_out", "联网阶段达到时限，未能确认。"
                    elif network == "failed" and terminal == "completed":
                        terminal, message = "failed", "联网检查进程未正常完成。"
        except Exception:
            terminal, message = "failed", "无法启动检查，请重试。"
        finally:
            cleanup_started = time.monotonic()
            with self.lock:
                self.result["phases"]["cleanup"] = dict(state="running", budget_seconds=round(cleanup_seconds, 2), elapsed_seconds=0)
                self._save()
            cleanup_ok = all_stopped and self._bounded_clean(min(global_deadline, cleanup_started + cleanup_seconds))
            with self.lock:
                for step in self.result["steps"]:
                    if step["state"] == "pending":
                        step.update(state="skipped", attempted=False,
                                    reason="cancelled" if terminal == "cancelled" else "phase_not_started",
                                    message="本次未执行此项检查。")
                self.result["phases"]["cleanup"].update(
                    state="completed" if cleanup_ok else "failed", elapsed_seconds=round(time.monotonic() - cleanup_started, 3))
                self.result.update(state=terminal, message=message, finished=now(),
                                   elapsed_seconds=round(time.monotonic() - started, 1), cleanup_complete=cleanup_ok)
                if not cleanup_ok:
                    self.result.update(state="failed", message="临时内容清理未完成；保留恢复记录，下次启动会重试。")
                self._save()

    def close(self):
        with self.lock:
            self.closed = True
            self.cancel_event.set()
        if self.thread:
            self.thread.join(timeout=15)
