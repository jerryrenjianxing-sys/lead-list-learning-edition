import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from workbench.app import make_app
from workbench.database import Database
from workbench.auth import update
from workbench.queue import Queue


def test_clear_preserves_data_auth_logs_and_is_reversible(tmp_path):
    app = make_app(tmp_path, run_queue=False)
    with TestClient(app) as c:
        c.get("/api/v1/session")
        job = c.post(
            "/api/v1/jobs", json={"platform": "dy", "keywords": "sample"}
        ).json()
        db = app.state.db
        Queue(db)._claim()
        update(db, job["id"], 1, "authenticated", state="authenticated")
        db.event(job["id"], "retained diagnostic")
        c.post(
            "/api/v1/datasets/" + job["dataset_id"] + "/records",
            json={"records": [{"id": "one", "text": "evidence"}]},
        )
        db.execute("UPDATE jobs SET state='failed' WHERE id=?", (job["id"],))
        active = c.post(
            "/api/v1/jobs", json={"platform": "xhs", "keywords": "still queued"}
        ).json()
        key = {"Idempotency-Key": "cleanup"}
        cleared = c.post("/api/v1/jobs/clear-history", json={}, headers=key).json()
        assert cleared["ids"] == [job["id"]] and cleared["data_preserved"]
        assert (
            c.post("/api/v1/jobs/clear-history", json={}, headers=key).json() == cleared
        )
        visible = c.get("/api/v1/jobs").json()
        assert visible["archived_count"] == 1 and [
            j["id"] for j in visible["items"]
        ] == [active["id"]]
        assert len(c.get("/api/v1/jobs?include_archived=true").json()["items"]) == 2
        assert (
            c.get("/api/v1/datasets/" + job["dataset_id"] + "/records").json()["items"][
                0
            ]["payload"]["text"]
            == "evidence"
        )
        assert (
            c.get("/api/v1/jobs/" + job["id"] + "/events").json()["items"][0]["message"]
            == "retained diagnostic"
        )
        assert (
            next(
                p for p in c.get("/api/v1/platforms").json()["items"] if p["id"] == "dy"
            )["login_state"]
            == "authenticated"
        )
        assert c.post("/api/v1/jobs/restore-history", json={}).json()["count"] == 1
        assert len(c.get("/api/v1/jobs").json()["items"]) == 2
        c.post("/api/v1/jobs/clear-history", json={"ids": [job["id"]]})
        assert (
            c.post("/api/v1/jobs/" + job["id"] + "/resume", json={}).status_code == 200
        )
        assert c.get("/api/v1/jobs").json()["archived_count"] == 0


def test_active_explicit_cleanup_is_atomic_and_unknown_id_rejected(tmp_path):
    app = make_app(tmp_path, run_queue=False)
    with TestClient(app) as c:
        c.get("/api/v1/session")
        a = c.post("/api/v1/jobs", json={"platform": "dy", "keywords": "a"}).json()[
            "id"
        ]
        b = c.post("/api/v1/jobs", json={"platform": "dy", "keywords": "b"}).json()[
            "id"
        ]
        c.post("/api/v1/jobs/" + a + "/stop", json={})
        assert (
            c.post("/api/v1/jobs/clear-history", json={"ids": [a, b]}).status_code
            == 409
        )
        assert c.get("/api/v1/jobs").json()["archived_count"] == 0
        assert (
            c.post("/api/v1/jobs/clear-history", json={"ids": ["missing"]}).status_code
            == 404
        )
        assert (
            c.post("/api/v1/jobs/clear-history", json={"ids": []}).json()["count"] == 0
        )
        assert (
            c.post("/api/v1/jobs/clear-history", json={"ids": [a, a]}).json()["count"]
            == 1
        )
    assert (
        Database(tmp_path).query("SELECT count(*) n FROM job_history_archive")[0]["n"]
        == 1
    )


def test_v2_migration_backs_up_and_keeps_login(tmp_path):
    db = Database(tmp_path)
    db.execute("PRAGMA user_version=2")
    db.execute("INSERT INTO settings VALUES('keep','123')")
    db = Database(tmp_path)
    backup = next((tmp_path / "backups").glob("*.sqlite3"))
    with sqlite3.connect(backup) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
    assert db.query("PRAGMA user_version")[0]["user_version"] == 4
    assert db.query("SELECT value FROM settings")[0]["value"] == "123"
