"""Real Win32 singleton tests, always using isolated desktop names."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

import psutil
import pytest

from workbench.desktop_guard import (
    PREFIX, WAIT_OBJECT_0, WAIT_TIMEOUT,
    WindowsObjects, coordination_namespace,
)

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Windows desktop coordination")


def isolated_env(path):
    return {
        **os.environ,
        "MEDIAWORKBENCH_DATA_DIR": str(path / "data"),
        "MEDIAWORKBENCH_INSTANCE": str(path / "instance.json"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
    }


class GuardProcess:
    def __init__(self, env, *args):
        # Never allow a native test to interact with the installed desktop.
        assert coordination_namespace(env).startswith(PREFIX + ".Isolated.")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "workbench.desktop_guard", *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", cwd=ROOT, env=env,
        )
        self.events = queue.Queue()
        def read():
            for line in self.process.stdout:
                self.events.put(json.loads(line))
        threading.Thread(target=read, daemon=True).start()

    def event(self, name, timeout=10):
        try:
            value = self.events.get(timeout=timeout)
        except queue.Empty:
            pytest.fail(f"No {name!r} event from guard (returncode={self.process.poll()})")
        assert value["event"] == name, value
        return value

    def command(self, command):
        self.process.stdin.write(json.dumps({"command": command}) + "\n")
        self.process.stdin.flush()

    def release(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            assert self.process.wait(timeout=10) == 0

    def kill(self):
        # Windows venv Python has a redirector; only kill this process tree.
        if self.process.poll() is None:
            children = psutil.Process(self.process.pid).children(recursive=True)
            for child in reversed(children):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            self.process.kill()
            self.process.wait(timeout=10)


@pytest.fixture
def guards(tmp_path):
    env = isolated_env(tmp_path)
    started = []
    def start(*args):
        result = GuardProcess(env, *args)
        started.append(result)
        return result
    yield start
    for result in reversed(started):
        try:
            result.release()
        finally:
            result.kill()


def test_default_names_match_original_launcher(tmp_path):
    env = {"LOCALAPPDATA": str(tmp_path)}
    assert coordination_namespace(env) == "Local\\MediaWorkbench.Desktop"
    env["MEDIAWORKBENCH_DATA_DIR"] = str(tmp_path / "MediaWorkbench" / "data")
    env["MEDIAWORKBENCH_INSTANCE"] = str(tmp_path / "MediaWorkbench" / "instance.json")
    assert coordination_namespace(env) == PREFIX


def test_isolated_paths_use_deterministic_different_namespace(tmp_path):
    first = isolated_env(tmp_path / "first")
    second = isolated_env(tmp_path / "second")
    namespace = coordination_namespace(first)
    assert namespace != PREFIX
    assert namespace == coordination_namespace(first.copy())
    assert namespace != coordination_namespace(second)
    # No private data path is exposed through the Win32 object name.
    assert str(tmp_path) not in namespace
    first["MEDIAWORKBENCH_INSTANCE"] += ".other"
    assert namespace != coordination_namespace(first)


@WINDOWS
def test_guard_owns_singleton_and_duplicate_activates_owner(guards):
    first = guards("hold")
    assert first.event("ready")["owner"] is True
    first.command("ping")
    first.event("pong")
    second = guards("hold")
    ready = second.event("ready")
    assert ready["owner"] is False
    assert ready["signaled"] == "activate"
    assert second.process.wait(timeout=10) == 0
    first.event("activate")
    assert first.process.poll() is None
    first.command("release")
    first.event("released")
    assert first.process.wait(timeout=10) == 0
    third = guards()
    assert third.event("ready")["owner"] is True


@WINDOWS
def test_simultaneous_starts_have_exactly_one_owner(guards):
    contenders = [guards() for _ in range(5)]
    owners = []
    for contender in contenders:
        ready = contender.event("ready")
        if ready["owner"]:
            owners.append(contender)
        else:
            assert ready["signaled"] == "activate"
            assert contender.process.wait(timeout=10) == 0
    assert len(owners) == 1
    owners[0].event("activate")
    assert owners[0].process.poll() is None


@WINDOWS
def test_quit_event_waits_for_parent_shutdown_and_does_not_release(guards):
    first = guards()
    assert first.event("ready")["owner"] is True
    quitter = guards("--quit")
    assert quitter.event("signaled") == {"event": "signaled", "action": "exit", "sent": True}
    assert quitter.process.wait(timeout=10) == 0
    first.event("exit")
    second = guards()
    assert second.event("ready")["owner"] is False
    assert second.process.wait(timeout=10) == 0
    first.event("activate")
    assert first.process.poll() is None


@WINDOWS
def test_parent_stdin_close_releases_guard(guards):
    first = guards()
    first.event("ready")
    first.release()
    second = guards()
    assert second.event("ready")["owner"] is True


@WINDOWS
def test_missing_owner_quit_does_not_create_objects(guards):
    quitter = guards("signal", "exit")
    assert quitter.event("signaled")["sent"] is False
    assert quitter.process.wait(timeout=10) == 0
    first = guards()
    assert first.event("ready")["owner"] is True


@WINDOWS
def test_legacy_named_mutex_and_events_interoperate(tmp_path, guards):
    objects = WindowsObjects(coordination_namespace(isolated_env(tmp_path)))
    try:
        activate = objects.event("Activate")
        exit_event = objects.event("Exit")
        assert objects.acquire() == (True, False)
        # These native objects match legacy .NET auto-reset event semantics.
        newcomer = guards("hold")
        assert newcomer.event("ready")["owner"] is False
        assert newcomer.process.wait(timeout=10) == 0
        assert objects.api.WaitForSingleObject(activate, 5000) == WAIT_OBJECT_0
        assert objects.api.WaitForSingleObject(activate, 0) == WAIT_TIMEOUT
        quitter = guards("--quit")
        assert quitter.event("signaled")["sent"] is True
        assert quitter.process.wait(timeout=10) == 0
        assert objects.api.WaitForSingleObject(exit_event, 5000) == WAIT_OBJECT_0
    finally:
        objects.close()


@WINDOWS
def test_abandoned_owner_is_recovered(tmp_path, guards):
    objects = WindowsObjects(coordination_namespace(isolated_env(tmp_path)))
    # A spectator handle keeps the abandoned mutex object alive after its
    # owner dies, reproducing a real concurrent-start crash.
    spectator = objects._checked_handle(objects.api.CreateMutexW(None, False, objects.namespace + ".Singleton"))
    try:
        first = guards()
        assert first.event("ready")["owner"] is True
        first.kill()
        second = guards()
        ready = second.event("ready")
        assert ready["owner"] is True
        assert ready["abandoned"] is True
        # It really owns the recovered mutex, not merely its handle.
        assert objects.api.WaitForSingleObject(spectator, 0) == WAIT_TIMEOUT
    finally:
        objects.close()


@WINDOWS
def test_bad_command_does_not_drop_ownership(guards):
    first = guards()
    first.event("ready")
    first.process.stdin.write('{"command":"shutdown-all-processes"}\n')
    first.process.stdin.flush()
    first.event("error")
    first.command("ping")
    first.event("pong")
    second = guards()
    assert second.event("ready")["owner"] is False
    assert second.process.wait(timeout=10) == 0
    first.event("activate")
