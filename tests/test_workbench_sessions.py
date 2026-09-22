import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from workbench import auth
from workbench.app import make_app
from workbench.database import Database, Conflict, SCHEMA, AUTH_SCHEMA
from workbench.models import CrawlRequest
from workbench.queue import Queue, create_job, control
from workbench.session_store import SessionStore
from workbench.sessions import SessionManager


def create(db, platform="dy", mode="search"):
    options = CrawlRequest(
        platform=platform, crawler_type=mode, keywords="test"
    ).model_dump()
    return db.write(None, "new", options, lambda con: create_job(con, options))


def test_v3_migration_backs_up_and_keeps_auth(tmp_path):
    with sqlite3.connect(tmp_path / "workbench.sqlite3") as con:
        con.executescript(SCHEMA + AUTH_SCHEMA + "PRAGMA user_version=3;")
        con.execute(
            "INSERT INTO platform_auth VALUES('dy','authenticated','yesterday','yesterday','old',1,'')"
        )
    db = Database(tmp_path)
    (backup,) = (tmp_path / "backups").glob("*.sqlite3")
    with sqlite3.connect(backup) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 3
    assert db.query("PRAGMA user_version")[0]["user_version"] == 4
    assert SessionStore(db).view("dy")["can_select"]
    Database(tmp_path)
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_snapshot_encrypted_atomic_and_revocation(tmp_path, monkeypatch):
    from workbench import session_store

    db = Database(tmp_path)
    store = SessionStore(db)
    store.bind("dy", Path("browser.exe"))
    snapshot = {
        "cookies": [{"name": "sample", "value": "synthetic-secret"}],
        "origins": [],
    }
    assert store.save("dy", snapshot, {"id": "one", "name": "Sample"}, new_auth=True)
    original = store.path("dy").read_bytes()
    assert b"synthetic-secret" not in original
    assert store.load("dy") == snapshot

    def broken(_):
        raise OSError("disk unavailable")

    monkeypatch.setattr(session_store, "seal", broken)
    assert not store.save("dy", {"cookies": []})
    assert store.path("dy").read_bytes() == original
    assert store.get("dy")["save_state"] == "failed"
    store.check("dy", "network_error")
    assert store.load("dy") == snapshot
    store.check("dy", "logged_out")
    assert store.load("dy") is None


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_resume_only_matching_account_once_and_not_stopped(tmp_path):
    db = Database(tmp_path)
    store = SessionStore(db)
    store.bind("dy", "browser.exe")
    identity = {"id": "account-a", "name": "A"}
    assert store.save("dy", {}, identity, new_auth=True)
    jobs = [create(db) for _ in range(3)]
    for j in jobs:
        store.pause(j["id"], "dy", "logged_out")
        db.execute("UPDATE jobs SET state='needs_login' WHERE id=?", (j["id"],))
    db.write(None, "stop", {}, lambda c: control(c, jobs[1]["id"], "stop"))
    db.execute(
        "UPDATE auth_pauses SET account_id=NULL WHERE job_id=?", (jobs[2]["id"],)
    )
    assert store.save("dy", {}, {"id": "account-b"}, new_auth=True)
    assert store.resume_matching("dy") == []
    assert store.save("dy", {}, identity, new_auth=True)
    assert store.resume_matching("dy") == [jobs[0]["id"]]
    assert store.resume_matching("dy") == []
    assert db.one("jobs", jobs[1]["id"])["state"] == "needs_login"
    assert store.view("dy")["account_confirmation_jobs"] == [jobs[2]["id"]]


@pytest.mark.parametrize(
    "platform", ["xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu"]
)
@pytest.mark.parametrize(
    "result", ["unknown", "network_error", "logged_out", "challenge"]
)
def test_collection_never_opens_login_on_auth_failure(platform, result, monkeypatch):
    import config

    monkeypatch.setenv("MEDIAWORKBENCH_JOB_ID", "unit")
    monkeypatch.delenv("MEDIAWORKBENCH_SESSION", raising=False)
    monkeypatch.setattr(config, "CRAWLER_TYPE", "search")
    monkeypatch.setattr(auth, "checked_probe", AsyncMock(return_value=result))
    events = []
    monkeypatch.setattr(auth, "report", lambda phase, **kw: events.append((phase, kw)))
    factory = AsyncMock()
    crawler = SimpleNamespace(context_page=SimpleNamespace(is_closed=lambda: False))
    with pytest.raises(auth.AuthError):
        asyncio.run(auth.managed_login(platform, crawler, object(), factory))
    factory.assert_not_called()
    assert "opening_login" not in [e[0] for e in events]


def test_check_endpoints_dedup_forget_blocks_active_and_preserves_data(tmp_path):
    app = make_app(tmp_path, run_queue=False)
    with TestClient(app) as c:
        c.get("/api/v1/session")
        job = c.post("/api/v1/platforms/dy/session/check").json()
        assert c.post("/api/v1/platforms/dy/session/login").json()["id"] == job["id"]
        assert c.post("/api/v1/platforms/dy/session/forget").status_code == 409
        c.post(f"/api/v1/jobs/{job['id']}/stop")
        profile = app.state.sessions.store.profile("dy")
        profile.mkdir(parents=True)
        (profile / "synthetic").write_text("test")
        demo = c.post("/api/v1/demo").json()
        assert c.post("/api/v1/platforms/dy/session/forget").status_code == 200
        assert not profile.exists()
        assert c.get("/api/v1/datasets").json()["items"][0]["id"] == demo["id"]
        p = next(
            p for p in c.get("/api/v1/platforms").json()["items"] if p["id"] == "dy"
        )
        assert not p["can_select"] and not p["account"]["name"]
        assert "profile" not in p and "browser" not in p


def test_login_priority_does_not_preempt_active(tmp_path):
    db = Database(tmp_path)
    q = Queue(db)
    active = create(db)
    assert q._claim()["id"] == active["id"]
    normal = create(db, "xhs")
    login = create(db, "bili", "login")
    assert q._claim()["id"] == login["id"]
    assert db.one("jobs", active["id"])["state"] == "running"


@pytest.mark.skipif(os.name != "nt", reason="Windows browser lifecycle and DPAPI")
@pytest.mark.parametrize("damage", [False, True])
def test_real_browser_reuse_snapshot_and_worker_disconnect(
    tmp_path, monkeypatch, damage
):
    from playwright.sync_api import sync_playwright
    from playwright.async_api import async_playwright
    from tools.cdp_browser import CDPBrowserManager

    with sync_playwright() as pw:
        executable = pw.chromium.executable_path
    db = Database(tmp_path)
    db.execute(
        "INSERT INTO settings VALUES('browser_path',?)", (json.dumps(executable),)
    )
    manager = SessionManager(db)
    q = Queue(db)
    try:
        first = create(db)
        claimed = q._claim()
        lease = manager.call("acquire", job=claimed)
        pid = manager.process.pid
        monkeypatch.setenv("MEDIAWORKBENCH_SESSION", json.dumps(lease))

        async def work():
            async with async_playwright() as pw:
                cdp = CDPBrowserManager()
                context = await cdp.launch_and_connect(pw)
                page = await context.new_page()
                await page.route(
                    "**/*",
                    lambda route: route.fulfill(
                        status=200,
                        body="<html>Local test</html>",
                        content_type="text/html",
                    ),
                )
                await page.goto("http://127.0.0.1:19876/")
                await context.add_cookies(
                    [
                        {
                            "name": "session-only",
                            "value": "synthetic",
                            "url": "http://127.0.0.1:19876/",
                        }
                    ]
                )
                await page.evaluate("localStorage.setItem('sample','synthetic')")
                assert manager.call(
                    "command",
                    lease=lease["lease"],
                    action="save",
                    identity={"id": "test-account"},
                    new_auth=True,
                )["saved"]
                await cdp.cleanup()

        asyncio.run(work())
        assert manager.process.poll() is None
        db.execute("UPDATE jobs SET state='completed' WHERE id=?", (first["id"],))
        manager.call("release", lease=lease["lease"])
        second = create(db)
        new_lease = manager.call("acquire", job=q._claim())
        assert manager.process.pid == pid
        with pytest.raises(Conflict):
            manager.call("command", lease=lease["lease"], action="show")
        db.execute("UPDATE jobs SET state='completed' WHERE id=?", (second["id"],))
        manager.call("release", lease=new_lease["lease"])
        manager.last_used = time.monotonic() - 301
        manager.call("idle")
        assert manager.browser is None
        if damage:
            (manager.store.profile("dy") / "Local State").write_text("not valid json")
        third = create(db)
        third_lease = manager.call("acquire", job=q._claim())

        async def verify():
            async with async_playwright() as pw:
                b = await pw.chromium.connect_over_cdp(third_lease["endpoint"])
                assert any(
                    c["name"] == "session-only" for c in await b.contexts[0].cookies()
                )
                if damage:
                    p = await b.contexts[0].new_page()
                    await p.route("**/*", lambda r: r.fulfill(body="<html>test</html>"))
                    await p.goto("http://127.0.0.1:19876/")
                    assert (
                        await p.evaluate("localStorage.getItem('sample')")
                        == "synthetic"
                    )

        asyncio.run(verify())
        db.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (third["id"],))
        manager.call("release", lease=third_lease["lease"])
    finally:
        manager.close()


def test_stop_during_snapshot_cannot_replace_good_copy(tmp_path):
    db = Database(tmp_path)
    job = create(db)
    Queue(db)._claim()
    manager = SessionManager(db)
    manager.platform = "dy"
    manager.store.get("dy")
    manager.lease = {
        "lease": "test",
        "job_id": job["id"],
        "attempt": 1,
        "generation": 0,
    }
    manager.verified = True

    async def snapshot(**kwargs):
        db.execute("UPDATE jobs SET state='stopping' WHERE id=?", (job["id"],))
        return {"cookies": [], "origins": []}

    manager.context = SimpleNamespace(storage_state=snapshot)
    assert not asyncio.run(manager._save())
    assert not manager.store.path("dy").exists()


def test_network_retry_budget_does_not_multiply_platform_retries(monkeypatch):
    from tenacity import retry, stop_after_attempt

    calls = []

    class Client:
        _host = "https://example.invalid"
        headers = {}
        update_cookies = AsyncMock()

        @retry(stop=stop_after_attempt(3))
        async def request(self, **kwargs):
            calls.append(True)
            raise OSError("offline")

    async def no_delay(_):
        return None

    monkeypatch.setattr(auth.asyncio, "sleep", no_delay)
    page = SimpleNamespace(is_closed=lambda: False, context=object())
    assert asyncio.run(auth.checked_probe("wb", Client(), page)) == "network_error"
    assert len(calls) == 3


def test_network_error_retains_previous_auth_and_selection(tmp_path):
    db = Database(tmp_path)
    job = create(db)
    Queue(db)._claim()
    auth.update(db, job["id"], 1, "authenticated", state="authenticated")
    store = SessionStore(db)
    store.check("dy", "network_error")
    auth.update(db, job["id"], 1, "failed", state="unknown", reason="network_error")
    assert store.view("dy")["can_select"]
    assert store.view("dy")["last_check"]["state"] == "network_error"
    store.check("dy", "logged_out")
    assert not store.view("dy")["can_select"]


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_snapshot_database_failure_and_crash_preserve_previous_generation(
    tmp_path, monkeypatch
):
    db = Database(tmp_path)
    store = SessionStore(db)
    store.bind("dy", "browser.exe")
    identity = {"id": "account-a"}
    first = {"cookies": [], "origins": [], "test": "first"}
    second = {"cookies": [], "origins": [], "test": "second"}
    assert store.save("dy", first, identity, new_auth=True)
    generation = store.get("dy")["generation"]
    execute = db.execute

    def fail_commit(sql, args=()):
        if "SET generation=?" in sql:
            raise sqlite3.OperationalError("simulated interrupted metadata commit")
        return execute(sql, args)

    monkeypatch.setattr(db, "execute", fail_commit)
    assert not store.save("dy", second, identity, new_auth=True)
    assert store.load("dy") == first
    monkeypatch.setattr(db, "execute", execute)
    assert store.save("dy", second, identity, new_auth=True)
    # Simulate process death after the file write but before metadata commit.
    db.execute(
        "UPDATE platform_sessions SET generation=? WHERE platform='dy'", (generation,)
    )
    assert store.load("dy") == first
    store.check("dy", "logged_out")
    assert store.load("dy") is None


@pytest.mark.parametrize(
    "method,reason",
    [("refresh_xhs_session", "logged_out"), ("recover_xhs_captcha", "challenge")],
)
def test_xhs_midrun_auth_releases_queue_without_qr(method, reason, monkeypatch):
    from media_platform.xhs.core import XiaoHongShuCrawler

    monkeypatch.setenv("MEDIAWORKBENCH_SESSION", "synthetic")
    monkeypatch.setattr(auth, "session_check", AsyncMock())
    monkeypatch.setattr("media_platform.xhs.core.report", lambda *a, **kw: None)
    with pytest.raises(auth.AuthError) as exc:
        asyncio.run(getattr(XiaoHongShuCrawler(), method)())
    assert exc.value.reason == reason


def test_unknown_check_cannot_open_qr_even_when_user_checks(monkeypatch):
    import config

    monkeypatch.setenv("MEDIAWORKBENCH_JOB_ID", "unit")
    monkeypatch.setattr(config, "CRAWLER_TYPE", "login")
    monkeypatch.setattr(config, "WORKBENCH_SESSION_ACTION", "check", raising=False)
    monkeypatch.setattr(auth, "checked_probe", AsyncMock(return_value="unknown"))
    monkeypatch.setattr(auth, "session_check", AsyncMock())
    monkeypatch.setattr(auth, "report", lambda *a, **kw: None)
    factory = AsyncMock()
    crawler = SimpleNamespace(context_page=SimpleNamespace(is_closed=lambda: False))
    with pytest.raises(auth.AuthError):
        asyncio.run(auth.managed_login("dy", crawler, object(), factory))
    factory.assert_not_called()
