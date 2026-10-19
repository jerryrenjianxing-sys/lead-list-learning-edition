"""Diagnostic contracts through SelfTests; only the worker process edge is substituted."""

import os
import subprocess
import time

import pytest

from workbench.selftest import SelfTests, atomic_json


@pytest.fixture
def diagnostic_processes(tmp_path, monkeypatch):
    script = tmp_path / "diagnostic_worker.py"
    script.write_text(
        """import json,os,sys,time
from pathlib import Path
directory=Path(sys.argv[1]);phase=sys.argv[2]
def save(value):
    target=directory/'progress.json'
    temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps(value),encoding='utf-8')
    temporary.replace(target)
if phase=='local':
    if os.environ.get('DIAGNOSTIC_FIXTURE_LOCAL')=='complete':
        save({key:{'state':'passed','message':'checked','elapsed_seconds':0.01,'attempted':True}
              for key in ('runtime','host','queue','skill','data','recovery')})
    else:
        save({'runtime':{'state':'passed','message':'runtime checked','elapsed_seconds':0.01,'attempted':True},
              'host':{'state':'running','message':'slow local process','elapsed_seconds':0,'attempted':True},
              'timings':{'host_start':{'state':'running','elapsed_seconds':0}}})
        time.sleep(60)
else:
    mode=os.environ.get('DIAGNOSTIC_FIXTURE_NETWORK','limited')
    if mode=='slow':
        save({'network':{'state':'running','message':'waiting for external platform','elapsed_seconds':0,'attempted':True}})
        time.sleep(60)
    elif mode=='bootstrap':
        time.sleep(60)
    else:
        save({'network':{'state':mode,'message':'guest fixture outcome','elapsed_seconds':0.01,'attempted':True}})
""",
        encoding="utf-8",
    )
    original = subprocess.Popen

    def start(command, *args, **kwargs):
        if len(command) > 3 and command[1:3] == ["-m", "workbench.selftest_runner"]:
            phase = command[command.index("--phase") + 1] if "--phase" in command else "local"
            command = [command[0], str(script), command[3], phase]
        return original(command, *args, **kwargs)

    monkeypatch.setattr("workbench.selftest.subprocess.Popen", start)


def completed(manager, timeout=12):
    deadline = time.monotonic() + timeout
    while manager.active() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not manager.active(), "diagnostic did not finish its bounded run"
    return manager.view()


def test_local_timeout_preserves_success_and_gives_network_its_own_budget(tmp_path, diagnostic_processes):
    manager = SelfTests(tmp_path / "isolated", budget=3.6)
    try:
        manager.start(include_network=True)
        result = completed(manager)
        assert result["network_state"] == "limited"
        assert result["local_state"] == "incomplete"
        assert next(s for s in result["steps"] if s["id"] == "runtime")["state"] == "passed"
        assert next(s for s in result["steps"] if s["id"] == "network")["attempted"] is True
        assert result["cleanup_complete"] is True
        assert result["timings"]["local"]["host_start"]["state"] == "interrupted"
    finally:
        manager.close()


def test_cleanup_failure_is_reported_and_can_recover_without_a_new_test(tmp_path, diagnostic_processes, monkeypatch):
    from workbench.database import Conflict
    import workbench.selftest as diagnostic

    monkeypatch.setenv("DIAGNOSTIC_FIXTURE_LOCAL", "complete")
    manager = SelfTests(tmp_path / "isolated", budget=3.6)
    original = diagnostic.shutil.rmtree

    def locked_directory(path, *args, **kwargs):
        raise PermissionError("temporary browser file still locked")

    monkeypatch.setattr(diagnostic.shutil, "rmtree", locked_directory)
    try:
        manager.start(False)
        result = completed(manager)
        assert result["cleanup_complete"] is False
        assert result["recovery_pending"] is True
        with pytest.raises(Conflict):
            manager.start(False)
    finally:
        monkeypatch.setattr(diagnostic.shutil, "rmtree", original)
        manager.close()
    restarted = SelfTests(tmp_path / "isolated")
    try:
        assert restarted.view()["id"] == result["id"]
        assert restarted.view()["recovery_pending"] is False
    finally:
        restarted.close()


def test_cancellation_keeps_unstarted_network_distinct_from_failure(tmp_path, diagnostic_processes):
    manager = SelfTests(tmp_path / "isolated", budget=3.6)
    try:
        identity = manager.start(True)["id"]
        deadline = time.monotonic() + 2
        while not manager.view()["steps"][0]["attempted"] and time.monotonic() < deadline:
            time.sleep(0.01)
        manager.cancel(identity)
        result = completed(manager)
        assert result["id"] == identity
        assert result["state"] == "cancelled"
        assert result["steps"][-1]["state"] == "skipped"
        assert result["steps"][-1]["attempted"] is False
        assert result["steps"][-1]["reason"] == "cancelled"
        assert result["cleanup_complete"] is True
    finally:
        manager.close()


@pytest.mark.parametrize("network,expected,attempted", [
    ("slow", "unconfirmed", True),
    ("bootstrap", "skipped", False),
    ("failed", "failed", True),
])
def test_network_deadline_does_not_turn_local_success_into_local_failure(
        tmp_path, diagnostic_processes, monkeypatch, network, expected, attempted):
    monkeypatch.setenv("DIAGNOSTIC_FIXTURE_LOCAL", "complete")
    monkeypatch.setenv("DIAGNOSTIC_FIXTURE_NETWORK", network)
    manager = SelfTests(tmp_path / "isolated", budget=3.6)
    try:
        manager.start(True)
        result = completed(manager)
        assert result["local_state"] == "passed"
        assert result["network_state"] == expected
        assert result["steps"][-1]["attempted"] is attempted
        assert result["cleanup_complete"] is True
    finally:
        manager.close()


def test_local_only_uses_170_second_budget_without_a_guest_attempt(tmp_path, diagnostic_processes, monkeypatch):
    monkeypatch.setenv("DIAGNOSTIC_FIXTURE_LOCAL", "complete")
    manager = SelfTests(tmp_path / "isolated")
    try:
        manager.start(False)
        result = completed(manager)
        assert result["local_state"] == "passed"
        assert result["phases"]["local"]["budget_seconds"] == 170
        assert result["phases"]["cleanup"]["budget_seconds"] == 10
        assert result["steps"][-1]["reason"] == "disabled"
        assert result["steps"][-1]["attempted"] is False
    finally:
        manager.close()


def test_temporary_windows_reader_lock_does_not_abort_a_diagnostic(tmp_path, diagnostic_processes, monkeypatch):
    import workbench.selftest as diagnostic

    monkeypatch.setenv("DIAGNOSTIC_FIXTURE_LOCAL", "complete")
    original = diagnostic.os.replace
    locked_attempts = 2

    def replace(source, destination):
        nonlocal locked_attempts
        if str(destination).endswith("latest.json") and locked_attempts:
            locked_attempts -= 1
            raise PermissionError("a concurrent reader briefly holds the result file")
        return original(source, destination)

    monkeypatch.setattr(diagnostic.os, "replace", replace)
    manager = SelfTests(tmp_path / "isolated")
    try:
        manager.start(False)
        result = completed(manager)
        assert result["local_state"] == "passed"
        assert result["cleanup_complete"] is True
    finally:
        manager.close()


def test_restart_does_not_leave_an_interrupted_phase_or_timing_running(tmp_path):
    manager = SelfTests(tmp_path)
    atomic_json(manager.file, {
        "id": "interrupted-run", "state": "running", "steps": [
            {"id": "runtime", "state": "running", "attempted": True},
            {"id": "network", "state": "pending", "attempted": False}],
        "phases": {"local": {"state": "running", "budget_seconds": 125, "elapsed_seconds": 3}},
        "timings": {"local": {"browser_start": {"state": "running", "elapsed_seconds": 2}}},
    })
    restarted = SelfTests(tmp_path)
    try:
        result = restarted.view()
        assert result["id"] == "interrupted-run"
        assert result["local_state"] == "incomplete"
        assert result["phases"]["local"]["state"] == "interrupted"
        assert result["timings"]["local"]["browser_start"]["state"] == "interrupted"
        assert result["steps"][-1]["state"] == "skipped"
        assert result["steps"][-1]["attempted"] is False
    finally:
        restarted.close()


@pytest.mark.skipif(os.environ.get("MEDIAWORKBENCH_TEST_REAL_DIAGNOSTICS") != "1",
                    reason="Explicit installed-browser and built-page diagnostic acceptance")
def test_local_real_checks_keep_all_six_assertions_and_report_stage_timings(tmp_path, monkeypatch):
    errors = tmp_path / "worker-errors.log"
    error_stream = errors.open("ab")
    original = subprocess.Popen

    def start(command, *args, **kwargs):
        if len(command) > 3 and command[1:3] == ["-m", "workbench.selftest_runner"]:
            kwargs["stderr"] = error_stream
        return original(command, *args, **kwargs)

    monkeypatch.setattr("workbench.selftest.subprocess.Popen", start)
    manager = SelfTests(tmp_path / "真实本机诊断")
    try:
        manager.start(include_network=False)
        result = completed(manager, timeout=185)
        assert result["local_state"] == "passed", (result, errors.read_text(encoding="utf-8", errors="replace"))
        assert {step["id"] for step in result["steps"] if step["state"] == "passed"} == {
            "runtime", "host", "queue", "skill", "data", "recovery"}
        assert result["network_state"] == "skipped"
        assert result["steps"][-1]["attempted"] is False
        assert {"imports", "browser_start", "runtime_page", "host_start", "host_page", "recovery"}.issubset(
            result["timings"]["local"])
        assert result["cleanup_complete"] is True
    finally:
        manager.close()
        error_stream.close()
