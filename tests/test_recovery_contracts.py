"""Recovery behavior observed through authenticated HTTP and real worker processes."""

import os
import sys
import time
from contextlib import contextmanager

from fastapi.testclient import TestClient
from workbench.app import make_app
from workbench.queue import Queue


@contextmanager
def host(tmp_path, worker, *, probe_command=None, clock=None):
    app = make_app(tmp_path, run_queue=False)
    if clock:
        app.state.availability.clock = clock
    queue = Queue(
        app.state.db,
        lambda job_id: [sys.executable, "-c", worker, job_id],
        availability=app.state.availability,
        probe_command=probe_command,
    )
    with TestClient(app) as client:
        client.get("/api/v1/session")
        queue.start()
        try:
            yield client
        finally:
            queue.close()


def settled(client, identity):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        job = client.get("/api/v1/jobs/" + identity).json()
        if job["state"] not in {"queued", "running", "stopping"}:
            return job
        time.sleep(0.02)
    raise AssertionError(job)


LOGGED_OUT = """
import os,sys
os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1]
from workbench.auth import report
report('failed', state='unauthenticated', reason='logged_out')
"""


def test_stop_waiting_for_auth_is_visible_and_survives_restart(tmp_path):
    with host(tmp_path, LOGGED_OUT) as client:
        identity = client.post(
            "/api/v1/jobs", json={"platform": "dy", "keywords": "fixture"}
        ).json()["id"]
        assert settled(client, identity)["state"] == "needs_login"
        response = client.post("/api/v1/jobs/" + identity + "/stop", json={})
        assert response.status_code == 200
        assert response.json()["state"] == "cancelled"
    with TestClient(make_app(tmp_path, run_queue=False)) as client:
        client.get("/api/v1/session")
        assert client.get("/api/v1/jobs/" + identity).json()["state"] == "cancelled"


def test_cleanup_cannot_hide_a_task_that_will_automatically_resume(tmp_path):
    with host(tmp_path,LOGGED_OUT) as client:
        created=client.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
        assert settled(client,created['id'])['state']=='needs_login'
        explicit=client.post('/api/v1/jobs/clear-history',json={'ids':[created['id']]})
        assert explicit.status_code==409
        assert client.post('/api/v1/jobs/clear-history',json={}).json()['count']==0
        client.post('/api/v1/jobs/'+created['id']+'/stop',json={})
        assert client.post('/api/v1/jobs/clear-history',json={'ids':[created['id']]}).json()['count']==1


def test_retry_preserves_each_attempt_and_its_events(tmp_path):
    with host(tmp_path, "print('ERROR fixture unavailable')") as client:
        identity = client.post(
            "/api/v1/jobs", json={"platform": "dy", "keywords": "fixture"}
        ).json()["id"]
        assert settled(client, identity)["state"] == "failed"
        assert (
            client.post("/api/v1/jobs/" + identity + "/resume", json={}).status_code
            == 200
        )
        assert settled(client, identity)["attempt"] == 2
        response = client.get("/api/v1/jobs/" + identity + "/attempts")
        assert response.status_code == 200
        attempts = response.json()["items"]
        assert [(a["attempt"], a["state"]) for a in attempts] == [
            (1, "failed"),
            (2, "failed"),
        ]
        assert all(a["checkpoint"]["saved_records"] == 0 for a in attempts)
        events = client.get("/api/v1/jobs/" + identity + "/events?attempt=1").json()[
            "items"
        ]
        assert events and all(e["attempt"] == 1 for e in events)


def test_stopped_worker_cannot_commit_late_records(tmp_path):
    ready, release = tmp_path / "ready", tmp_path / "release"
    worker = f"""
import os,sys,time
from pathlib import Path
os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1]
Path({str(ready)!r}).touch()
while not Path({str(release)!r}).exists():time.sleep(.01)
from workbench.queue import collect_record
collect_record({{'aweme_id':'late','desc':'fixture'}},'contents','dy')
"""
    with host(tmp_path, worker) as client:
        created = client.post(
            "/api/v1/jobs", json={"platform": "dy", "keywords": "fixture"}
        ).json()
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        assert (
            client.post("/api/v1/jobs/" + created["id"] + "/stop", json={}).status_code
            == 200
        )
        release.touch()
        assert settled(client, created["id"])["state"] == "cancelled"
        records = client.get(
            "/api/v1/datasets/" + created["dataset_id"] + "/records"
        ).json()["items"]
        assert records == []


def test_attempt_counts_distinguish_new_changed_and_duplicate_records(tmp_path):
    worker = """
import os,sys
os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1]
from workbench.queue import collect_record
collect_record({'aweme_id':'v1','desc':'first'},'contents','dy')
collect_record({'aweme_id':'v1','desc':'first'},'contents','dy')
collect_record({'aweme_id':'v1','desc':'second'},'contents','dy')
"""
    with host(tmp_path, worker) as client:
        identity = client.post(
            "/api/v1/jobs", json={"platform": "dy", "keywords": "fixture"}
        ).json()["id"]
        job = settled(client, identity)
        assert job["state"] == "completed"
        assert job["status_detail"]["counts"] == {
            "received": 3,
            "inserted": 1,
            "updated": 1,
            "unchanged": 1,
            "dataset_total": 1,
        }
        history = client.get("/api/v1/jobs/" + identity + "/attempts").json()["items"]
        assert history[0]["counts"]["inserted"] == 1


def test_session_operation_has_stable_identity_and_terminal_result(tmp_path):
    with host(tmp_path, LOGGED_OUT) as client:
        first = client.post(
            "/api/v1/platforms/dy/session/check",
            json={},
            headers={"Idempotency-Key": "check-one"},
        )
        assert first.status_code == 200
        operation_id = first.json()["operation_id"]
        assert (
            client.post(
                "/api/v1/platforms/dy/session/check",
                json={},
                headers={"Idempotency-Key": "check-one"},
            ).json()["operation_id"]
            == operation_id
        )
        settled(client, first.json()["id"])
        operation = client.get("/api/v1/operations/" + operation_id).json()
        assert operation["kind"] == "check"
        assert operation["state"] == "failed"
        assert operation["reason_code"] == "logged_out"
        assert operation["next_actions"]
        second = client.post("/api/v1/platforms/dy/session/open", json={}).json()
        assert second["operation_id"] != operation_id
        settled(client, second["id"])
        assert client.get("/api/v1/operations/" + operation_id).json() == operation


def test_expected_and_dynamic_scopes_keep_unexecuted_work_visible(tmp_path):
    worker = """
import os,sys
os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1]
from tools.crawl_status import record
record('search','started',scope='first')
record('comments','started',scope='video-one')
record('search','no_results',scope='first',response={'status_code':0,'has_more':0,'data':[]})
"""
    with host(tmp_path, worker) as client:
        identity = client.post(
            "/api/v1/jobs", json={"platform": "dy", "keywords": "first,second"}
        ).json()["id"]
        settled(client, identity)
        response = client.get("/api/v1/jobs/" + identity + "/scopes?attempt=1")
        assert response.status_code == 200
        scopes = {(s["capability"], s["scope"]): s for s in response.json()["items"]}
        assert scopes["search", "first"]["outcome"] == "no_results"
        assert scopes["search", "second"]["outcome"] == "not_executed"
        assert scopes["comments", "video-one"]["outcome"] == "abnormal_stop"
        assert scopes["comments", "video-one"]["origin"] == "discovered"
        diagnostic = client.get("/api/v1/jobs/" + identity + "/diagnostics").json()
        assert diagnostic["job_id"] == identity
        assert "first" not in str(diagnostic) and "video-one" not in str(diagnostic)


def test_raw_file_failure_does_not_lose_sqlite_commit_and_can_be_rebuilt(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("file blocks directory")
    worker = f"""
import os,sys,asyncio
os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1]
import config
config.SAVE_DATA_PATH={str(blocked)!r}
config.ENABLE_GET_WORDCLOUD=False
from tools.async_file_writer import AsyncFileWriter
asyncio.run(AsyncFileWriter('dy','search').write_to_jsonl({{'aweme_id':'durable','desc':'kept'}},'contents'))
"""
    with host(tmp_path, worker) as client:
        created = client.post(
            "/api/v1/jobs", json={"platform": "dy", "keywords": "fixture"}
        ).json()
        settled(client, created["id"])
        records = client.get(
            "/api/v1/datasets/" + created["dataset_id"] + "/records"
        ).json()["items"]
        assert len(records) == 1
        status = client.get("/api/v1/jobs/" + created["id"] + "/raw-files").json()
        assert status["counts"] == {"failed": 1}
        rebuilt = client.post(
            "/api/v1/jobs/" + created["id"] + "/raw-files/rebuild", json={}
        )
        assert rebuilt.status_code == 200
        assert rebuilt.json()["records"] == 1
        assert client.get("/api/v1/jobs/" + created["id"] + "/raw-files").json()[
            "counts"
        ] == {"rebuilt": 1}


def test_probe_is_serial_deduplicated_and_does_not_create_research_data(tmp_path):
    probe = 'import pathlib,sys,time;time.sleep(.1);pathlib.Path(sys.argv[1]).write_text(\'{"verdict":"available","reason":"control_success","requests":1}\')'
    command = lambda identity, result: [sys.executable, "-c", probe, str(result)]
    with host(tmp_path, "", probe_command=command) as client:
        first = client.post(
            "/api/v1/platforms/dy/probes", json={"capability": "search"}
        ).json()
        second = client.post(
            "/api/v1/platforms/dy/probes", json={"capability": "search"}
        ).json()
        assert second["id"] == first["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            operation = client.get("/api/v1/operations/" + first["id"]).json()
            if operation["state"] not in ("accepted", "running"):
                break
            time.sleep(0.02)
        assert operation["state"] == "completed"
        assert operation["verdict"] == "available"
        assert operation["requests"] == 1
        assert client.get("/api/v1/jobs").json()["items"] == []
        assert client.get("/api/v1/datasets").json()["items"] == []


def test_other_platform_probe_is_explicitly_not_enabled(tmp_path):
    with host(tmp_path, "") as client:
        result = client.post(
            "/api/v1/platforms/xhs/probes", json={"capability": "comments"}
        ).json()
        operation = client.get("/api/v1/operations/" + result["id"]).json()
        assert operation["state"] == "completed"
        assert operation["attempted"] is False
        assert operation["reason_code"] == "probe_not_enabled"


def test_cooldown_survives_restart_and_stop_prevents_timer_resume(tmp_path):
    clock = [1000000.0]
    worker = """
import os,sys
os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1]
from tools.crawl_status import record
record('search','restriction_explicit',scope='fixture',response={'http_status':429})
"""
    with host(tmp_path, worker, clock=lambda: clock[0]) as client:
        # A saved account is required for automatic replay; no platform login is simulated as a real acceptance.
        client.app.state.db.execute(
            "INSERT INTO platform_sessions(platform,generation,account_id,check_state) VALUES('dy',1,'fixture-account','authenticated')"
        )
        created = client.post(
            "/api/v1/jobs", json={"platform": "dy", "keywords": "fixture"}
        ).json()
        assert settled(client, created["id"])["state"] == "waiting_access"
        availability = client.get("/api/v1/platforms/dy/availability").json()["items"][
            0
        ]
        assert availability["state"] == "restricted"
        assert availability["cooldown_until"] == clock[0] + 3600
        assert (
            client.post("/api/v1/jobs/" + created["id"] + "/stop", json={}).json()[
                "state"
            ]
            == "cancelled"
        )
    clock[0] += 3700
    with host(tmp_path, "", clock=lambda: clock[0]) as client:
        assert (
            client.get("/api/v1/platforms/dy/availability").json()["items"][0][
                "cooldown_until"
            ]
            == 1003600
        )
        time.sleep(0.5)
        assert (
            client.get("/api/v1/jobs/" + created["id"]).json()["state"] == "cancelled"
        )


def test_three_unsuccessful_rechecks_keep_backoff_then_require_a_person(tmp_path):
    clock = [1000000.0]
    probe = 'import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(\'{"verdict":"network_error","reason":"timeout","attempted":true,"requests":1}\')'
    command = lambda identity, result: [sys.executable, "-c", probe, str(result)]
    worker = """
import os,sys
os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1]
from tools.crawl_status import record
record('search','restriction_explicit',scope='fixture',response={'http_status':429})
"""
    with host(
        tmp_path, worker, clock=lambda: clock[0], probe_command=command
    ) as client:
        client.app.state.db.execute(
            "INSERT INTO platform_sessions(platform,generation,account_id,check_state) VALUES('dy',1,'fixture-account','authenticated')"
        )
        created = client.post(
            "/api/v1/jobs", json={"platform": "dy", "keywords": "fixture"}
        ).json()
        assert settled(client, created["id"])["state"] == "waiting_access"
        for check, delay in ((1, 7200), (2, 14400), (3, None)):
            previous = client.get("/api/v1/platforms/dy/availability").json()["items"][
                0
            ]
            clock[0] = previous["cooldown_until"] + 1
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = client.get("/api/v1/platforms/dy/availability").json()["items"][
                    0
                ]
                if state["checks"] == check:
                    break
                time.sleep(0.03)
            assert state["checks"] == check
            assert state["cooldown_until"] == (clock[0] + delay if delay else None)
            assert state["state"] == ("manual_action" if check == 3 else "restricted")
