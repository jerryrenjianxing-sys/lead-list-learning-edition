"""Exercise the packaged Electron lifecycle using entirely isolated local data.

This is a standalone smoke runner, not a unit test. It never signals the
production desktop namespace and refuses to run against the old WinForms
package. Renderer behavior and bridge security have a separate UI test suite.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler, Request

import psutil

ROOT = Path(__file__).resolve().parents[1]


def wait(predicate, message, timeout=45):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(0.15)
    raise AssertionError(message)


def native_api():
    api = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    api.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    api.EnumWindows.restype = wintypes.BOOL
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.IsWindowVisible.restype = wintypes.BOOL
    api.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    api.PostMessageW.restype = wintypes.BOOL

    def window(pid):
        matches = []
        @callback_type
        def callback(hwnd, _):
            owner = wintypes.DWORD()
            api.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            title = ctypes.create_unicode_buffer(256)
            api.GetWindowTextW(hwnd, title, len(title))
            if owner.value == pid and title.value == "Media Deep Researcher":
                matches.append(hwnd)
            return True
        api.EnumWindows(callback, 0)
        return matches[0] if matches else None
    return api, window


def process_tree(pid):
    try:
        parent = psutil.Process(pid)
        return [parent, *parent.children(recursive=True)]
    except psutil.NoSuchProcess:
        return []


def run(directory: Path, timeout: float) -> dict:
    if os.name != "nt":
        raise RuntimeError("This lifecycle smoke requires Windows")
    directory = directory.resolve()
    executable = directory / "MediaWorkbench.exe"
    if not executable.is_file() or not (directory / "resources" / "app.asar").is_file():
        raise RuntimeError("An assembled Electron package with resources/app.asar is required")
    if not (directory / "workbench" / "desktop_guard.py").is_file():
        raise RuntimeError("Packaged isolated Windows coordination guard is missing")
    # Keep the native helper import independent of the shell's Python cwd.
    from workbench.desktop_guard import PREFIX, coordination_namespace
    api, window = native_api()
    cache = ROOT / ".cache"
    cache.mkdir(exist_ok=True)
    opener = build_opener(ProxyHandler({}))
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    with tempfile.TemporaryDirectory(prefix="electron-桌面测试-", dir=cache) as temporary:
        isolated = Path(temporary)
        discovery = isolated / "instance.json"
        data = isolated / "data"
        env = {
            **os.environ,
            "MEDIAWORKBENCH_DATA_DIR": str(data),
            "MEDIAWORKBENCH_INSTANCE": str(discovery),
        }
        env.pop("ELECTRON_RUN_AS_NODE", None)
        assert coordination_namespace(env).startswith(PREFIX + ".Isolated.")
        desktop = None
        info = None
        descendants = []
        launched = []
        with (isolated / "desktop.log").open("wb") as log:
            def launch(*arguments):
                process = subprocess.Popen(
                    [str(executable), *arguments], cwd=directory, env=env,
                    startupinfo=startup, stdout=log, stderr=log,
                )
                launched.append(process)
                return process

            def ready():
                if not info:
                    return False
                try:
                    request = Request(info["base_url"] + "/api/v1/health", headers={"Authorization": "Bearer " + info["token"]})
                    with opener.open(request, timeout=1) as response:
                        result = json.load(response)
                    return result if result["product"] == "MediaWorkbench" else False
                except (OSError, ValueError):
                    return False

            started = time.monotonic()
            try:
                desktop = launch()
                wait(discovery.exists, "Electron did not publish an isolated host", timeout)
                info = json.loads(discovery.read_text(encoding="utf-8"))
                assert Path(info["data_directory"]).resolve() == data.resolve()
                url = urlsplit(info["base_url"])
                assert url.scheme == "http" and url.hostname == "127.0.0.1"
                health = wait(ready, "Isolated host did not become ready", timeout)
                hwnd = wait(lambda: window(desktop.pid), "Electron desktop window missing", timeout)
                wait(lambda: any("--type=renderer" in p.cmdline() for p in process_tree(desktop.pid)), "Electron renderer process missing", timeout)
                startup_seconds = round(time.monotonic() - started, 3)
                with opener.open(info["base_url"] + "/", timeout=5) as response:
                    assert b"<html" in response.read().lower()
                tree = process_tree(desktop.pid)
                descendants = [(p.pid, p.create_time()) for p in tree]
                memory_mb = round(sum(p.memory_info().rss for p in tree) / 1024**2, 1)
                second = launch()
                assert second.wait(timeout=15) == 0
                wait(lambda: api.IsWindowVisible(hwnd), "Duplicate launch failed to show existing window")
                assert desktop.poll() is None
                assert api.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE -> tray
                wait(lambda: not api.IsWindowVisible(hwnd), "Close did not hide desktop into tray")
                assert desktop.poll() is None and ready()
                third = launch()
                assert third.wait(timeout=15) == 0
                wait(lambda: api.IsWindowVisible(hwnd), "Duplicate launch failed to restore tray window")
            finally:
                if desktop is not None:
                    # Retain identity for all current descendants before
                    # their parent exits and Windows reparents them.
                    descendants = list(set(descendants + [(p.pid, p.create_time()) for p in process_tree(desktop.pid)]))
                    try:
                        quit_process = launch("--quit")
                        assert quit_process.wait(timeout=15) == 0
                        desktop.wait(timeout=timeout)
                    finally:
                        # Cleanup is limited to this runner's launches and
                        # recorded process identities, even on a failed test.
                        if any(process.poll() is None for process in launched):
                            owned = descendants[:]
                            for process in launched:
                                owned.extend((p.pid, p.create_time()) for p in process_tree(process.pid))
                            for pid, created in reversed(owned):
                                try:
                                    candidate = psutil.Process(pid)
                                    if candidate.create_time() == created:
                                        candidate.kill()
                                except psutil.NoSuchProcess:
                                    pass
                            for process in launched:
                                process.wait(timeout=10)
            assert desktop is not None and desktop.returncode == 0
            assert not discovery.exists(), "Discovery survived desktop exit"
            def no_survivors():
                for pid, created in descendants:
                    try:
                        if psutil.Process(pid).create_time() == created:
                            return False
                    except psutil.NoSuchProcess:
                        pass
                return True
            wait(no_survivors, "Owned renderer, host or guard survived explicit desktop exit", timeout)
    return {
        "ok": True,
        "version": health["version"],
        "startup_seconds": startup_seconds,
        "process_tree_memory_mb": memory_mb,
        "checks": ["isolated-host", "native-window", "electron-renderer", "page-response", "single-instance", "close-to-tray", "tray-restore", "explicit-quit", "owned-process-cleanup", "chinese-data-path"],
        "limitation": "Current Windows lifecycle only; renderer UI and clean-system installation have separate checks.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "build" / "MediaWorkbench")
    parser.add_argument("--output", type=Path, default=ROOT / ".cache" / "electron-desktop-results.json")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    try:
        result = run(args.directory, args.timeout)
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "exception": type(exc).__name__}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        raise
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    # Standalone scripts otherwise have tests/ as sys.path[0].
    sys.path.insert(0, str(ROOT))
    main()
