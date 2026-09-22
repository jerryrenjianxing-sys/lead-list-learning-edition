import concurrent.futures
import io
import json
import sys
import time
import zipfile
import pytest
from fastapi.testclient import TestClient
from workbench.app import make_app
from workbench.database import Database
from workbench.queue import Queue, create_job, control
from workbench.models import CrawlRequest


@pytest.fixture
def client(tmp_path):
    with TestClient(make_app(tmp_path, run_queue=False)) as c:
        c.get("/api/v1/session")
        yield c


def post(c, path, body=None, key=None):
    response = c.post(
        "/api/v1" + path,
        json=body or {},
        headers={"Idempotency-Key": key} if key else {},
    )
    assert response.status_code == 200, response.text
    return response.json()


def prepare(c, schema=None):
    dataset = post(c, "/demo")
    body = {
        "dataset_id": dataset["id"],
        "name": "自由分析",
        "goal": "按我自己的任意字段分析",
    }
    if schema is not None:
        body["result_schema"] = schema
    a = post(c, "/analyses", body)
    inputs = c.get("/api/v1/analyses/" + a["id"] + "/inputs").json()["items"]
    return dataset, a, inputs


@pytest.mark.parametrize("output_format", ["json", "csv", "xlsx", "docx", "md"])
def test_roundtrip_free_form_evidence_and_exports(client, output_format):
    dataset, a, inputs = prepare(client)
    batch = {
        "batch_id": "batch-any-name",
        "processed_ids": [r["id"] for r in inputs],
        "results": [
            {
                "payload": {
                    "用户自定义字段": "自由结论",
                    "新字段": ["a", "b"],
                    "用户定义数量": 1000001,
                },
                "evidence_ids": [inputs[0]["id"]],
            }
        ],
    }
    result = post(client, f"/analyses/{a['id']}/batches", batch, "batch-request")
    assert result["coverage"]["processed"] == 6
    assert (
        post(client, f"/analyses/{a['id']}/batches", batch, "batch-request") == result
    )
    assert post(client, f"/analyses/{a['id']}/batches", batch) == result
    finished = post(client, f"/analyses/{a['id']}/finish")
    assert finished["state"] == "completed"
    artifact = post(
        client, f"/analyses/{a['id']}/export", {"format": output_format}, "export-id"
    )
    assert (
        post(
            client,
            f"/analyses/{a['id']}/export",
            {"format": output_format},
            "export-id",
        )
        == artifact
    )
    content = client.get(artifact["download_url"]).content
    assert len(content) > 100
    if output_format == "json":
        assert json.loads(content)["evidence"][0]["payload"] == inputs[0]["payload"]
    if output_format in ("xlsx", "docx"):
        assert zipfile.is_zipfile(io.BytesIO(content))
    assert len(client.get("/api/v1/artifacts").json()["items"]) == 1


def test_snapshot_stable_after_source_update(client):
    dataset, a, inputs = prepare(client)
    post(
        client,
        f"/datasets/{dataset['id']}/records",
        {
            "records": [
                {
                    "source_id": "demo-1",
                    "platform": "demo",
                    "kind": "post",
                    "payload": {"text": "后续改动"},
                }
            ]
        },
    )
    original = client.get(f"/api/v1/analyses/{a['id']}/inputs").json()["items"]
    assert original == inputs
    current = client.get(f"/api/v1/datasets/{dataset['id']}/records").json()["items"]
    assert current[0]["payload"]["text"] == "后续改动"
    assert current[0]["id"] == inputs[0]["id"]


def test_bad_batch_rolls_back_results_and_coverage(client):
    _, a, inputs = prepare(
        client,
        {
            "type": "object",
            "required": ["score"],
            "properties": {"score": {"type": "number"}},
        },
    )
    path = f"/api/v1/analyses/{a['id']}/batches"
    body = {
        "batch_id": "bad",
        "processed_ids": [inputs[0]["id"]],
        "results": [
            {"payload": {"score": 1}, "evidence_ids": [inputs[0]["id"]]},
            {"payload": {"score": "invalid"}},
        ],
    }
    assert client.post(path, json=body).status_code == 422
    assert (
        client.get(f"/api/v1/analyses/{a['id']}").json()["coverage"]["processed"] == 0
    )
    assert client.get(f"/api/v1/analyses/{a['id']}/results").json()["items"] == []
    body["results"] = [{"payload": {"score": 1}, "evidence_ids": ["missing"]}]
    assert client.post(path, json=body).status_code == 422


def test_partial_failure_then_resume(client):
    _, a, inputs = prepare(client)
    path = f"/analyses/{a['id']}"
    post(
        client,
        path + "/batches",
        {
            "batch_id": "first",
            "processed_ids": [inputs[0]["id"]],
            "failed": {inputs[1]["id"]: "模型调用中断"},
        },
    )
    assert client.post("/api/v1" + path + "/finish", json={}).status_code == 409
    assert post(client, path + "/finish", {"allow_partial": True})["state"] == "partial"
    post(
        client,
        path + "/batches",
        {"batch_id": "second", "processed_ids": [r["id"] for r in inputs[1:]]},
    )
    assert post(client, path + "/finish")["state"] == "completed"


def test_idempotency_conflict_and_restart(tmp_path):
    app = make_app(tmp_path, run_queue=False)
    with TestClient(app) as c:
        c.get("/api/v1/session")
        r = post(c, "/datasets", {"name": "first"}, "stable")
        assert (
            c.post(
                "/api/v1/datasets",
                json={"name": "changed"},
                headers={"Idempotency-Key": "stable"},
            ).status_code
            == 409
        )
    with TestClient(make_app(tmp_path, run_queue=False)) as c:
        c.get("/api/v1/session")
        assert post(c, "/datasets", {"name": "first"}, "stable") == r
        assert len(c.get("/api/v1/datasets").json()["items"]) == 1


def test_concurrent_retry_is_atomic(tmp_path):
    db = Database(tmp_path)

    def run(_):
        return db.write(
            "same",
            "same",
            {},
            lambda con: create_job(
                con, CrawlRequest(platform="xhs", keywords="test").model_dump()
            ),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(run, range(20)))
    assert len({r["id"] for r in responses}) == 1
    assert len(db.query("SELECT * FROM jobs")) == 1


def test_auth_origin_and_skill_package(client):
    assert (
        client.get(
            "/api/v1/datasets", headers={"Origin": "https://unrelated.example"}
        ).status_code
        == 403
    )
    client.cookies.clear()
    assert client.get("/api/v1/datasets").status_code == 401
    archive = zipfile.ZipFile(io.BytesIO(client.get("/api/v1/skill").content))
    assert "media-workbench/SKILL.md" in archive.namelist()
    assert "media-workbench/scripts/workbench.py" in archive.namelist()
    assert len(client.get("/api/v1/skill?format=connect").text) < 1500


def test_no_total_or_count_cap_and_query_pagination(client):
    r = post(
        client,
        "/datasets",
        {
            "name": "many",
            "records": [{"id": str(i), "text": str(i)} for i in range(2010)],
        },
    )
    page = client.get(f"/api/v1/datasets/{r['id']}/records?limit=2000").json()
    assert len(page["items"]) == 2000
    last = client.get(
        f"/api/v1/datasets/{r['id']}/records?after={page['next_after']}"
    ).json()
    assert len(last["items"]) == 10 and last["next_after"] is None
    j = post(
        client,
        "/jobs",
        {"platform": "dy", "keywords": "anything", "max_notes_count": 2000000},
    )
    assert (
        client.get(f"/api/v1/jobs/{j['id']}").json()["options"]["max_notes_count"]
        == 2000000
    )


def wait_state(db, id, states):
    for _ in range(150):
        job = db.one("jobs", id)
        if job["state"] in states:
            return job
        time.sleep(0.05)
    raise AssertionError(db.one("jobs", id))


def test_queue_failure_does_not_block_next_and_stops_persist(tmp_path):
    db = Database(tmp_path)

    def create(platform):
        return db.write(
            None,
            "job",
            {},
            lambda con: create_job(
                con, CrawlRequest(platform=platform, keywords="test").model_dump()
            ),
        )["id"]

    first, second = create("dy"), create("xhs")
    worker = lambda id: [
        sys.executable,
        "-c",
        'import sys; print("worker finished"); sys.exit('
        + ("1" if id == first else "0")
        + ")",
    ]
    queue = Queue(db, worker)
    queue.start()
    try:
        assert wait_state(db, first, {"failed"})["state"] == "failed"
        assert wait_state(db, second, {"completed"})["state"] == "completed"
    finally:
        queue.close()
    third = create("bili")
    slow = Queue(
        db,
        lambda id: [
            sys.executable,
            "-c",
            "from pathlib import Path; import time; p=Path("
            + repr(str(tmp_path / "runs" / third / ".stop"))
            + ");\nwhile not p.exists(): time.sleep(.02)",
        ],
    )
    slow.start()
    try:
        wait_state(db, third, {"running"})
        db.write(None, "stop", {}, lambda con: control(con, third, "stop"))
        assert wait_state(db, third, {"cancelled"})["state"] == "cancelled"
    finally:
        slow.close()
    assert Database(tmp_path).one("jobs", third)["state"] == "cancelled"


def test_legacy_surface_uses_same_queue(client):
    request = {"platform": "xhs", "keywords": "example", "task_id": "legacy-fixed"}
    first = client.post("/api/crawler/start", json=request).json()
    assert client.post("/api/crawler/start", json=request).json() == first
    assert len(client.get("/api/v1/jobs").json()["items"]) == 1
    assert (
        client.post("/api/crawler/stop", json={"task_id": "legacy-fixed"}).status_code
        == 200
    )
    assert client.get("/api/v1/jobs/" + first["job_id"]).json()["state"] == "cancelled"


def test_update_waits_for_idle_and_freezes_new_writes(client):
    job = post(client, "/jobs", {"platform": "xhs", "keywords": "test"})
    assert client.post("/api/v1/host/prepare-update").status_code == 409
    post(client, f"/jobs/{job['id']}/stop")
    result = post(client, "/host/prepare-update")
    assert result["state"] == "stopping"
    import sqlite3

    with sqlite3.connect(result["backup"]) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert (
        client.post("/api/v1/datasets", json={"name": "late write"}).status_code == 409
    )
    assert (
        client.post(
            "/api/v1/jobs", json={"platform": "xhs", "keywords": "late"}
        ).status_code
        == 409
    )
