"""Own the Windows desktop singleton independently of the JavaScript runtime.

The legacy launcher uses the same three Win32 objects. Electron keeps this
process's stdin open for its whole lifetime: releasing stdin relinquishes the
mutex even when Electron terminates unexpectedly. stdout is a JSONL protocol;
no browser, business process or login state is managed here.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import queue
import sys
import threading
from typing import Mapping

PREFIX = "Local\\MediaWorkbench.Desktop"
WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x80
WAIT_TIMEOUT = 0x102
WAIT_FAILED = 0xFFFFFFFF
EVENT_MODIFY_STATE = 0x0002
ERROR_FILE_NOT_FOUND = 2


def coordination_namespace(env: Mapping[str, str] | None = None) -> str:
    """Use legacy names in production and deterministic names for isolation.

    Explicit environment variables that equal the production paths still
    represent production. Overriding either path creates a separate namespace;
    test subprocesses must always override the data and discovery paths.
    """
    env = os.environ if env is None else env
    local = Path(env.get("LOCALAPPDATA") or (Path.home() / ".local/share"))
    default_data = local / "MediaWorkbench" / "data"
    default_instance = local / "MediaWorkbench" / "instance.json"

    def identity(value: str | Path) -> str:
        return os.path.normcase(str(Path(value).resolve()))

    data = identity(env.get("MEDIAWORKBENCH_DATA_DIR") or default_data)
    instance = identity(env.get("MEDIAWORKBENCH_INSTANCE") or default_instance)
    if data == identity(default_data) and instance == identity(default_instance):
        return PREFIX
    digest = hashlib.sha256((data + "\0" + instance).encode("utf-8")).hexdigest()[:24]
    return PREFIX + ".Isolated." + digest


class WindowsObjects:
    """Small typed Win32 wrapper; every handle is closed by its creator."""

    def __init__(self, namespace: str):
        if os.name != "nt":
            raise OSError("Desktop coordination requires Windows")
        self.namespace = namespace
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.CreateMutexW.restype = wintypes.HANDLE
        self.api.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self.api.ReleaseMutex.restype = wintypes.BOOL
        self.api.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.CreateEventW.restype = wintypes.HANDLE
        self.api.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.OpenEventW.restype = wintypes.HANDLE
        self.api.SetEvent.argtypes = [wintypes.HANDLE]
        self.api.SetEvent.restype = wintypes.BOOL
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.WaitForSingleObject.restype = wintypes.DWORD
        self.api.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD]
        self.api.WaitForMultipleObjects.restype = wintypes.DWORD
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handles: list[int] = []
        self.mutex = None
        self.owner = False

    def _checked_handle(self, handle):
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handles.append(handle)
        return handle

    def event(self, name: str):
        # Auto-reset matches the original .NET EventWaitHandle contract.
        return self._checked_handle(self.api.CreateEventW(None, False, False, self.namespace + "." + name))

    def acquire(self) -> tuple[bool, bool]:
        # Waiting is necessary: CreateMutex's "already exists" is not proof
        # another thread owns it, particularly after an abandoned owner.
        self.mutex = self._checked_handle(self.api.CreateMutexW(None, False, self.namespace + ".Singleton"))
        result = self.api.WaitForSingleObject(self.mutex, 0)
        if result not in (WAIT_OBJECT_0, WAIT_ABANDONED, WAIT_TIMEOUT):
            raise ctypes.WinError(ctypes.get_last_error())
        self.owner = result != WAIT_TIMEOUT
        return self.owner, result == WAIT_ABANDONED

    def signal(self, action: str) -> bool:
        name = {"activate": "Activate", "exit": "Exit"}[action]
        handle = self.api.OpenEventW(EVENT_MODIFY_STATE, False, self.namespace + "." + name)
        if not handle:
            error = ctypes.get_last_error()
            if error == ERROR_FILE_NOT_FOUND:
                return False
            raise ctypes.WinError(error)
        try:
            if not self.api.SetEvent(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            return True
        finally:
            self.api.CloseHandle(handle)

    def wait_events(self, handles: tuple[int, int], timeout_ms: int = 100) -> int | None:
        array = (wintypes.HANDLE * len(handles))(*handles)
        result = self.api.WaitForMultipleObjects(len(handles), array, False, timeout_ms)
        if result == WAIT_TIMEOUT:
            return None
        if result == WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        if result >= len(handles):
            raise OSError("Unexpected desktop coordination wait result")
        return result

    def close(self):
        # Win32 mutex ownership is thread-local, so hold() calls this on the
        # exact thread that acquired it, after the desktop stops its host.
        if self.owner:
            self.api.ReleaseMutex(self.mutex)
            self.owner = False
        for handle in reversed(self.handles):
            self.api.CloseHandle(handle)
        self.handles.clear()


def emit(event: str, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=True), flush=True)


def _read_commands(commands: queue.Queue):
    try:
        for line in sys.stdin:
            try:
                data = json.loads(line)
                command = data.get("command") if isinstance(data, dict) else None
                if command not in ("release", "ping"):
                    commands.put("invalid")
                    continue
                commands.put(command)
                if command == "release":
                    return
            except (ValueError, TypeError):
                commands.put("invalid")
    finally:
        commands.put("release")


def hold(objects: WindowsObjects) -> int:
    # Publish both events before competing for ownership. A simultaneous
    # second launch can signal activation while the first is still starting.
    activate = objects.event("Activate")
    quit_event = objects.event("Exit")
    owner, abandoned = objects.acquire()
    if not owner:
        objects.signal("activate")
        emit("ready", owner=False, namespace=objects.namespace, signaled="activate")
        return 0

    commands: queue.Queue = queue.Queue()
    threading.Thread(target=_read_commands, args=(commands,), daemon=True).start()
    emit("ready", owner=True, namespace=objects.namespace, abandoned=abandoned)
    while True:
        try:
            while True:
                command = commands.get_nowait()
                if command == "release":
                    emit("released")
                    return 0
                if command == "ping":
                    emit("pong")
                else:
                    emit("error", message="Expected a ping or release command")
        except queue.Empty:
            pass
        result = objects.wait_events((activate, quit_event))
        if result is not None:
            # Exit requests keep ownership until Electron has saved data and
            # stopped the backend, rather than admitting a second desktop.
            emit("activate" if result == 0 else "exit")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", default="hold", choices=("hold", "signal"))
    parser.add_argument("action", nargs="?", choices=("activate", "exit"))
    parser.add_argument("--quit", action="store_true")
    args = parser.parse_args(argv)
    action = "exit" if args.quit else args.action
    if args.mode == "signal" and action is None:
        parser.error("signal requires activate or exit")
    objects = None
    try:
        objects = WindowsObjects(coordination_namespace())
        if action is not None:
            emit("signaled", action=action, sent=objects.signal(action))
            return 0
        return hold(objects)
    except BrokenPipeError:
        return 0
    except (OSError, ValueError) as exc:
        emit("error", message=str(exc))
        return 1
    finally:
        if objects is not None:
            objects.close()


if __name__ == "__main__":
    raise SystemExit(main())
