"""Retired download options must not reactivate downloads through old clients/jobs."""

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from workbench.app import make_app
from workbench.database import Database, dump
from workbench.models import CrawlRequest
from workbench.queue import Queue, control, create_job


@pytest.mark.parametrize("endpoint", ["/api/v1/jobs", "/api/crawler/start"])
def test_old_clients_cannot_request_media(endpoint, tmp_path):
    app = make_app(tmp_path, run_queue=False)
    with TestClient(app) as client:
        client.get("/api/v1/session")
        response = client.post(
            endpoint,
            json={"platform": "dy", "keywords": "sample", "enable_media": True},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        job_id = result.get("id") or result["job_id"]
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        assert "enable_media" not in job["options"]
        assert "enable_media" not in json.loads(
            app.state.db.one("jobs", job_id)["options"]
        )
        capabilities = client.get("/api/v1/capabilities").json()
        assert all(p["media"] is False for p in capabilities["platforms"])
        schema = client.get("/openapi.json").json()["components"]["schemas"][
            "CrawlRequest"
        ]
        assert "enable_media" not in schema["properties"]
        assert "enable_media" not in client.get("/api/v1/skill?format=markdown").text


@pytest.mark.parametrize("state", ["queued", "cancelled"])
def test_old_jobs_discard_download_setting_and_keep_other_options(state, tmp_path):
    db = Database(tmp_path)
    options = CrawlRequest(
        platform="dy",
        keywords="sample",
        enable_comments=True,
        enable_sub_comments=True,
        enrich_profiles=True,
        max_notes_count=100,
    ).model_dump()
    job = db.write(None, "create", options, lambda con: create_job(con, options))
    old_options = {**options, "enable_media": True}
    db.execute(
        "UPDATE jobs SET options=?,state=? WHERE id=?",
        (dump(old_options), state, job["id"]),
    )
    old_media = tmp_path / "runs" / job["id"] / "existing.mp4"
    old_media.parent.mkdir(parents=True)
    old_media.write_bytes(b"existing user file")
    if state == "cancelled":
        db.write(None, "resume", {}, lambda con: control(con, job["id"], "resume"))
    claimed = Queue(db)._claim()
    assert claimed["id"] == job["id"]
    assert claimed["dataset_id"] == job["dataset_id"]
    assert json.loads(db.one("jobs", job["id"])["options"]) == options
    assert old_media.read_bytes() == b"existing user file"


@pytest.mark.parametrize(
    "platform", ["xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu"]
)
def test_worker_overrides_old_job_and_enabled_upstream_default(
    platform, tmp_path, monkeypatch
):
    import config
    import cmd_arg
    from workbench import worker
    from tools.browser_launcher import BrowserLauncher
    from tools import app_runner

    # The upstream parser mutates global config; restore every setting after the test.
    for key, value in vars(config).copy().items():
        if key.isupper():
            monkeypatch.setattr(config, key, value)
    monkeypatch.setattr(config, "ENABLE_GET_MEDIA", True)
    db = Database(tmp_path)
    options = CrawlRequest(platform=platform, keywords="sample").model_dump()
    job = db.write(None, "create", options, lambda con: create_job(con, options))
    db.execute(
        "UPDATE jobs SET options=? WHERE id=?",
        (dump({**options, "enable_media": True}), job["id"]),
    )
    monkeypatch.setattr(worker, "data_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["worker", job["id"]])
    monkeypatch.setattr(
        BrowserLauncher, "detect_browser_paths", lambda _: ["test-browser"]
    )
    monkeypatch.chdir(tmp_path)
    for key in (
        "MEDIAWORKBENCH_JOB_ID",
        "MEDIAWORKBENCH_BROWSER_ROOT",
        "MEDIAWORKBENCH_LOGIN_DIR",
    ):
        monkeypatch.setenv(key, "test")
    observed = []

    async def engine_main():
        observed.append(config.ENABLE_GET_MEDIA)
        await cmd_arg.parse_cmd(sys.argv[1:])
        observed.append(config.ENABLE_GET_MEDIA)
        assert config.ENABLE_GET_COMMENTS is True

    monkeypatch.setitem(
        sys.modules, "main", SimpleNamespace(main=engine_main, async_cleanup=None)
    )
    monkeypatch.setattr(
        app_runner, "run", lambda crawl, cleanup, **kw: asyncio.run(crawl())
    )
    worker.main()
    assert observed == [False, False]
