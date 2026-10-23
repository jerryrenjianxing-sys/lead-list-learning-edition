"""Recovery behavior observed through authenticated HTTP and real worker processes."""

import sys
import time
import pytest
from pathlib import Path
import json
import psutil
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


@pytest.mark.parametrize(
    "platform,capability,reason",
    [
        ("tieba", "comments", "request_failed"),
        ("wb", "replies", "response_unconfirmed"),
    ],
)
def test_parsed_comment_paths_keep_structural_failure_facts(
    tmp_path, platform, capability, reason
):
    driver = Path(__file__).parent / "fixtures" / "embedded_comments_worker.py"
    worker = f"import runpy,sys;sys.argv=[{str(driver)!r},{platform!r},sys.argv[1]];runpy.run_path({str(driver)!r},run_name='__main__')"
    with host(tmp_path, worker) as client:
        job = client.post(
            "/api/v1/jobs", json={'retry_threshold': None, "platform": platform, "keywords": "fixture"}
        ).json()
        settled(client, job["id"])
        scopes = client.get("/api/v1/jobs/" + job["id"] + "/scopes").json()["items"]
        assert any(
            s["capability"] == capability and s["reason"] == reason for s in scopes
        )


def test_stop_waiting_for_auth_is_visible_and_survives_restart(tmp_path):
    with host(tmp_path, LOGGED_OUT) as client:
        identity = client.post(
            "/api/v1/jobs", json={'retry_threshold': None, "platform": "dy", "keywords": "fixture"}
        ).json()["id"]
        assert settled(client, identity)["state"] == "needs_login"
        response = client.post("/api/v1/jobs/" + identity + "/stop", json={})
        assert response.status_code == 200
        assert response.json()["state"] == "cancelled"
    with TestClient(make_app(tmp_path, run_queue=False)) as client:
        client.get("/api/v1/session")
        assert client.get("/api/v1/jobs/" + identity).json()["state"] == "cancelled"


def test_cleanup_cannot_hide_a_task_that_will_automatically_resume(tmp_path):
    with host(tmp_path, LOGGED_OUT) as client:
        created = client.post(
            "/api/v1/jobs", json={'retry_threshold': None, "platform": "dy", "keywords": "fixture"}
        ).json()
        assert settled(client, created["id"])["state"] == "needs_login"
        explicit = client.post(
            "/api/v1/jobs/clear-history", json={"ids": [created["id"]]}
        )
        assert explicit.status_code == 409
        assert client.post("/api/v1/jobs/clear-history", json={}).json()["count"] == 0
        client.post("/api/v1/jobs/" + created["id"] + "/stop", json={})
        assert (
            client.post(
                "/api/v1/jobs/clear-history", json={"ids": [created["id"]]}
            ).json()["count"]
            == 1
        )


def test_retry_preserves_each_attempt_and_its_events(tmp_path):
    with host(tmp_path, "print('ERROR fixture unavailable')") as client:
        identity = client.post(
            "/api/v1/jobs", json={'retry_threshold': None, "platform": "dy", "keywords": "fixture"}
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
        page = client.get("/api/v1/jobs/" + identity + "/attempts?limit=1").json()
        assert len(page["items"]) == 1 and page["next_after"] == 1
        assert (
            client.get("/api/v1/jobs/" + identity + "/attempts?after=1&limit=1").json()[
                "items"
            ][0]["attempt"]
            == 2
        )
        events = client.get("/api/v1/jobs/" + identity + "/events?attempt=1").json()[
            "items"
        ]
        assert events and all(e["attempt"] == 1 for e in events)


def test_paged_attempts_do_not_hide_legacy_history_limitations(tmp_path):
    with host(tmp_path, "print('ERROR fixture')") as client:
        job = client.post(
            "/api/v1/jobs", json={'retry_threshold': None, "platform": "dy", "keywords": "fixture"}
        ).json()
        settled(client, job["id"])
        client.app.state.db.execute(
            "UPDATE job_attempts SET origin='legacy_snapshot' WHERE job_id=?",
            (job["id"],),
        )
        client.post("/api/v1/jobs/" + job["id"] + "/resume", json={})
        settled(client, job["id"])
        assert (
            client.get("/api/v1/jobs/" + job["id"] + "/attempts?after=1").json()[
                "history_complete"
            ]
            is False
        )


def test_forget_rejects_active_probe_and_preserves_saved_profile(tmp_path):
    app = make_app(tmp_path, run_queue=False)
    profile = tmp_path / "browser_data/cdp_dy_user_data_dir"
    profile.mkdir(parents=True)
    (profile / "Preferences").write_text("saved profile fixture")
    with TestClient(app) as client:
        client.get("/api/v1/session")
        operation = client.post(
            "/api/v1/platforms/dy/probes", json={"capability": "search"}
        ).json()
        assert operation["state"] == "accepted"
        assert (
            client.post("/api/v1/platforms/dy/session/forget", json={}).status_code
            == 409
        )
        assert (profile / "Preferences").read_text() == "saved profile fixture"


def test_gathered_detail_records_keep_per_scope_counts(tmp_path):
    worker = """
import asyncio,os,sys
os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1]
from workbench.platform_observation import observed
from workbench.queue import collect_record
@observed('dy','detail')
async def get(aweme_id):return {'aweme_id':aweme_id,'desc':'fixture'}
async def main():
 for item in await asyncio.gather(get('v1')):collect_record(item,'contents','dy')
asyncio.run(main())
"""
    with host(tmp_path, worker) as client:
        job = client.post(
            "/api/v1/jobs",
            json={'retry_threshold': None, "platform": "dy", "crawler_type": "detail", "specified_ids": "v1"},
        ).json()
        settled(client, job["id"])
        scope = next(
            s
            for s in client.get("/api/v1/jobs/" + job["id"] + "/scopes").json()["items"]
            if s["capability"] == "detail" and s["scope"] == "v1"
        )
        assert scope["counts"]["inserted"] == 1


def test_probe_result_retries_storage_commit_without_repeating_network(tmp_path):
    marker = tmp_path / "probe-count"
    code = f"import pathlib,time,json,sys;p=pathlib.Path({str(marker)!r});p.write_text(p.read_text()+'1' if p.exists() else '1');time.sleep(.2);pathlib.Path(sys.argv[1]).write_text(json.dumps({{'verdict':'available','reason':'control_success','attempted':True,'requests':1}}))"
    command = lambda identity, result: [sys.executable, "-c", code, str(result)]
    with host(tmp_path, "", probe_command=command) as client:
        db = client.app.state.db
        db.execute("CREATE TABLE transient_write_fault(enabled INTEGER)")
        db.execute("INSERT INTO transient_write_fault VALUES(1)")
        db.execute(
            "CREATE TRIGGER fail_probe_commit BEFORE UPDATE OF state ON operations WHEN NEW.state='completed' AND EXISTS(SELECT 1 FROM transient_write_fault WHERE enabled=1) BEGIN SELECT RAISE(ABORT,'temporary storage unavailable'); END"
        )
        op = client.post(
            "/api/v1/platforms/dy/probes", json={"capability": "search"}
        ).json()
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.6)
        db.execute("UPDATE transient_write_fault SET enabled=0")
        while time.monotonic() < deadline:
            result = client.get("/api/v1/operations/" + op["id"]).json()
            if result["state"] == "completed":
                break
            time.sleep(0.02)
        assert result["state"] == "completed"
        assert marker.read_text() == "1"
        job = client.post(
            "/api/v1/jobs", json={'retry_threshold': None, "platform": "xhs", "keywords": "fixture"}
        ).json()
        assert settled(client, job["id"])["state"] == "completed"


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
            "/api/v1/jobs", json={'retry_threshold': None, "platform": "dy", "keywords": "fixture"}
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
            "/api/v1/jobs", json={'retry_threshold': None, "platform": "dy", "keywords": "fixture"}
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
            "/api/v1/jobs", json={'retry_threshold': None, "platform": "dy", "keywords": "first,second"}
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
            "/api/v1/jobs", json={'retry_threshold': None, "platform": "dy", "keywords": "fixture"}
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
            "/api/v1/jobs", json={'retry_threshold': None, "platform": "dy", "keywords": "fixture"}
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
            "/api/v1/jobs", json={'retry_threshold': None, "platform": "dy", "keywords": "fixture"}
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


@pytest.mark.parametrize(
    "platform", ["dy", "xhs", "ks", "bili", "wb", "zhihu", "tieba"]
)
def test_seven_platform_terminal_search_shapes_are_observed_without_losing_parser_metadata(
    tmp_path, platform
):
    driver = Path(__file__).parent / "fixtures" / "observed_platform_worker.py"
    worker = f"import runpy,sys;sys.argv=[{str(driver)!r},{platform!r},sys.argv[1]];runpy.run_path({str(driver)!r},run_name='__main__')"
    with host(tmp_path, worker) as client:
        created = client.post(
            "/api/v1/jobs",
            json={'retry_threshold': None,
                "platform": platform,
                "keywords": "fixture",
                "enable_comments": False,
            },
        ).json()
        job = settled(client, created["id"])
        assert job["state"] == "completed", job
        scopes = client.get("/api/v1/jobs/" + created["id"] + "/scopes").json()["items"]
        assert len(scopes) == 1
        assert scopes[0]["outcome"] == "no_results"
        assert job["status_detail"]["range_verified"]


def test_successful_control_does_not_replay_a_persistently_bad_target_forever(tmp_path):
    probe = 'import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(\'{"verdict":"available","reason":"control_success","attempted":true,"requests":1}\')'
    command = lambda identity, result: [sys.executable, "-c", probe, str(result)]
    worker = """
import os,sys
os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1]
from tools.crawl_status import record
record('search','empty_response',scope='fixture')
"""
    with host(tmp_path, worker, probe_command=command) as client:
        client.app.state.db.execute(
            "INSERT INTO platform_sessions(platform,generation,account_id,check_state) VALUES('dy',1,'fixture-account','authenticated')"
        )
        created = client.post(
            "/api/v1/jobs", json={'retry_threshold': None, "platform": "dy", "keywords": "fixture"}
        ).json()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = client.get("/api/v1/jobs/" + created["id"]).json()
            if job["attempt"] >= 2:
                break
            time.sleep(0.03)
        time.sleep(1.2)
        job = client.get("/api/v1/jobs/" + created["id"]).json()
        assert job["attempt"] == 2
        assert job["state"] == "waiting_access"


def test_cancel_probe_reaps_its_process_tree_before_reporting_cleanup(tmp_path):
    marker = tmp_path / "probe-processes.json"
    probe = f"""
import subprocess,sys,time,os,json,pathlib
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
pathlib.Path({str(marker)!r}).write_text(json.dumps([os.getpid(),child.pid]))
time.sleep(120)
"""
    command = lambda identity, result: [sys.executable, "-c", probe]
    with host(tmp_path, "", probe_command=command) as client:
        identity = client.post(
            "/api/v1/platforms/dy/probes", json={"capability": "search"}
        ).json()["id"]
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        assert (
            client.post(
                "/api/v1/operations/" + identity + "/cancel", json={}
            ).status_code
            == 200
        )
        while time.monotonic() < deadline:
            operation = client.get("/api/v1/operations/" + identity).json()
            if operation["state"] == "cancelled" and "cleanup_complete" in operation:
                break
            time.sleep(0.02)
        assert operation["cleanup_complete"] is True
        assert operation["attempted"] is True
        assert not any(psutil.pid_exists(pid) for pid in json.loads(marker.read_text()))
        assert not (tmp_path / "probes" / identity).exists()


def test_cancelling_probe_recovers_after_host_restart(tmp_path):
    app = make_app(tmp_path, run_queue=False)
    with TestClient(app) as client:
        client.get("/api/v1/session")
        identity = client.post(
            "/api/v1/platforms/dy/probes", json={"capability": "search"}
        ).json()["id"]
        app.state.db.execute(
            "UPDATE operations SET state='cancelling' WHERE id=?", (identity,)
        )
    with host(tmp_path, "") as client:
        operation = client.get("/api/v1/operations/" + identity).json()
        assert operation["state"] == "failed"
        assert operation["reason_code"] == "host_interrupted"
        assert client.post("/api/v1/host/prepare-update", json={}).status_code == 200


def test_raw_rebuild_obeys_update_freeze_and_idempotent_receipt(tmp_path):
    with host(tmp_path, "") as client:
        created = client.post(
            "/api/v1/jobs", json={'retry_threshold': None, "platform": "dy", "keywords": "fixture"}
        ).json()
        settled(client, created["id"])
        key = {"Idempotency-Key": "raw-rebuild-once"}
        first = client.post(
            "/api/v1/jobs/" + created["id"] + "/raw-files/rebuild", json={}, headers=key
        )
        assert first.status_code == 200
        assert (
            client.post(
                "/api/v1/jobs/" + created["id"] + "/raw-files/rebuild",
                json={},
                headers=key,
            ).json()
            == first.json()
        )
        events = client.get("/api/v1/jobs/" + created["id"] + "/events").json()["items"]
        assert sum(event.get("kind") == "raw_rebuilt" for event in events) == 1
        assert client.post("/api/v1/host/prepare-update", json={}).status_code == 200
        assert (
            client.post(
                "/api/v1/jobs/" + created["id"] + "/raw-files/rebuild", json={}
            ).status_code
            == 409
        )


def test_probe_directory_failure_keeps_collection_queue_alive(tmp_path):
    (tmp_path / "probes").write_text("blocks probe directory creation")
    with host(tmp_path, "") as client:
        identity = client.post(
            "/api/v1/platforms/dy/probes", json={"capability": "search"}
        ).json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            operation = client.get("/api/v1/operations/" + identity).json()
            if operation["state"] not in ("accepted", "running", "cancelling"):
                break
            time.sleep(0.02)
        assert operation["attempted"] is False
        assert operation["reason_code"] == "storage_error"
        assert operation["cleanup_complete"] is True
        created = client.post(
            "/api/v1/jobs", json={'retry_threshold': None, "platform": "xhs", "keywords": "fixture"}
        ).json()
        assert settled(client, created["id"])["state"] == "completed"


@pytest.mark.parametrize(
    "scenario,capability,expected_requests,verdict",
    [
        ("network", "search", 1, "network_error"),
        ("comments", "comments", 2, "available"),
    ],
)
def test_real_probe_worker_shares_request_budget_without_transport_retry_amplification(
    tmp_path, scenario, capability, expected_requests, verdict
):
    driver = Path(__file__).parent / "fixtures" / "probe_transport_worker.py"
    marker = tmp_path / "actual-network-calls.json"
    command = lambda identity, result: [
        sys.executable,
        str(driver),
        identity,
        str(result),
        str(marker),
        scenario,
    ]
    with host(tmp_path, "", probe_command=command) as client:
        client.app.state.db.execute(
            "INSERT INTO access_samples VALUES('dy','','comments',?,?)",
            (json.dumps({"aweme_id": "sample"}), "2026-10-21T00:00:00Z"),
        )
        identity = client.post(
            "/api/v1/platforms/dy/probes", json={"capability": capability}
        ).json()["id"]
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            operation = client.get("/api/v1/operations/" + identity).json()
            if operation["state"] not in ("accepted", "running", "cancelling"):
                break
            time.sleep(0.03)
        assert operation["verdict"] == verdict, operation
        assert len(json.loads(marker.read_text())) == expected_requests
        assert (
            operation["requests"] == expected_requests + 1
        )  # Includes the initial navigation.
        assert operation["requests"] <= 3
        assert operation["cleanup_complete"] is True
        assert client.get("/api/v1/datasets").json()["items"] == []
