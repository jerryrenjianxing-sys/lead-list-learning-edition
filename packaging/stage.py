"""Stage a relocatable release using locked wheels and redistributable runtimes."""

from pathlib import Path
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tomllib
import urllib.request
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "packaging" / "cache"
LOCK = ROOT / "packaging" / "downloads.lock.json"
STAGE = ROOT / "build" / "MediaWorkbench"
SHELL = ROOT / "build" / "electron" / "MediaWorkbench-win32-x64"


def digest_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def shell_versions(root=ROOT):
    lock = json.loads((root / "electron/package-lock.json").read_text(encoding="utf-8"))
    packages = lock["packages"]
    versions = {
        "electron": packages["node_modules/electron"]["version"],
        "forge": packages["node_modules/@electron-forge/cli"]["version"],
        "velopack": packages["node_modules/velopack"]["version"],
    }
    if versions != {"electron": "44.7.0", "forge": "8.0.1", "velopack": "1.2.0"}:
        raise RuntimeError("Desktop dependency versions differ from the release contract")
    return versions


def validate_shell(shell, version, versions, cache=CACHE):
    shell = Path(shell)
    receipt = json.loads((shell.parent / "build-shell.json").read_text(encoding="utf-8"))
    expected = {"version": version, "platform": "win32", "arch": "x64", **versions}
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Forge shell version or architecture differs from source")
    for name in ("MediaWorkbench.exe", "resources/app.asar", "LICENSE", "LICENSES.chromium.html"):
        if not (shell / name).is_file():
            raise RuntimeError("Missing Electron shell file: " + name)
    if not list((shell / "resources/app.asar.unpacked").rglob("*.node")):
        raise RuntimeError("Native updater module must be unpacked outside app.asar")
    # Forge changes executable branding; Chromium payloads must retain the
    # exact bytes of the separately checksum-verified official distribution.
    archive = cache / f"electron-v{versions['electron']}-win32-x64.zip"
    with zipfile.ZipFile(archive) as distribution:
        for name in ("icudtl.dat", "resources.pak", "chrome_100_percent.pak", "chrome_200_percent.pak", "LICENSE", "LICENSES.chromium.html"):
            with distribution.open(name) as stream:
                expected_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest_file(shell / name) != expected_hash:
                raise RuntimeError("Electron runtime differs from locked distribution: " + name)
    return receipt


def bind_shell_source(receipt, revision, dirty):
    if receipt.get("source_revision") != revision or receipt.get("source_dirty") is not dirty:
        raise RuntimeError("Forge shell was built from a different source checkout; rebuild Electron")


def merge_shell(shell, stage, notices):
    """Retain the upstream project license while merging the full desktop tree."""
    stage, notices = Path(stage), Path(notices)
    notices.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(shell) / "LICENSE", notices / "Electron-LICENSE.txt")
    shutil.copy2(Path(shell) / "LICENSES.chromium.html", notices / "Chromium-LICENSES.html")
    for source in Path(shell).iterdir():
        if source.name == "LICENSE":
            continue
        target = stage / source.name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)


def npm_notices(package_root, notices):
    """Ship notices for bundled production dependencies, including transitives."""
    lock = json.loads((package_root / "package-lock.json").read_text(encoding="utf-8"))
    for relative, package in lock["packages"].items():
        if not relative or package.get("dev"):
            continue
        package_dir = package_root / relative
        if not package_dir.is_dir():
            if package.get("optional"):
                continue
            raise RuntimeError("Bundled npm dependency is missing: " + relative)
        target = notices / relative.removeprefix("node_modules/")
        target.mkdir(parents=True, exist_ok=True)
        for file in package_dir.iterdir():
            if file.is_file() and (file.name.lower().startswith(("license", "licence", "notice", "copying")) or file.name in ("package.json", "README.md")):
                shutil.copy2(file, target / file.name)


def run(*args, **kwargs):
    return subprocess.check_output([str(a) for a in args], cwd=ROOT, **kwargs)


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    desktop_versions = shell_versions()
    # Verify before clearing the previous staging output, and never stage
    # a legacy shell if a Forge build is missing or stale.
    from importlib.util import module_from_spec, spec_from_file_location

    specification = spec_from_file_location("bootstrap_electron", ROOT / "packaging/bootstrap-electron.py")
    bootstrap = module_from_spec(specification)
    specification.loader.exec_module(bootstrap)
    bootstrap.prepare()
    desktop_receipt = validate_shell(SHELL, version, desktop_versions)
    source_revision = run("git", "rev-parse", "HEAD").decode().strip()
    source_dirty = bool(run("git", "status", "--porcelain").strip())
    bind_shell_source(desktop_receipt, source_revision, source_dirty)
    if STAGE.exists():
        if STAGE.is_symlink() or (getattr(STAGE.stat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT) or not STAGE.resolve().is_relative_to((ROOT / "build").resolve()):
            raise RuntimeError("Unexpected staging directory")
        shutil.rmtree(STAGE)
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
        actual = digest_file(path)
        if expected or previous.get("sha256"):
            assert actual == (expected or previous["sha256"]), (
                f"Checksum mismatch: {name}"
            )
        if not previous or actual != previous.get("sha256"):
            raise RuntimeError("Release downloads must already exist in downloads.lock.json: " + name)
        if path.stat().st_size != previous["size"]:
            raise RuntimeError("Release download size mismatch: " + name)
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
    for name in ["main.py", "var.py", "LICENSE", "README.md", "THIRD_PARTY_NOTICES.md", "upstream.lock.json"]:
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
        "ROLLBACK_0.4.3.md",
        "LOGIN_SESSIONS.md",
        "PUBLIC_PREVIEW.md",
        "RELEASE_NOTES.md",
        "ELECTRON_MIGRATION.md",
    ):
        for file in (ROOT / "docs").glob(pattern):
            shutil.copy2(file, STAGE / "docs" / file.name)
    notices = STAGE / "licenses"
    notices.mkdir(exist_ok=True)
    merge_shell(SHELL, STAGE, notices)
    shutil.copy2(ROOT / "packaging/licenses/Velopack-LICENSE.txt", notices / "Velopack-LICENSE.txt")
    runtime = STAGE / "runtime"
    # Vite bundles runtime dependencies; ship their original notices too.
    npm_notices(ROOT / "webui", notices / "frontend")
    npm_notices(ROOT / "electron", notices / "electron")
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
    requirements = CACHE / "requirements.generated.txt"
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
    def dependency_lines(path):
        return [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]
    if dependency_lines(requirements) != dependency_lines(ROOT / "packaging/requirements.lock.txt"):
        raise RuntimeError("Exported dependencies differ from committed requirements.lock.txt")
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
    # Media downloads are disabled. Clear the obsolete runtime from reused
    # staging folders without following a path outside this build directory.
    obsolete_ffmpeg = (runtime / "ffmpeg").resolve()
    if not obsolete_ffmpeg.is_relative_to(STAGE.resolve()):
        raise RuntimeError("Unexpected FFmpeg staging path")
    if obsolete_ffmpeg.exists():
        shutil.rmtree(obsolete_ffmpeg)
    (STAGE / "build-version.json").write_text(json.dumps({"version": version, "channel": "win-preview", "desktop": "electron"}), encoding="utf-8")
    manifest = {
        "product": "MediaWorkbench",
        "version": version,
        "python": "3.11.16",
        "node": "22.22.3",
        "desktop": {"kind": "electron", **desktop_receipt},
        "upstream": json.loads((ROOT / "upstream.lock.json").read_text(encoding="utf-8"))["revision"],
        "build_time": datetime.now(timezone.utc).isoformat(),
        "source_revision": source_revision,
        "source_dirty": source_dirty,
        "downloads": locked,
        "files": {},
    }
    for file in sorted(STAGE.rglob("*")):
        if file.is_file() and file.name != "build-manifest.json":
            manifest["files"][file.relative_to(STAGE).as_posix()] = {
                "size": file.stat().st_size,
                "sha256": digest_file(file),
            }
    if run("git", "rev-parse", "HEAD").decode().strip() != source_revision or bool(run("git", "status", "--porcelain").strip()) != source_dirty:
        raise RuntimeError("Source checkout changed while staging; rebuild before release")
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
