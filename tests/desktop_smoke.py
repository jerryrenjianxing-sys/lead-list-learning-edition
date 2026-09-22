"""Exercise only the built workbench's WinForms lifecycle on Windows."""

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import build_opener, ProxyHandler
import psutil

root = Path(__file__).resolve().parents[1]
app = root / "build/MediaWorkbench"
directory = root / ".cache/desktop-smoke"
directory.mkdir(parents=True, exist_ok=True)
instance = directory / "instance.json"
env = {
    **os.environ,
    "MEDIAWORKBENCH_DATA_DIR": str(directory / "data"),
    "MEDIAWORKBENCH_INSTANCE": str(instance),
}
exe = app / "MediaWorkbench.exe"
startup = subprocess.STARTUPINFO()
startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
startup.wShowWindow = 0
user32 = ctypes.windll.user32
user32.PostMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
opener = build_opener(ProxyHandler({}))


def wait(predicate, message, timeout=45):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(0.15)
    raise AssertionError(message)


def window(pid):
    matches = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        text = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, text, 256)
        if owner.value == pid and text.value == "Media Deep Researcher":
            matches.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return matches[0] if matches else None


def start():
    return subprocess.Popen([str(exe)], cwd=app, env=env, startupinfo=startup)


desktop = start()
info = None
try:
    wait(instance.exists, "desktop did not publish a local host")
    info = json.loads(instance.read_text(encoding="utf-8"))

    def ready():
        try:
            with opener.open(
                info["base_url"] + "/api/v1/health", timeout=1
            ) as response:
                return json.load(response)["product"] == "MediaWorkbench"
        except OSError:
            return False

    wait(ready, "desktop host did not become ready")
    hwnd = wait(lambda: window(desktop.pid), "desktop window missing")
    wait(
        lambda: any(
            p.name().lower() == "msedgewebview2.exe"
            for p in psutil.Process(desktop.pid).children(recursive=True)
        ),
        "WebView2 did not start",
    )
    second = start()
    assert second.wait(timeout=10) == 0
    wait(
        lambda: user32.IsWindowVisible(hwnd),
        "second launch did not restore existing window",
    )
    assert desktop.poll() is None
    assert user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE routes into the tray.
    wait(lambda: not user32.IsWindowVisible(hwnd), "close did not hide into tray")
    assert ready() and desktop.poll() is None
    third = start()
    assert third.wait(timeout=10) == 0
    wait(lambda: user32.IsWindowVisible(hwnd), "tray window did not restore")
    print("window, WebView2, single instance, tray close and restore passed")
finally:
    subprocess.run(
        [str(exe), "--quit"],
        cwd=app,
        env=env,
        startupinfo=startup,
        timeout=10,
        check=True,
    )
    desktop.wait(timeout=45)
    if info:
        wait(
            lambda: not psutil.pid_exists(info["pid"]),
            "host survived explicit desktop exit",
        )
assert desktop.returncode == 0
assert not instance.exists()
result = {
    "ok": True,
    "checks": [
        "native-window",
        "WebView2-process",
        "single-instance",
        "close-to-tray",
        "restore",
        "explicit-exit-stops-host",
    ],
    "limitation": "Current Windows with WebView2 installed; not clean VM installation.",
}
(root / ".cache/desktop-results.json").write_text(
    json.dumps(result, indent=2), encoding="utf-8"
)
print("Desktop lifecycle passed")
