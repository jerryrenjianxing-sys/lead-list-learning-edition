"""Install the release into an isolated workspace path, then verify data retention."""

import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from urllib.request import build_opener, ProxyHandler

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--directory", default=str(root / ".cache/安装验收 新目录"))
directory = Path(parser.parse_args().directory).resolve()
install = (directory / "应用").resolve()
assert install.is_relative_to((root / ".cache").resolve())
if install.exists():
    raise RuntimeError(
        "Use a fresh test install directory; do not overwrite an existing installation"
    )
directory.mkdir(parents=True, exist_ok=True)
setup = next((root / "release").glob("*-Setup.exe"))
instance = directory / "instance.json"
data = directory / "用户数据"
env = {
    **os.environ,
    "MEDIAWORKBENCH_INSTANCE": str(instance),
    "MEDIAWORKBENCH_DATA_DIR": str(data),
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
}
startup = subprocess.STARTUPINFO()
startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
startup.wShowWindow = 0
opener = build_opener(ProxyHandler({}))


def run(*args, timeout=180):
    subprocess.run(
        [str(a) for a in args],
        env=env,
        startupinfo=startup,
        check=True,
        timeout=timeout,
    )


def install_app():
    run(setup, "--silent", "--installto", install, "--log", directory / "setup.log")
    app = install / "current"
    assert (app / "MediaWorkbench.exe").is_file()
    assert (app / "runtime/python/python.exe").is_file()
    assert (app / "runtime/WebView2RuntimeInstallerX64.exe").stat().st_size > 50_000_000
    return app


def start(app):
    process = subprocess.Popen(
        [str(app / "MediaWorkbench.exe")], cwd=app, env=env, startupinfo=startup
    )
    for _ in range(300):
        if process.poll() is not None:
            raise RuntimeError("Installed desktop exited unexpectedly")
        if instance.exists():
            info = json.loads(instance.read_text(encoding="utf-8"))
            try:
                with opener.open(
                    info["base_url"] + "/api/v1/health", timeout=1
                ) as response:
                    if json.load(response)["product"] == "MediaWorkbench":
                        return process
            except OSError:
                pass
        time.sleep(0.1)
    raise RuntimeError("Installed application did not become ready")


def stop(app, process):
    run(app / "MediaWorkbench.exe", "--quit", timeout=10)
    process.wait(timeout=45)
    assert process.returncode == 0 and not instance.exists()


def uninstall_app():
    # Only this script's checked, isolated install root is ever passed to the uninstaller.
    assert install.is_relative_to((root / ".cache").resolve())
    run(install / "Update.exe", "--silent", "--rootDir", install, "uninstall")
    # Velopack exits before its delayed self-deletion helper (three seconds).
    # Starting setup before the entire root disappears races that helper.
    for _ in range(450):
        if not install.exists():
            break
        time.sleep(0.1)
    assert not install.exists(), "Uninstall directory cleanup has not completed"


app = install_app()
print("Installer completed; starting installed app", flush=True)
desktop = start(app)
try:
    run(
        app / "runtime/python/python.exe",
        root / "tests/client_smoke.py",
        "--instance",
        instance,
        "--output",
        directory / "成果",
        timeout=120,
    )
finally:
    stop(app, desktop)
database = data / "workbench.sqlite3"
with sqlite3.connect(database) as con:
    before = con.execute(
        "SELECT COUNT(*) FROM analyses WHERE state='completed'"
    ).fetchone()[0]
assert before == 3
uninstall_app()
assert database.is_file()
print("Uninstall retained user data; reinstalling the same package", flush=True)
app = install_app()
desktop = start(app)
try:
    with sqlite3.connect(database) as con:
        assert (
            con.execute(
                "SELECT COUNT(*) FROM analyses WHERE state='completed'"
            ).fetchone()[0]
            == before
        )
        assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
finally:
    stop(app, desktop)
uninstall_app()
assert database.is_file()
report = {
    "ok": True,
    "directory": str(directory),
    "analyses_retained": before,
    "checks": [
        "silent-install-in-chinese-path",
        "installed-desktop-launch",
        "fresh-skill-client",
        "15-exports",
        "explicit-exit",
        "uninstall-retains-user-data",
        "reinstall-retains-analyses",
    ],
    "limitation": "Current Windows with existing WebView2; no clean VM, offline network isolation, or cross-version update validation.",
}
(root / "release/installer-validation.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("Installer and retention checks passed", flush=True)
