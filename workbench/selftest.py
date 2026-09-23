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


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


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
        self.result = read_json(self.file)
        self._recover()

    def _clean(self):
        if self.workspace.exists():
            if self.workspace.is_symlink() or self.workspace.resolve().parent != self.directory.resolve():
                raise RuntimeError("Unexpected diagnostic workspace")
            shutil.rmtree(self.workspace)

    def _recover(self):
        owner = read_json(self.workspace / "owner.json", {})
        if owner:
            try:
                p = psutil.Process(owner["pid"])
                if (abs(p.create_time() - owner["created"]) < 0.01
                        and "workbench.selftest_runner" in p.cmdline()
                        and str(self.workspace) in p.cmdline()):
                    kill_tree(p.pid, owner["created"])
            except (psutil.Error, KeyError):
                pass
        if self.result and self.result["state"] in ACTIVE:
            self.result.update(state="interrupted", finished=now(), message="软件已重开，上次检查中断。")
            for step in self.result["steps"]:
                if step["state"] == "running":
                    step.update(state="interrupted", message="检查中断。")
            atomic_json(self.file, self.result)
        try:
            self._clean()
        except OSError:
            # Never erase outside the owned workspace; retry before the next run.
            pass

    def _save(self):
        atomic_json(self.file, self.result)

    def active(self):
        with self.lock:
            return bool(self.result and self.result["state"] in ACTIVE)

    def view(self, identity=None):
        with self.lock:
            if identity and (not self.result or self.result["id"] != identity):
                raise Missing(identity)
            if not self.result:
                return None
            value = json.loads(json.dumps(self.result))
            value["local_state"] = local_result(value["steps"])
            value["network_state"] = value["steps"][-1]["state"]
            return value

    def start(self, include_network=True):
        with self.lock:
            if self.closed:
                raise Conflict("软件正在退出或更新，请稍后重试。")
            if self.active():
                return self.view()
            self._clean()
            self.workspace.mkdir()
            self.cancel_event.clear()
            self.result = {
                "id": uuid.uuid4().hex, "state": "running", "started": now(),
                "finished": None, "elapsed_seconds": 0, "include_network": include_network,
                "message": "正在独立环境中检查，不会改动正式数据。",
                "steps": [dict(id=k, name=n, state="pending", message="", elapsed_seconds=0) for k, n in STEPS],
            }
            if not include_network:
                self.result["steps"][-1].update(state="skipped", message="本次仅检查本机。")
            self._save()
            self.thread = threading.Thread(target=self._run, name="workbench-self-test", daemon=True)
            self.thread.start()
            return self.view()

    def cancel(self, identity):
        with self.lock:
            self.view(identity)
            if self.active():
                self.result.update(state="cancelling", message="正在停止检查并清理临时内容…")
                self.cancel_event.set()
                self._save()
            return self.view()

    def _run(self):
        process = None
        scope = None
        created = None
        start = time.monotonic()
        terminal, message = "completed", "检查完成。"
        try:
            env = {k: v for k, v in os.environ.items() if not k.startswith("MEDIAWORKBENCH_") and k not in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}}
            env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONPATH=str(ROOT))
            temporary = self.workspace / "temp"
            temporary.mkdir()
            env.update(TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary))
            runtime = ROOT / "runtime"
            if runtime.exists():
                env["PATH"] = str(runtime / "node") + os.pathsep + os.environ.get("SystemRoot", "C:\\Windows") + "\\System32"
                env["PLAYWRIGHT_BROWSERS_PATH"] = str(runtime / "browsers")
            command = [sys.executable, "-m", "workbench.selftest_runner", str(self.workspace),
                       "--parent", str(os.getpid()), "--parent-created", str(psutil.Process().create_time())]
            if self.result["include_network"]:
                command.append("--network")
            scope = ProcessScope()
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            created = psutil.Process(process.pid).create_time()
            scope.assign(process)
            atomic_json(self.workspace / "owner.json", {"pid": process.pid, "created": created})
            while True:
                with self.lock:
                    progress = read_json(self.workspace / "progress.json", {})
                    for step in self.result["steps"]:
                        if step["id"] in progress:
                            step.update(progress[step["id"]])
                    self.result["elapsed_seconds"] = round(time.monotonic() - start, 1)
                    self._save()
                if self.cancel_event.is_set():
                    terminal, message = "cancelled", "检查已取消。"
                    break
                if time.monotonic() - start >= self.budget:
                    terminal, message = "timed_out", "检查达到三分钟时限，已停止。"
                    break
                if process.poll() is not None:
                    if process.returncode:
                        terminal, message = "failed", "检查进程未正常完成，请重试。"
                    break
                self.cancel_event.wait(0.25)
        except Exception as exc:
            terminal, message = "failed", "无法启动检查（" + type(exc).__name__ + "）。"
        finally:
            if scope:
                scope.close()
            if process and process.poll() is None:
                kill_tree(process.pid, created)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            cleanup_ok = True
            for attempt in range(5):
                try:
                    self._clean()
                    break
                except OSError:
                    cleanup_ok = False
                    time.sleep(0.2)
                else:
                    cleanup_ok = True
            if self.workspace.exists():
                cleanup_ok = False
            else:
                cleanup_ok = True
            with self.lock:
                for step in self.result["steps"]:
                    if step["state"] == "running":
                        step.update(state="failed" if terminal in {"failed", "timed_out"} else "interrupted", message=message)
                    elif step["state"] == "pending":
                        step.update(state="skipped", message="前序检查未完成。")
                self.result.update(state=terminal, message=message, finished=now(), cleanup_complete=cleanup_ok)
                if not cleanup_ok:
                    self.result.update(state="failed", message="临时内容清理未完成；下次启动会重试。")
                self._save()

    def close(self):
        with self.lock:
            self.closed = True
            self.cancel_event.set()
        if self.thread:
            self.thread.join(timeout=15)
