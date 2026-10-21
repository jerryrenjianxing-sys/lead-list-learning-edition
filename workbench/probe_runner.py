"""Run a bounded, cancellable capability check on the queue's single browser lease."""

from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from .paths import ROOT


def run_probe(db, operation, stopping, sessions=None, command=None):
    from .queue import kill_tree

    started = time.monotonic()
    deadline = started + 60
    directory = db.root / "probes" / operation["id"]
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / "result.json"
    process = lease = scope = None
    result = {
        "verdict": "unconfirmed",
        "reason": "startup_failed",
        "attempted": False,
        "requests": 0,
    }
    cleanup = True
    try:
        if sessions:
            lease = sessions.call(
                "acquire_probe",
                operation=operation,
                timeout=max(1, deadline - time.monotonic() - 4),
            )
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "MEDIAWORKBENCH_DATA_DIR": str(db.root),
            "MEDIAWORKBENCH_PROBE_DEADLINE": str(deadline),
        }
        # A probe cannot accidentally enter the production collector or update auth.
        for name in (
            "MEDIAWORKBENCH_JOB_ID",
            "MEDIAWORKBENCH_ATTEMPT",
            "MEDIAWORKBENCH_LOGIN_DIR",
        ):
            env.pop(name, None)
        if lease:
            env["MEDIAWORKBENCH_SESSION"] = json.dumps(lease)
        argv = (
            command(operation["id"], result_path)
            if command
            else [
                sys.executable,
                "-m",
                "workbench.probe_worker",
                operation["id"],
                str(result_path),
            ]
        )
        process = subprocess.Popen(
            argv,
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        from .process_scope import ProcessScope

        scope = ProcessScope()
        scope.assign(process)
        result["attempted"] = True
        while process.poll() is None:
            current = db.one("operations", operation["id"])
            if stopping.is_set() or current["state"] in ("cancelled", "cancelling"):
                result["reason"] = (
                    "user_stopped"
                    if current["state"] in ("cancelled", "cancelling")
                    else "host_interrupted"
                )
                break
            if time.monotonic() >= deadline - 4:
                result["reason"] = "timeout"
                break
            stopping.wait(0.05)
        else:
            if (
                process.returncode == 0
                and result_path.is_file()
                and result_path.stat().st_size < 16384
            ):
                candidate = json.loads(result_path.read_text(encoding="utf-8"))
                if candidate.get("verdict") not in (
                    "available",
                    "unconfirmed",
                    "restricted",
                    "network_error",
                    "interface_error",
                    "content_unavailable",
                ):
                    raise ValueError("Invalid probe verdict")
                result = candidate
            else:
                result["reason"] = "worker_failed"
    except TimeoutError:
        result["reason"] = "startup_timeout"
    except Exception:
        result["reason"] = "startup_failed" if process is None else "worker_failed"
    finally:
        if scope:
            try:
                scope.close()
            except OSError:
                cleanup = False
        if process and process.poll() is None:
            kill_tree(process.pid)
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                cleanup = False
        if lease:
            try:
                sessions.call(
                    "release_probe",
                    lease=lease["lease"],
                    timeout=max(1, deadline - time.monotonic()),
                )
            except Exception:
                cleanup = False
        try:
            result_path.unlink(missing_ok=True)
            directory.rmdir()
        except OSError:
            cleanup = False
    return {
        **result,
        "cleanup_complete": cleanup,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }
