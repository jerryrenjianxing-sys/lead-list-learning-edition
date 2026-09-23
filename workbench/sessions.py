"""One host-owned browser with attempt-scoped worker leases and encrypted recovery."""

from __future__ import annotations
import asyncio
import ctypes
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
import psutil
from .database import Conflict, now
from .session_store import SessionStore

IDLE_SECONDS = 300


def window_visibility(pid, show):
    if os.name != "nt":
        return
    user = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(hwnd, _):
        owner = ctypes.c_ulong()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            user.ShowWindow(
                hwnd, 9 if show else 7
            )  # Restore, or minimize without activation.
            if show:
                user.SetForegroundWindow(hwnd)
        return True

    user.EnumWindows(callback_type(visit), 0)


class SessionManager:
    def __init__(self, db):
        self.db = db
        self.store = SessionStore(db)
        self.url = ""
        self.token = ""
        self.thread = None
        self.start_lock = threading.Lock()
        self.ready = threading.Event()
        self.browser = self.context = self.process = self.pw = None
        self.keepalive_page = None
        self.platform = self.lease = None
        self.identity = {}
        self.endpoint = ""
        self.last_used = time.monotonic()
        self.profile_lock = None
        self.verified = False
        self.pending_auth_generation = False

    def _start(self):
        with self.start_lock:
            if not self.thread:

                def run():
                    self.loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(self.loop)
                    self.lock = asyncio.Lock()
                    self.ready.set()
                    self.loop.run_forever()
                    self.loop.close()

                self.thread = threading.Thread(
                    target=run, name="workbench-browser", daemon=True
                )
                self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError("浏览器管理器启动超时，请重试。")

    def call(self, operation, **kwargs):
        self._start()

        async def serialized():
            async with self.lock:
                return await getattr(self, "_" + operation)(**kwargs)

        return asyncio.run_coroutine_threadsafe(serialized(), self.loop).result(
            timeout=100
        )

    def close(self):
        if not self.thread:
            return
        try:
            self.call("shutdown")
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
            self.thread = None
            self.ready.clear()

    def _validate(self, lease, *, ending=False):
        if not self.lease or not secrets.compare_digest(lease, self.lease["lease"]):
            raise Conflict("This browser lease has ended")
        job = self.db.one("jobs", self.lease["job_id"])
        if self.store.get(job["platform"])["generation"] != self.lease["generation"]:
            raise Conflict("The account session has changed")
        if job["attempt"] != self.lease["attempt"] or (
            not ending and job["state"] != "running"
        ):
            raise Conflict("This task attempt no longer owns the browser")
        return job

    async def _acquire(self, job):
        if self.lease:
            raise Conflict("Browser is in use")
        current = self.db.one("jobs", job["id"])
        if current["state"] != "running" or current["attempt"] != job["attempt"]:
            raise Conflict("Task was stopped before browser startup")
        if (
            self.platform != job["platform"]
            or not self.browser
            or not self.browser.is_connected()
        ):
            await self._close_browser()
            for attempt in range(2):
                try:
                    await self._launch(job["platform"])
                    break
                except Conflict:
                    raise
                except Exception:
                    await self._close_browser()
                    if attempt:
                        raise
                    await asyncio.sleep(1)
        options = job["options"]
        self.lease = {
            "lease": secrets.token_urlsafe(32),
            "job_id": job["id"],
            "attempt": job["attempt"],
            "generation": self.store.get(job["platform"])["generation"],
            "endpoint": self.endpoint,
        }
        self.last_used = time.monotonic()
        try:
            self._validate(self.lease["lease"])
        except Conflict:
            self.lease = None
            raise
        if options.get("session_action") == "open":
            window_visibility(self.process.pid, True)
        return dict(self.lease)

    async def _launch(self, platform):
        from playwright.async_api import async_playwright
        from tools.browser_launcher import BrowserLauncher

        if not self.pw:
            self.pw = await async_playwright().start()
        row = self.store.get(platform)
        settings = {
            r["key"]: json.loads(r["value"])
            for r in self.db.query("SELECT * FROM settings")
        }
        browser = row["browser"] or settings.get("browser_path")
        if not browser:
            paths = BrowserLauncher().detect_browser_paths()
            browser = paths[0] if paths else self.pw.chromium.executable_path
        if not Path(browser).is_file():
            raise ValueError("已保存账号使用的浏览器无法启动，请恢复该浏览器后重试。")
        row = self.store.bind(platform, browser)
        profile = self.store.profile(platform)
        missing = not (profile / "Default").is_dir()
        profile.mkdir(parents=True, exist_ok=True)
        self.platform = platform
        self.identity = {"id": row["account_id"], "name": row["account_name"]}
        self.verified = False
        self.profile_lock = (profile / ".workbench.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.profile_lock.seek(0)
                self.profile_lock.write(b"0")
                self.profile_lock.flush()
                self.profile_lock.seek(0)
                msvcrt.locking(self.profile_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.profile_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.profile_lock.close()
            self.profile_lock = None
            raise Conflict("该账号的浏览器正在被另一个程序使用，请先关闭它。")
        # Reap only a browser previously owned by this manager, with PID creation-time protection.
        marker = self.db.root / "sessions" / "browser-process.json"
        if marker.exists():
            try:
                old = json.loads(marker.read_text())
                proc = psutil.Process(old["pid"])
                if (
                    abs(proc.create_time() - old["created"]) < 0.1
                    and "--user-data-dir=" + old["profile"] in proc.cmdline()
                ):
                    proc.terminate()
                    proc.wait(timeout=5)
            except (psutil.Error, ValueError, KeyError):
                pass
        snapshot = self.store.load(platform)
        if self._profile_damaged(profile):
            if not snapshot:
                self.profile_lock.close()
                self.profile_lock = None
                raise RuntimeError(
                    "浏览器档案损坏且没有可用的登录备份。原档案已保留，请修复或忘记此账号后重新登录。"
                )
            # Preserve evidence of confirmed corruption. Never reset a profile merely
            # because startup or a network request failed.
            recovery = (
                self.db.root / "sessions" / "recovery" / platform / secrets.token_hex(8)
            )
            recovery.mkdir(parents=True)
            for child in profile.iterdir():
                if child.name != ".workbench.lock":
                    shutil.move(str(child), str(recovery / child.name))
            missing = True
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.endpoint = f"http://127.0.0.1:{port}"
        args = [
            browser,
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--disable-blink-features=AutomationControlled",
            "--start-minimized",
            "about:blank",
        ]
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 7
            kwargs.update(
                startupinfo=startup, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )
        try:
            self.process = subprocess.Popen(args, **kwargs)
            marker.parent.mkdir(exist_ok=True)
            marker.write_text(
                json.dumps(
                    {
                        "pid": self.process.pid,
                        "created": psutil.Process(self.process.pid).create_time(),
                        "profile": str(profile),
                    }
                ),
                encoding="utf-8",
            )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError("登录浏览器退出，已保留原账号档案，请重试。")
                try:
                    self.browser = await self.pw.chromium.connect_over_cdp(
                        self.endpoint, timeout=1000
                    )
                    break
                except Exception:
                    await asyncio.sleep(0.3)
            if not self.browser:
                raise RuntimeError("登录浏览器启动超时，已保留原账号档案，请重试。")
            self.context = self.browser.contexts[0]
            # CDP can connect before Chromium exposes its initial about:blank tab.
            # Own a known tab before a worker attaches, so closing worker pages
            # cannot close the last browser window and end the shared session.
            self.keepalive_page = await self.context.new_page()
            if snapshot:
                if missing:
                    await self.context.set_storage_state(snapshot)
                else:
                    # Persistent profiles may omit session cookies after a clean exit.
                    # Restore only missing session cookies; never overwrite newer live values.
                    cookies = await self.context.cookies()
                    key = lambda c: (
                        c["name"],
                        c["domain"],
                        c["path"],
                        c.get("partitionKey"),
                    )
                    present = {key(c) for c in cookies}
                    absent = [
                        c
                        for c in snapshot.get("cookies", [])
                        if c.get("expires", -1) == -1 and key(c) not in present
                    ]
                    if absent:
                        await self.context.add_cookies(absent)
            window_visibility(self.process.pid, False)
        except Exception:
            await self._close_browser()
            raise

    @staticmethod
    def _profile_damaged(profile):
        local_state = profile / "Local State"
        if local_state.exists():
            try:
                json.loads(local_state.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError):
                return True
        for path in (profile / "Default/Network/Cookies", profile / "Default/Cookies"):
            if path.is_file():
                try:
                    with sqlite3.connect(
                        path.as_uri() + "?mode=ro", uri=True, timeout=1
                    ) as con:
                        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                            return True
                except sqlite3.DatabaseError as exc:
                    if "malformed" in str(exc) or "not a database" in str(exc):
                        return True
                    # A locked file is not corruption.
        return False

    async def _command(self, lease, action, identity=None, new_auth=False, state=None):
        job = self._validate(lease)
        if action == "show":
            window_visibility(self.process.pid, True)
            return {"shown": True}
        if action == "check":
            if state not in (
                "authenticated",
                "logged_out",
                "challenge",
                "unknown",
                "network_error",
            ):
                raise ValueError("Unknown authentication state")
            self.store.check(self.platform, state)
            if state != "authenticated":
                self.verified = False
            return {"state": state}
        if action == "save":
            previous = self.store.get(self.platform)
            changed = bool(
                previous["account_id"]
                and identity
                and identity.get("id")
                and previous["account_id"] != identity["id"]
                and job["dataset_id"]
                and self.db.query(
                    "SELECT 1 FROM records WHERE dataset_id=? LIMIT 1",
                    (job["dataset_id"],),
                )
            )
            if changed:
                self.store.pause(job["id"], self.platform, "account_changed")
            self.identity = identity or {}
            self.verified = True
            self.pending_auth_generation |= new_auth
            self.store.check(self.platform, "authenticated")
            saved = await self._save(new_auth=new_auth)
            return {"saved": saved, "account_changed": changed}
        raise ValueError("Unknown session operation")

    async def _save(self, *, new_auth=False, ending=False):
        if not self.context or not self.verified:
            return False
        try:
            snapshot = await asyncio.wait_for(
                self.context.storage_state(indexed_db=True), timeout=20
            )
            if self.lease:
                job = self._validate(self.lease["lease"], ending=ending)
                if ending and job["state"] != "completed":
                    return False
            saved = self.store.save(
                self.platform,
                snapshot,
                self.identity,
                new_auth=new_auth or self.pending_auth_generation,
            )
            if saved:
                self.pending_auth_generation = False
                if self.lease:
                    self.lease["generation"] = self.store.get(self.platform)[
                        "generation"
                    ]
            return saved
        except Conflict:
            return False
        except Exception:
            self.db.execute(
                "UPDATE platform_sessions SET save_state='failed',last_error='登录有效，但保存未完成，请重试保存。' WHERE platform=?",
                (self.platform,),
            )
            return False

    async def _release(self, lease):
        job = self._validate(lease, ending=True)
        if job["state"] == "completed" and self.verified:
            await self._save(ending=True)
        if self.context and json.loads(job["options"]).get("session_action") != "open":
            # Leave a blank page alive; never close the persistent context from a worker.
            try:
                if not self.keepalive_page or self.keepalive_page.is_closed():
                    self.keepalive_page = await self.context.new_page()
                for page in list(self.context.pages):
                    if page != self.keepalive_page:
                        await page.close()
                window_visibility(self.process.pid, False)
            except Exception:
                pass
        self.lease = None
        self.last_used = time.monotonic()
        if job["state"] not in ("completed",):
            self.verified = (
                False  # Failed/cancelled attempts cannot overwrite a good snapshot.
            )
        self.store.resume_matching(job["platform"])

    async def _idle(self):
        if (
            not self.lease
            and self.browser
            and time.monotonic() - self.last_used >= IDLE_SECONDS
        ):
            await self._close_browser()

    async def _close_browser(self):
        await self._save()
        if self.browser:
            try:
                session = await self.browser.new_browser_cdp_session()
                await session.send("Browser.close")
            except Exception:
                pass
        if self.process:
            try:
                await asyncio.to_thread(self.process.wait, 8)
            except subprocess.TimeoutExpired:
                from .queue import kill_tree

                kill_tree(self.process.pid)
        self.context = self.browser = self.process = None
        self.keepalive_page = None
        self.platform = None
        self.verified = False
        self.pending_auth_generation = False
        if self.profile_lock:
            self.profile_lock.close()
            self.profile_lock = None

    async def _shutdown(self):
        if self.lease:
            self.verified = False
        self.lease = None
        await self._close_browser()
        if self.pw:
            await self.pw.stop()
            self.pw = None

    async def _forget(self, platform):
        profile = self.store.profile(platform)
        snapshot = self.store.path(platform)
        with self.db.lock, self.db.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute(
                "SELECT 1 FROM jobs WHERE platform=? AND state IN ('queued','running','stopping')",
                (platform,),
            ).fetchone():
                raise Conflict("该平台仍有任务，请先停止任务再忘记账号。")
            if self.platform == platform:
                self.verified = False
                await self._close_browser()
            base = (self.db.root / "browser_data").resolve()
            # Check final absolute paths immediately before deleting only this app's profile.
            if profile.parent != base or profile == base or profile.is_symlink():
                raise ValueError("Refusing to remove an unrelated browser profile")
            profiles = {
                profile,
                base / f"cdp_{platform}_user_data_dir",
                base / f"{platform}_user_data_dir",
            }
            arguments = {"--user-data-dir=" + str(p) for p in profiles}
            for process in psutil.process_iter(["cmdline"]):
                if arguments.intersection(process.info.get("cmdline") or []):
                    raise Conflict("该账号的浏览器仍在使用，请关闭它后再忘记账号。")
            for candidate in profiles:
                if (
                    candidate.resolve() != candidate.absolute()
                    or candidate.parent != base
                ):
                    raise ValueError("Refusing to remove a linked browser profile")
            recovery = self.db.root / "sessions" / "recovery" / platform
            recovery_base = (self.db.root / "sessions" / "recovery").resolve()
            if (
                recovery.resolve() != recovery.absolute()
                or recovery.resolve().parent != recovery_base
            ):
                raise ValueError("Refusing to remove unrelated recovery files")
            for candidate in profiles:
                if candidate.exists():
                    shutil.rmtree(candidate)
            if recovery.exists():
                shutil.rmtree(recovery)
            snapshot.unlink(missing_ok=True)
            snapshot.with_suffix(".tmp").unlink(missing_ok=True)
            snapshot.with_suffix(".previous.dpapi").unlink(missing_ok=True)
            snapshot.with_suffix(".previous.tmp").unlink(missing_ok=True)
            con.execute("DELETE FROM platform_auth WHERE platform=?", (platform,))
            con.execute(
                "UPDATE auth_pauses SET enabled=0 WHERE platform=?", (platform,)
            )
            con.execute(
                "UPDATE platform_sessions SET generation=generation+1,account_id=NULL,account_name=NULL,saved_at=NULL,save_state='not_saved',check_state='not_checked',snapshot_valid=0,last_error='' WHERE platform=?",
                (platform,),
            )
            con.commit()
        return {"forgotten": True}
