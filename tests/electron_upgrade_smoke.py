"""Validate legacy-to-Electron installers and native Velopack full updates.

All app roots and business data are fresh directories under .cache. The old
desktop is never started: its bundled Python seeds synthetic data instead.
Velopack's shared per-user registration and product shortcuts are backed up
before installation and restored even when a check fails. This runner must
not run concurrently with another installer for the same product.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from contextlib import closing
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from urllib.request import build_opener, ProxyHandler, Request

import psutil

ROOT = Path(__file__).resolve().parents[1]
CACHE = (ROOT / ".cache").resolve()
TITLE = "Media Deep Researcher"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\MediaWorkbench.Desktop"
OPENER = build_opener(ProxyHandler({}))


def wait(predicate, message, timeout=60):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(0.15)
    raise AssertionError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_root(path):
    result = Path(path).resolve()
    if result == CACHE or not result.is_relative_to(CACHE):
        raise RuntimeError("Installer root must stay in a dedicated .cache directory")
    return result


def known_folder(guid):
    folder = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(wintypes.LPWSTR)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole = ctypes.WinDLL("ole32")
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    result = wintypes.LPWSTR()
    status = shell.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(result))
    if status != 0:
        raise OSError(f"Cannot resolve Windows known folder ({status})")
    try:
        return Path(result.value)
    finally:
        ole.CoTaskMemFree(result)


def registry_tree(winreg, key):
    values, children = [], {}
    count_children, count_values, _ = winreg.QueryInfoKey(key)
    for index in range(count_values):
        name, value, kind = winreg.EnumValue(key, index)
        values.append((name, value, kind))
    for index in range(count_children):
        name = winreg.EnumKey(key, index)
        with winreg.OpenKey(key, name) as child:
            children[name] = registry_tree(winreg, child)
    return {"values": values, "children": children}


def delete_registry_tree(winreg, path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as key:
            while winreg.QueryInfoKey(key)[0]:
                delete_registry_tree(winreg, path + "\\" + winreg.EnumKey(key, 0))
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        pass


def write_registry_tree(winreg, path, tree):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        for name, value, kind in tree["values"]:
            winreg.SetValueEx(key, name, 0, kind, value)
    for name, child in tree["children"].items():
        write_registry_tree(winreg, path + "\\" + name, child)


class ShellRegistration:
    def __init__(self, directory):
        import winreg
        self.winreg = winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as key:
                self.registry = registry_tree(winreg, key)
        except FileNotFoundError:
            self.registry = None
        desktop = known_folder("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")
        menu = known_folder("625B53C3-AB48-4EC1-BA1F-A1EF4146FC19") / "Programs"
        self.shortcuts = {path / (TITLE + ".lnk"): None for path in (desktop, menu)}
        for path in self.shortcuts:
            if path.is_file():
                self.shortcuts[path] = path.read_bytes()
        # Retain a recovery record before any installer can alter shell state.
        recovery = {"registry": self.registry, "shortcuts": {str(path): (base64.b64encode(content).decode("ascii") if content is not None else None) for path, content in self.shortcuts.items()}}
        (directory / "shell-registration-before.json").write_text(json.dumps(recovery, indent=2, default=lambda value: {"bytes": base64.b64encode(value).decode("ascii")}), encoding="utf-8")

    def restore_shortcuts(self):
        for path, content in self.shortcuts.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

    def restore(self):
        self.restore_shortcuts()
        delete_registry_tree(self.winreg, REG_KEY)
        if self.registry is not None:
            write_registry_tree(self.winreg, REG_KEY, self.registry)

    def verify(self):
        for path, content in self.shortcuts.items():
            assert (path.read_bytes() if path.exists() else None) == content
        try:
            with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, REG_KEY) as key:
                current = registry_tree(self.winreg, key)
        except FileNotFoundError:
            current = None
        assert current == self.registry, "Original installation registration was not restored"


def run_command(arguments, env, *, timeout=360, cwd=None):
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return subprocess.run([str(argument) for argument in arguments], env=env, cwd=cwd, startupinfo=startup, check=True, timeout=timeout, capture_output=True, text=True, encoding="utf-8", errors="replace")


def app_version(app):
    document = ET.parse(app / "sq.version")
    return next(item.text for item in document.iter() if item.tag.rsplit("}", 1)[-1] == "version")


def request(info, method, path, body=None):
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    query = Request(info["base_url"] + path, data=payload, method=method, headers={"Authorization": "Bearer " + info["token"], "Content-Type": "application/json"})
    with OPENER.open(query, timeout=8) as response:
        return json.load(response)


def launch_host_or_desktop(app, env, data, instance, *, desktop=False):
    arguments = [app / "MediaWorkbench.exe"] if desktop else [app / "runtime/python/python.exe", "-m", "workbench", "--data-dir", data, "--port", "0"]
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    log = (data.parent / ("desktop.log" if desktop else "old-host.log")).open("ab")
    process = subprocess.Popen([str(argument) for argument in arguments], cwd=app, env=env, startupinfo=startup, stdout=log, stderr=log)
    def ready():
        if process.poll() is not None:
            raise AssertionError("Isolated process exited before becoming ready")
        if not instance.exists():
            return False
        info = json.loads(instance.read_text(encoding="utf-8"))
        assert Path(info["data_directory"]).resolve() == data.resolve()
        try:
            health = request(info, "GET", "/api/v1/health")
            return info if health["status"] == "ok" else False
        except OSError:
            return False
    try:
        info = wait(ready, "Isolated host did not become ready")
        return process, log, info
    except BaseException:
        stop_owned_tree(process)
        log.close()
        raise


def stop_owned_tree(process):
    if process.poll() is None:
        children = psutil.Process(process.pid).children(recursive=True)
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        process.kill()
        process.wait(timeout=15)


def stop_host_or_desktop(app, env, instance, process, log, info, *, desktop=False):
    try:
        if desktop:
            # Electron's guard uses only the isolated namespace from env.
            run_command([app / "MediaWorkbench.exe", "--quit"], env, timeout=15)
        else:
            request(info, "POST", "/api/v1/host/stop", {})
        assert process.wait(timeout=60) == 0
        wait(lambda: not instance.exists(), "Isolated discovery survived shutdown")
    finally:
        stop_owned_tree(process)
        log.close()


def snapshot(data):
    with closing(sqlite3.connect(data / "workbench.sqlite3")) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        rows = {}
        for table in tables:
            # Table names come only from SQLite's own schema, never user input.
            values = connection.execute('SELECT * FROM "' + table.replace('"', '""') + '"').fetchall()
            rows[table] = sorted(json.dumps(value, ensure_ascii=False) for value in values)
    files = {}
    for folder in ("browser_data", "sessions", "artifacts"):
        for path in (data / folder).rglob("*"):
            if path.is_file():
                files[str(path.relative_to(data))] = digest(path)
    return {"rows": rows, "files": files}


RECOVERY_TABLES = {'job_attempts','attempt_metrics','event_details','operations','collection_scopes','raw_commits',
                   'access_checks','access_state','access_waits','access_samples','scope_metrics','scope_records','execution_sessions','access_replays','job_retry_plans'}


def preserved(data,before):
    current=snapshot(data)
    assert current['files']==before['files'],'Saved accounts or artifact files changed'
    assert {k:current['rows'][k] for k in before['rows']}==before['rows'],'Existing business rows changed'
    added=set(current['rows'])-set(before['rows'])
    assert added<=RECOVERY_TABLES,'Migration created an unrecognized business table'
    if 'job_retry_plans' in added:
        assert not current['rows']['job_retry_plans'],'Historical tasks gained automatic retry plans during migration'
    if added:
        with closing(sqlite3.connect(data/'workbench.sqlite3')) as con:
            assert con.execute('PRAGMA user_version').fetchone()[0]==6
        backups=list((data/'backups').glob('*.sqlite3'))
        backed_up=False
        for path in backups:
            with closing(sqlite3.connect(path)) as con:
                backed_up |= con.execute('PRAGMA user_version').fetchone()[0]==4
        assert backed_up,'Schema 4 was not backed up'
    return current


SEED = '''
import json, sys
from pathlib import Path
from workbench.database import Database, now, dump
from workbench.session_store import SessionStore
data, app = map(Path, sys.argv[1:])
db = Database(data)
dataset = db.query("SELECT id FROM datasets LIMIT 1")[0]["id"]
stamp = now()
db.execute("INSERT INTO jobs(id,platform,state,options,dataset_id,created,updated) VALUES(?,?,?,?,?,?,?)", ("upgrade-fixture", "dy", "completed", dump({"keywords":"synthetic-only"}), dataset, stamp, stamp))
profile = data / "browser_data/cdp_dy_user_data_dir/Default"
profile.mkdir(parents=True, exist_ok=True)
(profile / "Preferences").write_text('{"upgrade_fixture":true}', encoding="utf-8")
(profile / "Cookies").write_bytes(b"synthetic profile fixture, no real credentials")
browser = next((app / "runtime/browsers").rglob("chrome.exe"))
store = SessionStore(db)
store.bind("dy", browser)
storage = {"cookies":[{"name":"fixture","value":"synthetic-not-a-credential","domain":"example.test","path":"/"}],"origins":[]}
assert store.save("dy", storage, {"id":"upgrade-fixture-account","name":"升级测试账号"}, new_auth=True)
db.execute("INSERT OR REPLACE INTO platform_auth VALUES(?,?,?,?,?,?,?)", ("dy", "authenticated", stamp, stamp, "upgrade-fixture", 0, ""))
assert store.load("dy") == storage
print(json.dumps({"ok":True,"synthetic_snapshot":True}))
'''

VERIFY_SNAPSHOT = '''
import json, sys
from pathlib import Path
from workbench.database import Database
from workbench.session_store import SessionStore
store = SessionStore(Database(Path(sys.argv[1])))
storage = store.load("dy")
assert storage["cookies"][0]["value"] == "synthetic-not-a-credential"
assert store.get("dy")["account_id"] == "upgrade-fixture-account"
assert Path(store.get("dy")["browser"]).is_file()
print(json.dumps({"ok":True,"snapshot_decrypts":True,"browser_binding_exists":True}))
'''

EXHAUSTED_SEED = '''
import sys,time
from pathlib import Path
from workbench.database import Database,dump,now
db=Database(Path(sys.argv[1]))
dataset=db.query('SELECT id FROM datasets LIMIT 1')[0]['id']
stamp=now()
db.execute("INSERT INTO jobs(id,platform,state,options,dataset_id,created,updated) VALUES(?,?,?,?,?,?,?)",('exhausted-upgrade','dy','waiting_retry',dump({'crawler_type':'search','keywords':'synthetic-only'}),dataset,stamp,stamp))
db.execute("INSERT INTO job_retry_plans VALUES('exhausted-upgrade',1,999,5400,7200,?,NULL,'collection_incomplete','upgrade-fixture-account',?)",(time.time()-10,stamp))
db.execute("INSERT INTO access_state VALUES('dy','upgrade-fixture-account','replies','suspected_restriction','empty_response',?,3,?,NULL)",(time.time()-10,stamp))
db.execute("INSERT INTO access_waits VALUES('exhausted-upgrade','replies','upgrade-fixture-account',1,?)",(stamp,))
'''


def preserved_exhausted(data, before):
    """Only the declared capability repair and its single audit event may differ."""
    current=snapshot(data)
    assert current['files']==before['files']
    assert set(current['rows'])==set(before['rows'])
    for table,rows in before['rows'].items():
        if table not in ('access_state','events','event_details'):
            assert current['rows'][table]==rows,table+' changed during state repair'
    for table in ('events','event_details'):
        assert set(before['rows'][table])<=set(current['rows'][table]),'Prior evidence changed'
        assert len(current['rows'][table])==len(before['rows'][table])+1,'Unexpected repair evidence'
    states=[json.loads(row) for row in current['rows']['access_state']]
    assert len(states)==1
    expected=json.loads(before['rows']['access_state'][0])
    expected[3]='manual_action';expected[5]=None;expected[7]=states[0][7]
    assert states[0]==expected,'Unexpected capability changes'
    with closing(sqlite3.connect(data/'workbench.sqlite3')) as con:
        assert con.execute("SELECT count(*) FROM event_details WHERE kind='access_budget_exhausted'").fetchone()[0]==1
    return current


def seed(app, env, data, instance, baseline_version="0.2.0"):
    process, log, info = launch_host_or_desktop(app, env, data, instance)
    try:
        assert request(info, "GET", "/api/v1/health")["version"] == baseline_version
        request(info, "GET", "/api/v1/platforms")
        run_command([app / "runtime/python/python.exe", ROOT / "tests/client_smoke.py", "--instance", instance, "--output", data.parent / "fixture-exports"], env, timeout=120)
    finally:
        stop_host_or_desktop(app, env, instance, process, log, info)
    run_command([app / "runtime/python/python.exe", "-c", SEED, data, app], {**env, "PYTHONPATH": str(app)},cwd=app)
    if baseline_version=='0.5.3':
        run_command([app/'runtime/python/python.exe','-c',EXHAUSTED_SEED,data],{**env,'PYTHONPATH':str(app)},cwd=app)
    if baseline_version in ('0.4.3','0.4.4'):
        with closing(sqlite3.connect(data/'workbench.sqlite3')) as con:
            assert con.execute('PRAGMA user_version').fetchone()[0]==4,'Old installer fixture must remain schema 4 before upgrade'
    return snapshot(data)


def verify_new(app, env, data, instance, before, version, *, exhausted=False):
    assert app_version(app) == version
    assert (app / "resources/app.asar").is_file()
    assert list((app / "resources/app.asar.unpacked").rglob("*.node"))
    assert not (app / "runtime/WebView2RuntimeInstallerX64.exe").exists()
    assert snapshot(data) == before, "Installer changed business data or saved session files"
    process, log, info = launch_host_or_desktop(app, env, data, instance, desktop=True)
    try:
        assert request(info, "GET", "/api/v1/health")["version"] == version
        assert request(info, "GET", "/api/v1/datasets")["items"]
        if exhausted:
            job=request(info,'GET','/api/v1/jobs/exhausted-upgrade')
            assert job['attempt']==0 and job['status_detail']['next_retry_at'] is None
            assert job['status_detail']['retry']['blocked_reason']=='manual_action'
            assert job['status_detail']['retry']['threshold']==999
            cap=request(info,'GET','/api/v1/platforms/dy/availability')['items'][0]
            assert cap['state']=='manual_action' and cap['checks']==3 and cap['cooldown_until'] is None
        run_command([app / "runtime/python/python.exe", "-c", VERIFY_SNAPSHOT, data], {**env, "PYTHONPATH": str(app)},cwd=app)
    finally:
        stop_host_or_desktop(app, env, instance, process, log, info, desktop=True)
    (preserved_exhausted if exhausted else preserved)(data,before)


def native_apply(install, package, env, directory):
    install = safe_root(install)
    return run_command([install / "Update.exe", "--silent", "--rootDir", install, "--log", directory / "update.log", "apply", "--norestart", "--package", package], env)


def uninstall(install, env, registration):
    install = safe_root(install)
    # Restore original shortcuts BEFORE uninstall: Velopack won't unpin or
    # delete a restored link whose target belongs to the real installation.
    registration.restore_shortcuts()
    if (install / "Update.exe").is_file():
        run_command([install / "Update.exe", "--silent", "--rootDir", install, "uninstall"], env)
        wait(lambda: not install.exists(), "Isolated uninstall did not finish delayed cleanup")


def execute(args):
    if os.name != "nt":
        raise RuntimeError("Windows is required")
    for name in ("old_setup", "new_setup", "new_package", "next_package"):
        value = getattr(args, name)
        if value is not None and not value.resolve().is_file():
            raise FileNotFoundError(value)
    directory = safe_root(args.directory or CACHE / ("Electron升级验收-" + uuid.uuid4().hex[:10]))
    if directory.exists():
        raise RuntimeError("Use a fresh upgrade-check directory")
    directory.mkdir(parents=True)
    registration = ShellRegistration(directory)
    checks = []
    try:
        for mode in ("full-installer", "native-package-update"):
            case = directory / mode
            case.mkdir()
            install = safe_root(case / "应用")
            data = case / "用户数据"
            instance = case / "instance.json"
            env = {**os.environ, "MEDIAWORKBENCH_DATA_DIR": str(data), "MEDIAWORKBENCH_INSTANCE": str(instance), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            env.pop("ELECTRON_RUN_AS_NODE", None)
            from workbench.desktop_guard import PREFIX, coordination_namespace
            assert coordination_namespace(env).startswith(PREFIX + ".Isolated.")
            try:
                run_command([args.old_setup, "--silent", "--installto", install, "--log", case / "old-setup.log"], env)
                app = install / "current"
                assert app_version(app) == args.baseline_version
                before = seed(app, env, data, instance, args.baseline_version)
                backup=case/'pre-upgrade-schema4'
                if args.baseline_version=='0.4.3' and tuple(map(int,args.version.split('.'))) >= (0,5,0):
                    shutil.copytree(data,backup)
                if mode == "full-installer":
                    run_command([args.new_setup, "--silent", "--installto", install, "--log", case / "new-setup.log"], env)
                else:
                    corrupted = case / "broken-full.nupkg"
                    with args.new_package.open("rb") as source:
                        corrupted.write_bytes(source.read(1024))
                    try:
                        native_apply(install, corrupted, env, case)
                    except subprocess.CalledProcessError:
                        pass
                    else:
                        raise AssertionError("Truncated package was unexpectedly accepted")
                    assert app_version(app) == args.baseline_version and snapshot(data) == before
                    checks.append("broken-package-keeps-old-installation-and-data")
                    native_apply(install, args.new_package.resolve(), env, case)
                verify_new(app, env, data, instance, before, args.version,exhausted=args.baseline_version=='0.5.3')
                checks.append(mode + "-" + args.baseline_version + "-to-" + args.version)
                if mode == "native-package-update" and args.next_package is not None:
                    migrated=snapshot(data)
                    native_apply(install, args.next_package.resolve(), env, case)
                    verify_new(app, env, data, instance, migrated, args.next_version)
                    checks.append("electron-native-update-to-private-" + args.next_version)
                if backup.exists():
                    # Preserve all new-version data before any restore. Use an actual
                    # uninstall/reinstall; the updater must never silently downgrade.
                    process,log,info=launch_host_or_desktop(app,env,data,instance)
                    try:
                        added=request(info,'POST','/api/v1/datasets',{'name':'new-version-preservation-fixture','records':[{'id':'after-upgrade','text':'keep in sealed new data'}]})
                    finally:
                        stop_host_or_desktop(app,env,instance,process,log,info)
                    sealed=case/'sealed-schema5-with-new-records'
                    shutil.copytree(data,sealed)
                    assert snapshot(sealed)['rows']['datasets']!=before['rows']['datasets']
                    uninstall(install,env,registration)
                    assert (data/'workbench.sqlite3').is_file(),'Uninstall deleted business data'
                    # Move only this test-owned directory, preserving it in addition to the seal.
                    archived=case/'data-before-rollback'
                    assert data.resolve().parent==case.resolve() and not archived.exists()
                    data.rename(archived)
                    shutil.copytree(backup,data)
                    run_command([args.old_setup,'--silent','--installto',install,'--log',case/'rollback-setup.log'],env)
                    assert app_version(app)=='0.4.3'
                    rejected=run_command([app/'runtime/python/python.exe','-c',
                        'from pathlib import Path;from workbench.database import Database;import sys;\ntry:Database(Path(sys.argv[1]))\nexcept RuntimeError as e:assert "newer" in str(e).lower();print("schema5-rejected")\nelse:raise SystemExit(2)',sealed],
                        {**env,'PYTHONPATH':str(app)},cwd=app)
                    process,log,info=launch_host_or_desktop(app,env,data,instance)
                    try:
                        assert request(info,'GET','/api/v1/health')['version']=='0.4.3'
                        run_command([app/'runtime/python/python.exe','-c',VERIFY_SNAPSHOT,data],{**env,'PYTHONPATH':str(app)},cwd=app)
                    finally:
                        stop_host_or_desktop(app,env,instance,process,log,info)
                    assert snapshot(data)==before
                    assert any('new-version-preservation-fixture' in row for row in snapshot(sealed)['rows']['datasets'])
                    checks.append(mode+'-uninstall-keep-data-rollback-to-0.4.3-preserves-new-data-separately')
            finally:
                uninstall(install, env, registration)
                registration.restore()
                registration.verify()
            preserved(data,before)
            checks.append(mode + "-uninstall-retains-business-data-and-login-fixture")
    finally:
        registration.restore()
        registration.verify()
    return {
        "ok": True, "version": args.version, "baseline_version": args.baseline_version,
        "next_version": args.next_version if args.next_package is not None else None,
        "cleanup_complete": True,
        'rollback_verified':args.baseline_version=='0.4.3' and tuple(map(int,args.version.split('.'))) >= (0,5,0),
        "checks": checks + ["original-shortcuts-and-registry-restored", "encrypted-synthetic-snapshot-readable", "browser-binding-path-retained", "three-analyses-fifteen-exports-retained"],
        "limitations": ["Synthetic login fixture only; not proof of actual platform authentication", "Current Windows machine, not clean VM", *([] if args.next_package else ["Private next-version package not supplied; Electron-to-next update remains unverified"])],
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-setup", type=Path)
    parser.add_argument("--baseline-version", default="0.2.0")
    parser.add_argument("--new-setup", type=Path, required=True)
    parser.add_argument("--new-package", type=Path, required=True)
    parser.add_argument("--version", default="0.3.0")
    parser.add_argument("--next-package", type=Path)
    parser.add_argument("--next-version")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--output", type=Path, default=CACHE / "electron-upgrade-results.json")
    args = parser.parse_args(argv)
    if args.old_setup is None:
        args.old_setup = ROOT.parent / f"outputs/Media Deep Researcher {args.baseline_version} Windows安装包/MediaWorkbench.Desktop-win-preview-Setup.exe"
    if args.next_version is None:
        prefix, patch = args.version.rsplit(".", 1)
        args.next_version = prefix + "." + str(int(patch) + 1)
    return args


def main():
    args = parse_args()
    try:
        result = execute(args)
    except BaseException as exc:
        result = {"ok": False, "error": str(exc), "exception": type(exc).__name__}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        raise
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
