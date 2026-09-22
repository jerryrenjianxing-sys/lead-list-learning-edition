import asyncio
import concurrent.futures
import json
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from workbench import auth
from workbench.app import make_app
from workbench.database import Database, SCHEMA
from workbench.models import CrawlRequest
from workbench.queue import Queue, create_job, control

PLATFORMS = ["xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu"]


def create(db, platform="xhs", crawler_type="login"):
    options = CrawlRequest(
        platform=platform, crawler_type=crawler_type, keywords="sample"
    ).model_dump()
    return db.write(None, "create", options, lambda con: create_job(con, options))


def test_migration_backs_up_original_and_is_idempotent(tmp_path):
    path = tmp_path / "workbench.sqlite3"
    with sqlite3.connect(path) as con:
        con.executescript(SCHEMA + "PRAGMA user_version=1;")
        con.execute("INSERT INTO settings VALUES('example','123')")
    db = Database(tmp_path)
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 1
        assert con.execute("SELECT value FROM settings").fetchone()[0] == "123"
    assert db.query("PRAGMA user_version")[0]["user_version"] == 4
    Database(tmp_path)
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1
    assert db.query("SELECT value FROM settings")[0]["value"] == "123"


def test_concurrent_login_and_resume_reuse_same_flow(tmp_path):
    db = Database(tmp_path)
    old = create(db)
    db.write(None, "stop", {}, lambda con: control(con, old["id"], "stop"))

    def submit(_):
        return create(Database(tmp_path))

    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        results = list(pool.map(submit, range(12)))
    assert len({r["id"] for r in results}) == 1
    resumed = db.write(
        None, "resume", {}, lambda con: control(con, old["id"], "resume")
    )
    assert resumed["id"] == results[0]["id"]
    assert len(db.query("SELECT * FROM datasets")) == 0
    assert db.query("SELECT COUNT(*) n FROM jobs WHERE state='queued'")[0]["n"] == 1


def test_reuse_collection_waiting_for_login_and_reject_stale_worker(tmp_path):
    db = Database(tmp_path)
    job = create(db, crawler_type="search")
    claimed = Queue(db)._claim()
    assert auth.update(db, job["id"], claimed["attempt"], "opening_login")
    auth.update(
        db,
        job["id"],
        claimed["attempt"],
        "opening_login",
        state="unknown",
        reason="unconfirmed",
    )
    assert auth.platform_view(db, "xhs")["login"]["message"] == ""
    assert auth.update(db, job["id"], claimed["attempt"], "waiting_scan")
    assert create(db)["id"] == job["id"]
    assert auth.update(db, job["id"], 1, "authenticated", state="authenticated")
    assert not auth.update(db, job["id"], 1, "waiting_scan")
    assert not auth.update(db, job["id"], 0, "failed", state="logged_out")
    db.write(None, "stop", {}, lambda con: control(con, job["id"], "stop"))
    assert not auth.update(db, job["id"], 1, "authenticated", state="authenticated")
    assert (
        auth.platform_view(Database(tmp_path), "xhs")["login_state"] == "authenticated"
    )


def test_cache_is_not_auth_and_login_options_are_isolated(tmp_path):
    (tmp_path / "browser_data" / "cdp_xhs_user_data_dir").mkdir(parents=True)
    app = make_app(tmp_path, run_queue=False)
    with TestClient(app) as c:
        c.get("/api/v1/session")
        p = c.get("/api/v1/platforms").json()["items"][0]
        assert p["saved_session"] and p["login_state"] == "not_checked"
        body = {
            "platform": "bili",
            "crawler_type": "login",
            "keywords": "ignore",
            "enrich_profiles": True,
            "enable_media": True,
            "headless": True,
        }
        first = c.post("/api/v1/jobs", json=body).json()
        assert c.post("/api/v1/jobs", json=body).json()["id"] == first["id"]
        job = c.get("/api/v1/jobs/" + first["id"]).json()
        assert job["dataset_id"] is None
        assert job["options"]["keywords"] == ""
        for key in ("enrich_profiles", "headless", "enable_comments"):
            assert job["options"][key] is False
        c.post("/api/v1/jobs/" + first["id"] + "/stop")
        p = next(
            p for p in c.get("/api/v1/platforms").json()["items"] if p["id"] == "bili"
        )
        assert p["login"]["phase"] == "cancelled" and not p["login"]["active"]


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("restored", [True, False])
def test_managed_login_requires_confirmation(platform, restored, monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIAWORKBENCH_JOB_ID", "test")
    import config

    monkeypatch.setattr(config, "CRAWLER_TYPE", "login")
    events = []
    monkeypatch.setattr(auth, "report", lambda phase, **kw: events.append((phase, kw)))
    monkeypatch.setattr(
        auth,
        "probe",
        AsyncMock(
            side_effect=["authenticated"]
            if restored
            else ["logged_out", "authenticated"]
        ),
    )
    page = SimpleNamespace(is_closed=lambda: False, goto=AsyncMock(), context=object())
    client = SimpleNamespace(update_cookies=AsyncMock())
    crawler = SimpleNamespace(
        context_page=page,
        browser_context=object(),
        cookie_urls=[],
        mobile_index_url="https://m.weibo.cn",
    )
    login = SimpleNamespace(begin=AsyncMock())
    assert asyncio.run(auth.managed_login(platform, crawler, client, lambda **_: login))
    assert events[-1] == (
        "authenticated",
        {"state": "authenticated", **({"restored": True} if restored else {})},
    )
    assert login.begin.await_count == int(not restored)


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("result", ["logged_out", "unknown", OSError("offline")])
def test_failed_confirmation_never_authenticates(platform, result, monkeypatch):
    monkeypatch.setenv("MEDIAWORKBENCH_JOB_ID", "test")
    import config

    monkeypatch.setattr(config, "CRAWLER_TYPE", "login")
    events = []
    monkeypatch.setattr(auth, "report", lambda phase, **kw: events.append((phase, kw)))
    monkeypatch.setattr(auth, "probe", AsyncMock(side_effect=["logged_out", result]))
    page = SimpleNamespace(is_closed=lambda: False, goto=AsyncMock(), context=object())
    client = SimpleNamespace(update_cookies=AsyncMock())
    crawler = SimpleNamespace(
        context_page=page,
        browser_context=object(),
        cookie_urls=[],
        mobile_index_url="https://m.weibo.cn",
    )
    with pytest.raises(auth.AuthError):
        asyncio.run(
            auth.managed_login(
                platform,
                crawler,
                client,
                lambda **_: SimpleNamespace(begin=AsyncMock()),
            )
        )
    assert all(kw.get("state") != "authenticated" for _, kw in events)
    assert events[-1][1]["reason"] == (
        "network_error"
        if isinstance(result, Exception)
        else "logged_out"
        if result == "logged_out"
        else "unconfirmed"
    )


@pytest.mark.parametrize(
    "platform,payload",
    [
        ("xhs", {"data": {"result": {"success": True}}}),
        ("bili", {"data": {"isLogin": True}}),
        ("wb", {"login": True}),
        ("ks", {"visionProfileUserList": {"result": 1}}),
        ("zhihu", {"uid": "123", "name": "sample"}),
        ("dy", {"status_code": 0, "user": {"uid": "123"}}),
        ("tieba", {"is_login": 1}),
    ],
)
def test_server_probes_positive_and_missing(platform, payload):
    async def check(data):
        client = SimpleNamespace(
            query_self=AsyncMock(return_value=data),
            request=AsyncMock(return_value=data),
            post=AsyncMock(return_value=data),
            get_current_user_info=AsyncMock(return_value=data),
            get=AsyncMock(return_value=data),
            _fetch_json_by_browser=AsyncMock(return_value=data),
            _host="https://example.invalid",
            headers={},
            graphql={},
        )
        page = SimpleNamespace(
            evaluate=AsyncMock(return_value={"status": 200, "body": data})
        )
        return await auth.probe(platform, client, page)

    assert asyncio.run(check(payload)) == "authenticated"
    assert asyncio.run(check({})) == "unknown"


@pytest.mark.parametrize(
    "closed,reason", [(True, "browser_closed"), (False, "scan_timeout")]
)
def test_closed_window_and_timeout_are_distinct(closed, reason):
    async def check():
        with pytest.raises(auth.AuthError) as exc:
            await auth.watched(
                asyncio.sleep(10),
                SimpleNamespace(is_closed=lambda: closed),
                0.01,
                "scan_timeout",
            )
        assert exc.value.reason == reason

    asyncio.run(check())


def test_douyin_explicit_logout_is_not_a_network_error():
    client = SimpleNamespace(
        get=AsyncMock(return_value={"status_code": 8, "status_msg": "用户未登录"})
    )
    assert asyncio.run(auth.probe("dy", client, None)) == "logged_out"


def test_zero_exit_and_log_messages_do_not_prove_login(tmp_path):
    db = Database(tmp_path)
    job = create(db)
    queue = Queue(db, lambda _: [sys.executable, "-c", "print('Login successful')"])
    claimed = queue._claim()
    queue._run(claimed)
    assert db.one("jobs", job["id"])["state"] == "failed"
    assert auth.platform_view(db, "xhs")["login_state"] == "not_checked"


def test_collection_errors_still_produce_failure(tmp_path):
    db = Database(tmp_path)
    job = create(db, crawler_type="search")
    queue = Queue(
        db, lambda _: [sys.executable, "-c", "print('ERROR collection failed')"]
    )
    queue._run(queue._claim())
    assert db.one("jobs", job["id"])["state"] == "failed"


def test_verified_login_survives_expected_unauthenticated_logs(tmp_path):
    db = Database(tmp_path)
    job = create(db)
    script = (
        "from workbench.database import Database; from workbench.auth import update; from pathlib import Path; print('ERROR old session was expired'); update(Database(Path("
        + repr(str(tmp_path))
        + ")),"
        + repr(job["id"])
        + ",1,'authenticated',state='authenticated')"
    )
    queue = Queue(db, lambda _: [sys.executable, "-c", script])
    queue._run(queue._claim())
    assert db.one("jobs", job["id"])["state"] == "completed"
    assert (
        auth.platform_view(Database(tmp_path), "xhs")["login_state"] == "authenticated"
    )


def test_host_recovery_preserves_verified_auth_but_closes_progress(tmp_path):
    db = Database(tmp_path)
    job = create(db)
    queue = Queue(db)
    queue._claim()
    auth.update(db, job["id"], 1, "verifying", state="unknown")
    queue.start()
    queue.close()
    p = auth.platform_view(db, "xhs")
    assert p["login"]["phase"] == "interrupted" and not p["login"]["active"]


def test_retry_tracks_new_attempt_and_cannot_accept_old_confirmation(tmp_path):
    db = Database(tmp_path)
    job = create(db)
    queue = Queue(db)
    queue._claim()
    auth.update(db, job["id"], 1, "failed", state="unknown", reason="scan_timeout")
    db.execute("UPDATE jobs SET state='failed' WHERE id=?", (job["id"],))
    db.write(None, "retry", {}, lambda con: control(con, job["id"], "resume"))
    assert auth.platform_view(db, "xhs")["login"]["phase"] == "queued"
    second = queue._claim()
    assert second["attempt"] == 2
    assert not auth.update(db, job["id"], 1, "authenticated", state="authenticated")
    assert auth.update(
        db, job["id"], 2, "authenticated", state="authenticated", restored=True
    )
    history = db.query(
        "SELECT * FROM login_progress WHERE job_id=? ORDER BY attempt", (job["id"],)
    )
    assert [p["phase"] for p in history] == ["queued", "failed", "authenticated"]
    assert auth.platform_view(db, "xhs")["login_state"] == "authenticated"
