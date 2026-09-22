"""Relocate the complete app, strip developer PATH, and exercise its own runtime."""

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument(
    "--directory", default=str(root / ".cache/迁移验证 新电脑/MediaWorkbench")
)
args = parser.parse_args()
destination = Path(args.directory).resolve()
source = root / "build/MediaWorkbench"
shutil.copytree(source, destination, dirs_exist_ok=True)
data = destination.parent / "独立用户数据"
instance = destination.parent / "instance.json"
env = {
    k: v
    for k, v in os.environ.items()
    if k
    not in (
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_PROJECT_ENVIRONMENT",
        "MEDIAWORKBENCH_DATA_DIR",
        "MEDIAWORKBENCH_INSTANCE",
    )
}
env.update(
    {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PATH": str(destination / "runtime/node")
        + ";"
        + os.environ["SystemRoot"]
        + "\\System32",
        "PLAYWRIGHT_BROWSERS_PATH": str(destination / "runtime/browsers"),
        "MEDIAWORKBENCH_INSTANCE": str(instance),
        "MEDIAWORKBENCH_DATA_DIR": str(data),
    }
)
python = destination / "runtime/python/python.exe"
opener = build_opener(ProxyHandler({}))


def start():
    process = subprocess.Popen(
        [str(python), "-m", "workbench", "--port", "0"],
        cwd=destination,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Relocated host exited " + str(process.returncode))
        if instance.exists():
            info = json.loads(instance.read_text(encoding="utf-8"))
            if info["pid"] == process.pid:
                try:
                    with opener.open(
                        info["base_url"] + "/api/v1/health", timeout=1
                    ) as response:
                        if json.load(response)["product"] == "MediaWorkbench":
                            return process, info
                except OSError:
                    pass
        time.sleep(0.1)
    raise RuntimeError("Relocated host did not start")


def stop(process, info):
    request = Request(
        info["base_url"] + "/api/v1/host/stop",
        method="POST",
        data=b"{}",
        headers={
            "Authorization": "Bearer " + info["token"],
            "Content-Type": "application/json",
        },
    )
    with opener.open(request, timeout=5) as response:
        assert response.status == 200
    process.wait(timeout=30)
    assert process.returncode == 0


host, info = start()
try:
    subprocess.run(
        [
            str(python),
            str(root / "tests/client_smoke.py"),
            "--instance",
            str(instance),
            "--output",
            str(destination.parent / "验收成果"),
        ],
        cwd=destination,
        env=env,
        check=True,
        timeout=90,
    )
    test = (
        "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(); page=b.new_page(); page.goto('"
        + info["base_url"]
        + "'); assert 'Media Deep Researcher' in page.title(); b.close(); p.stop(); import execjs; assert execjs.eval('21*2')==42; print('bundled browser and node OK')"
    )
    subprocess.run(
        [str(python), "-c", test], cwd=destination, env=env, check=True, timeout=45
    )
finally:
    stop(host, info)
host, info = start()
try:
    with opener.open(
        Request(
            info["base_url"] + "/api/v1/analyses",
            headers={"Authorization": "Bearer " + info["token"]},
        ),
        timeout=5,
    ) as response:
        analyses = json.load(response)["items"]
    assert len(analyses) >= 3 and all(a["state"] == "completed" for a in analyses)
finally:
    stop(host, info)
(root / ".cache/portable-results.json").write_text(
    json.dumps(
        {
            "ok": True,
            "directory": str(destination),
            "python": str(python),
            "developer_path_removed": True,
            "relocated_chinese_space_path": True,
            "restart_retained_analyses": len(analyses),
            "checks": [
                "portable-host",
                "fresh-skill-client",
                "15-exports",
                "bundled-chromium",
                "bundled-node-signing",
                "cooperative-exit",
                "restart-persistence",
            ],
            "limitation": "Current Windows machine; not a clean Windows 10/11 VM or a real platform test.",
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
print("Relocation checks passed")
