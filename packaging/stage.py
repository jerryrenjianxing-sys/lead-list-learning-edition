"""Stage a relocatable release using locked wheels and redistributable runtimes."""

from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tomllib
import urllib.request
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "packaging" / "cache"
LOCK = ROOT / "packaging" / "downloads.lock.json"
STAGE = ROOT / "build" / "MediaWorkbench"


def run(*args, **kwargs):
    return subprocess.check_output([str(a) for a in args], cwd=ROOT, **kwargs)


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    locked = json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.exists() else {}

    def fetch(name, url, expected=None):
        path = CACHE / name
        previous = locked.get(name, {})
        if not path.exists():
            print("Downloading " + name, flush=True)
            with (
                urllib.request.urlopen(
                    previous.get("url", url), timeout=60
                ) as response,
                path.with_suffix(path.suffix + ".part").open("wb") as output,
            ):
                shutil.copyfileobj(response, output)
                url = response.geturl()
            path.with_suffix(path.suffix + ".part").replace(path)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected or previous.get("sha256"):
            assert actual == (expected or previous["sha256"]), (
                f"Checksum mismatch: {name}"
            )
        locked[name] = {
            "url": previous.get("url", url),
            "sha256": actual,
            "size": path.stat().st_size,
        }
        LOCK.write_text(json.dumps(locked, indent=2), encoding="utf-8", newline="\n")
        return path

    ignore = shutil.ignore_patterns(
        "__pycache__", "*.pyc", ".env", "*.log", "*.db", "*.sqlite3", "*.dpapi", "node_modules"
    )
    modules = [
        "workbench",
        "api",
        "base",
        "cache",
        "cmd_arg",
        "config",
        "constant",
        "database",
        "libs",
        "media_platform",
        "media_downloader",
        "model",
        "proxy",
        "store",
        "tools",
        "skills",
        "examples",
    ]
    for name in modules:
        shutil.copytree(ROOT / name, STAGE / name, dirs_exist_ok=True, ignore=ignore)
    for name in ["main.py", "var.py", "LICENSE", "README.md", "THIRD_PARTY_NOTICES.md"]:
        if (ROOT / name).is_file():
            shutil.copy2(ROOT / name, STAGE / name)
    (STAGE / "docs").mkdir(exist_ok=True)
    for pattern in (
        "*.ttf",
        "*.TTF",
        "*stopwords*",
        "VALIDATION.md",
        "UPGRADE_STATUS.md",
        "RECOVERY.md",
        "LOGIN_SESSIONS.md",
        "PUBLIC_PREVIEW.md",
    ):
        for file in (ROOT / "docs").glob(pattern):
            shutil.copy2(file, STAGE / "docs" / file.name)
    for file in (ROOT / "build" / "launcher").iterdir():
        if file.is_file():
            shutil.copy2(file, STAGE / file.name)
    notices = STAGE / "licenses"
    notices.mkdir(exist_ok=True)
    for source, target in (
        (CACHE / "webview-sdk/LICENSE.txt", "WebView2-LICENSE.txt"),
        (CACHE / "webview-sdk/NOTICE.txt", "WebView2-NOTICE.txt"),
        (CACHE / "json-sdk/LICENSE.md", "Newtonsoft.Json-LICENSE.md"),
        (ROOT / "packaging/licenses/Velopack-LICENSE.txt", "Velopack-LICENSE.txt"),
    ):
        shutil.copy2(source, notices / target)
    runtime = STAGE / "runtime"
    # Vite bundles runtime dependencies; ship their original notices too.
    frontend_lock = json.loads((ROOT / "webui/package-lock.json").read_text(encoding="utf-8"))
    for relative, package in frontend_lock["packages"].items():
        if not relative or package.get("dev"):
            continue
        package_dir = ROOT / "webui" / relative
        target = notices / "frontend" / relative.removeprefix("node_modules/")
        target.mkdir(parents=True, exist_ok=True)
        for file in package_dir.iterdir():
            if file.is_file() and (file.name.lower().startswith(("license", "licence", "notice", "copying")) or file.name in ("package.json", "README.md")):
                shutil.copy2(file, target / file.name)
    runtime.mkdir(exist_ok=True)
    python = Path(
        run("uv", "python", "find", "--system", "--managed-python", "3.11.16")
        .decode()
        .strip()
    )
    print("Staging standalone Python " + str(python), flush=True)
    python_target = (runtime / "python").resolve()
    assert python_target.is_relative_to((ROOT / "build").resolve())
    if python_target.exists():
        shutil.rmtree(python_target)
    shutil.copytree(python.parent, python_target, ignore=ignore)
    requirements = ROOT / "packaging" / "requirements.lock.txt"
    subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--output-file",
            requirements.relative_to(ROOT).as_posix(),
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(runtime / "python" / "python.exe"),
            "--target",
            str(runtime / "python" / "Lib" / "site-packages"),
            "--require-hashes",
            "-r",
            str(requirements),
        ],
        cwd=ROOT,
        check=True,
    )
    node = fetch(
        "node-v22.22.3-win-x64.zip",
        "https://nodejs.org/dist/v22.22.3/node-v22.22.3-win-x64.zip",
        "6c8d54f635feff4df76c2ca80f45332eb2ff57d25226edce36592e51a177ee33",
    )
    (runtime / "node").mkdir(exist_ok=True)
    with zipfile.ZipFile(node) as archive:
        for name in ("node.exe", "LICENSE", "README.md"):
            (runtime / "node" / name).write_bytes(
                archive.read("node-v22.22.3-win-x64/" + name)
            )
    browser_env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(runtime / "browsers")}
    import re

    locations = run(
        ROOT / ".venv/Scripts/python.exe",
        "-m",
        "playwright",
        "install",
        "--dry-run",
        "chromium",
    ).decode("utf-8")
    for location in re.findall(r"Install location:\s+([^\r\n]+)", locations):
        cached = Path(location.strip())
        if cached.is_dir():
            shutil.copytree(
                cached, runtime / "browsers" / cached.name, dirs_exist_ok=True
            )
    subprocess.run(
        [
            str(runtime / "python" / "python.exe"),
            "-m",
            "playwright",
            "install",
            "chromium",
        ],
        env=browser_env,
        cwd=ROOT,
        check=True,
    )
    webview = fetch(
        "WebView2RuntimeInstallerX64.exe",
        "https://go.microsoft.com/fwlink/?LinkId=2124701",
    )
    if webview.stat().st_size < 50_000_000:
        raise RuntimeError(
            "An offline WebView2 installer is required, not the small online bootstrapper"
        )
    shutil.copy2(webview, runtime / webview.name)
    # Media downloads are disabled. Clear the obsolete runtime from reused
    # staging folders without following a path outside this build directory.
    obsolete_ffmpeg = (runtime / "ffmpeg").resolve()
    if not obsolete_ffmpeg.is_relative_to(STAGE.resolve()):
        raise RuntimeError("Unexpected FFmpeg staging path")
    if obsolete_ffmpeg.exists():
        shutil.rmtree(obsolete_ffmpeg)
    manifest = {
        "product": "MediaWorkbench",
        "version": tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"],
        "python": "3.11.16",
        "node": "22.22.3",
        "upstream": "380b426000aac3d612837ed72c99808347dc94c9",
        "build_time": datetime.now(timezone.utc).isoformat(),
        "source_revision": run("git", "rev-parse", "HEAD").decode().strip(),
        "source_dirty": bool(run("git", "status", "--porcelain").strip()),
        "downloads": locked,
        "files": {},
    }
    for file in sorted(STAGE.rglob("*")):
        if file.is_file() and file.name != "build-manifest.json":
            manifest["files"][file.relative_to(STAGE).as_posix()] = {
                "size": file.stat().st_size,
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            }
    (STAGE / "build-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "staged": str(STAGE),
                "files": len(manifest["files"]),
                "bytes": sum(f["size"] for f in manifest["files"].values()),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
