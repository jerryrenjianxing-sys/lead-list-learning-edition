"""Measure the shipped Skill against an isolated packaged host, without an LLM.

Cold means a new bundled Python process, not cleared Windows/antivirus caches.
Warm means a fresh Client and protected handshake in one existing Python process.
This is a standalone acceptance runner; it never starts the daily desktop.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import zipfile

import psutil


class BenchmarkFailure(RuntimeError):
    """Only controlled error codes, never a response or credential, leave the runner."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


HOST_BOOTSTRAP = """import runpy,sys
root=sys.argv[1]
sys.path.insert(0,root)
sys.argv=['workbench','--port','0']
runpy.run_module('workbench',run_name='__main__')
"""

WARM_PROBE = """import importlib.util,json,sys,time
script,instance,expected_path,count=sys.argv[1:]
expected=json.load(open(expected_path,encoding='utf-8'))
started=time.perf_counter()
spec=importlib.util.spec_from_file_location('fresh_benchmark_client',script)
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
import_seconds=time.perf_counter()-started
samples=[]
for index in range(int(count)):
    started=time.perf_counter()
    client=module.Client(instance)
    discovered=time.perf_counter()
    value=client.connect()
    finished=time.perf_counter()
    assert value.get('product')=='MediaWorkbench' and value.get('connection')=='ready'
    assert value.get('status')=='ok' and value.get('api_version')==1
    assert all(value.get(key)==expected[key] for key in ('version','instance_id','workspace_id','capability_revision'))
    samples.append({'index':index+1,'ok':True,'discovery_seconds':discovered-started,
                    'handshake_seconds':finished-discovered,'total_seconds':finished-started})
print(json.dumps({'ok':True,'import_seconds':import_seconds,'samples':samples}))
"""


def statistics_for(values: list[float], target: float) -> dict:
    ordered = sorted(values)
    count = len(ordered)
    return {
        "count": count,
        "median_seconds": statistics.median(ordered) if ordered else None,
        "max_seconds": max(ordered) if ordered else None,
        "p95_seconds": ordered[math.ceil(0.95 * count) - 1] if ordered else None,
        "p95_method": "nearest_rank",
        "target_p95_seconds": target,
        "sufficient_samples": count >= 20,
        "target_met": ordered[math.ceil(0.95 * count) - 1] <= target if count >= 20 else None,
    }


def identity_ok(value: dict, expected: dict) -> bool:
    return (
        value.get("product") == "MediaWorkbench" and value.get("connection") == "ready"
        and value.get("status") == "ok" and value.get("api_version") == 1
        and all(value.get(key) == expected[key]
                for key in ("version", "instance_id", "workspace_id", "capability_revision"))
    )


def run(app: Path, count: int) -> dict:
    app = app.resolve()
    python = app / "runtime" / "python" / "python.exe"
    if not python.is_file() or not (app / "workbench" / "app.py").is_file():
        raise BenchmarkFailure("COMPLETE_PACKAGED_RUNTIME_REQUIRED")
    manifest = json.loads((app / "build-manifest.json").read_text(encoding="utf-8"))
    build = json.loads((app / "build-version.json").read_text(encoding="utf-8"))
    if manifest.get("product") != "MediaWorkbench" or manifest.get("version") != build.get("version"):
        raise BenchmarkFailure("PACKAGE_IDENTITY_MISMATCH")
    report = {
        "ok": False, "product": "MediaWorkbench", "version": build["version"],
        "source_revision": manifest.get("source_revision"), "requested_count": count,
        "developer_path_removed": True, "isolated": True,
        "definition": {
            "cold": "New bundled Python CLI process; OS caches remain unchanged.",
            "warm": "One bundled Python process; each sample constructs a new Client and authorizes anew.",
            "scope": "Runtime and protected local handshake; not fresh LLM/Agent onboarding or full self-test.",
        },
        "setup": {}, "cold": {"samples": []}, "warm": {"samples": []},
        "cleanup_complete": False,
    }
    opener = build_opener(ProxyHandler({}), NoRedirect())
    temporary = tempfile.TemporaryDirectory(prefix="MDR 接入基准 中文 ")
    isolated = Path(temporary.name).resolve()
    instance = isolated / "发现文件" / "instance.json"
    data = isolated / "独立用户数据"
    cwd = isolated / "外部 Agent 任意工作目录"
    cwd.mkdir()
    scratch = isolated / "临时文件"
    scratch.mkdir()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("MEDIAWORKBENCH_") and k not in {
               "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}}
    env.update(
        PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1", PYTHONSAFEPATH="1",
        PATH=str(app / "runtime" / "node") + os.pathsep + str(Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32"),
        PLAYWRIGHT_BROWSERS_PATH=str(app / "runtime" / "browsers"),
        MEDIAWORKBENCH_DATA_DIR=str(data), MEDIAWORKBENCH_INSTANCE=str(instance),
        TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch),
    )
    process = None
    info = None
    owned: dict[int, float] = {}
    graceful = False

    def capture_owned():
        if process is None:
            return
        try:
            parent = psutil.Process(process.pid)
            for child in [parent, *parent.children(recursive=True)]:
                owned[child.pid] = child.create_time()
        except psutil.Error:
            pass

    def request(path: str, *, method="GET", token=None):
        if info is None:
            raise BenchmarkFailure("HOST_NOT_READY")
        headers = {"Authorization": "Bearer " + (info["token"] if token is None else token)}
        body = b"{}" if method == "POST" else None
        if body is not None:
            headers["Content-Type"] = "application/json"
        with opener.open(Request(info["base_url"] + path, data=body, headers=headers, method=method), timeout=5) as response:
            return response.read()

    def business_snapshot():
        snapshot = {key: len(json.loads(request("/api/v1/" + key))["items"])
                    for key in ("jobs", "datasets", "analyses", "artifacts")}
        snapshot["diagnostic"] = json.loads(request("/api/v1/diagnostics/self-tests/latest"))
        return snapshot

    try:
        started = time.perf_counter()
        with (isolated / "host.log").open("wb") as log:
            process = subprocess.Popen([str(python), "-c", HOST_BOOTSTRAP, str(app)],
                                       cwd=cwd, env=env, stdout=log, stderr=log,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            capture_owned()
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise BenchmarkFailure("ISOLATED_HOST_EXITED")
                if instance.is_file():
                    candidate = json.loads(instance.read_text(encoding="utf-8-sig"))
                    url = urlsplit(candidate.get("base_url", ""))
                    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
                        raise BenchmarkFailure("NON_LOCAL_DISCOVERY")
                    if Path(candidate["data_directory"]).resolve() != data.resolve():
                        raise BenchmarkFailure("NON_ISOLATED_DATA")
                    capture_owned()
                    if candidate.get("pid") not in owned:
                        raise BenchmarkFailure("HOST_PROCESS_IDENTITY_MISMATCH")
                    info = candidate
                    try:
                        expected = json.loads(request("/api/v1/agent/handshake"))
                        break
                    except OSError:
                        pass
                time.sleep(0.05)
            else:
                raise BenchmarkFailure("ISOLATED_HOST_STARTUP_TIMEOUT")
            report["setup"]["host_start_seconds"] = time.perf_counter() - started
            if expected.get("version") != build["version"] or expected.get("status") != "ok":
                raise BenchmarkFailure("HOST_VERSION_MISMATCH")
            try:
                request("/api/v1/agent/handshake", token="benchmark-invalid-token")
            except HTTPError as exc:
                if exc.code != 401:
                    raise BenchmarkFailure("BAD_CREDENTIAL_NOT_REJECTED") from None
                exc.close()
            else:
                raise BenchmarkFailure("HANDSHAKE_NOT_PROTECTED")
            before = business_snapshot()
            if any(before[key] for key in ("jobs", "datasets", "analyses", "artifacts")) or before["diagnostic"] is not None:
                raise BenchmarkFailure("ISOLATED_WORKSPACE_NOT_EMPTY")
            started = time.perf_counter()
            archive_bytes = request("/api/v1/skill")
            report["setup"]["skill_download_seconds"] = time.perf_counter() - started
            report["skill_zip_sha256"] = hashlib.sha256(archive_bytes).hexdigest()
            started = time.perf_counter()
            fresh = isolated / "全新 Skill"
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                for name in archive.namelist():
                    if not (fresh / name).resolve().is_relative_to(fresh.resolve()):
                        raise BenchmarkFailure("UNSAFE_SKILL_ARCHIVE")
                archive.extractall(fresh)
            script = fresh / "media-workbench" / "scripts" / "workbench.py"
            if not script.is_file():
                raise BenchmarkFailure("SHIPPED_CLIENT_MISSING")
            report["setup"]["skill_extract_seconds"] = time.perf_counter() - started
            for index in range(count):
                client_cwd = cwd / f"冷进程 {index + 1:02d}"
                client_cwd.mkdir()
                started = time.perf_counter()
                result = subprocess.run([str(python), str(script), "--instance", str(instance), "connect"],
                                        cwd=client_cwd, env=env, capture_output=True, timeout=15,
                                        encoding="utf-8")
                sample = {"index": index + 1, "total_seconds": time.perf_counter() - started, "ok": False}
                report["cold"]["samples"].append(sample)
                if result.returncode:
                    raise BenchmarkFailure("COLD_CONNECT_FAILED")
                value = json.loads(result.stdout)
                if not identity_ok(value, expected):
                    raise BenchmarkFailure("COLD_AUTHORIZATION_UNCONFIRMED")
                sample["ok"] = True
            expected_path = isolated / "expected-identity.json"
            expected_path.write_text(json.dumps({key: expected[key] for key in (
                "version", "instance_id", "workspace_id", "capability_revision")}), encoding="utf-8")
            started = time.perf_counter()
            result = subprocess.run([str(python), "-c", WARM_PROBE, str(script), str(instance), str(expected_path), str(count)],
                                    cwd=cwd, env=env, capture_output=True, timeout=count * 12 + 15, encoding="utf-8")
            report["setup"]["warm_helper_process_seconds"] = time.perf_counter() - started
            if result.returncode:
                raise BenchmarkFailure("WARM_CONNECT_FAILED")
            warm = json.loads(result.stdout)
            report["warm"]["samples"] = warm["samples"]
            report["setup"]["warm_module_import_seconds"] = warm["import_seconds"]
            if len(warm["samples"]) != count or not all(sample.get("ok") for sample in warm["samples"]):
                raise BenchmarkFailure("WARM_SAMPLE_COUNT_MISMATCH")
            if business_snapshot() != before:
                raise BenchmarkFailure("CONNECTION_CREATED_BUSINESS_OR_DIAGNOSTIC_DATA")
            report["business_unchanged"] = True
            report["cold"]["summary"] = statistics_for([s["total_seconds"] for s in report["cold"]["samples"]], 5)
            report["warm"]["summary"] = statistics_for([s["total_seconds"] for s in warm["samples"]], 2)
            report["warm"]["stages"] = {key: statistics_for([s[key] for s in warm["samples"]], 2)
                                          for key in ("discovery_seconds", "handshake_seconds")}
            report["ok"] = True
    except Exception as exc:
        report["error_code"] = str(exc) if isinstance(exc, BenchmarkFailure) else type(exc).__name__
    finally:
        capture_owned()
        if process is not None:
            try:
                if process.poll() is None and info is not None:
                    request("/api/v1/host/stop", method="POST")
                process.wait(timeout=15)
                graceful = process.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
            for pid, created in reversed(list(owned.items())):
                try:
                    child = psutil.Process(pid)
                    if abs(child.create_time() - created) < 0.01:
                        child.kill()
                except psutil.NoSuchProcess:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        survivors = []
        for pid, created in owned.items():
            try:
                child = psutil.Process(pid)
                if abs(child.create_time() - created) < 0.01:
                    survivors.append(child)
            except psutil.NoSuchProcess:
                pass
        if survivors:
            _, survivors = psutil.wait_procs(survivors, timeout=5)
        report["graceful_host_shutdown"] = graceful
        try:
            if isolated.is_symlink() or isolated != Path(temporary.name).resolve():
                raise BenchmarkFailure("UNSAFE_CLEANUP_PATH")
            temporary.cleanup()
            report["cleanup_complete"] = not survivors and not isolated.exists()
        except (OSError, BenchmarkFailure):
            report["cleanup_complete"] = False
        if not report["cleanup_complete"] or not graceful:
            report["ok"] = False
            report.setdefault("error_code", "BENCHMARK_CLEANUP_OR_SHUTDOWN_FAILED")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.count <= 100:
        parser.error("count must be between 1 and 100")
    target = args.output.resolve()
    if target.is_relative_to(args.directory.resolve()):
        parser.error("output must stay outside the immutable candidate directory")
    try:
        report = run(args.directory, args.count)
    except Exception as exc:
        report = {"ok": False, "error_code": str(exc) if isinstance(exc, BenchmarkFailure) else type(exc).__name__}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "result": str(target)}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
