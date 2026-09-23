import concurrent.futures
import json
import threading
import time
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from workbench.app import make_app
from workbench.selftest import SelfTests, atomic_json, local_result
from workbench.selftest_runner import classify_network


@pytest.fixture
def host(tmp_path, monkeypatch):
    def fake_run(self):
        self.cancel_event.wait(10)
        with self.lock:
            self.result.update(state="cancelled", message="cancelled")
            self._clean()
            self._save()
    monkeypatch.setattr(SelfTests, "_run", fake_run)
    app = make_app(tmp_path, run_queue=False)
    with TestClient(app) as client:
        client.get("/api/v1/session")
        yield client, app


def test_selftest_api_idempotency_parallel_and_business_isolation(host):
    client, app = host
    assert client.get("/api/v1/diagnostics/self-tests/latest").json() is None
    def start(i):
        response = client.post("/api/v1/diagnostics/self-tests", json={"include_network": False}, headers={"Idempotency-Key": "self-" + str(i)})
        assert response.status_code == 200
        return response.json()
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(start, range(5)))
    assert len({r["id"] for r in results}) == 1
    identity = results[0]["id"]
    assert start(0)["id"] == identity
    assert client.get("/api/v1/diagnostics/self-tests/" + identity).json()["state"] == "running"
    assert client.get("/api/v1/diagnostics/self-tests/latest").json()["id"] == identity
    assert client.get("/api/v1/diagnostics/self-tests/missing").status_code == 404
    assert client.post("/api/v1/host/prepare-update").status_code == 409
    for table in ("jobs", "datasets", "records", "analyses", "platform_auth", "platform_sessions"):
        assert app.state.db.query("SELECT count(*) n FROM " + table)[0]["n"] == 0
    assert client.post("/api/v1/diagnostics/self-tests/" + identity + "/cancel", json={}).status_code == 200
    app.state.self_tests.thread.join(timeout=3)
    assert not app.state.self_tests.workspace.exists()
    assert client.get("/api/v1/diagnostics/self-tests/latest").json()["state"] == "cancelled"
    # A transport retry after cancellation must not start another run.
    assert start(0)["id"] == identity
    assert not app.state.self_tests.active()


def test_auth_and_no_public_worker_override(host):
    client, _ = host
    client.cookies.clear()
    assert client.post("/api/v1/diagnostics/self-tests", json={}).status_code == 401
    client.get("/api/v1/session")
    assert client.post("/api/v1/diagnostics/self-tests", json={"worker_command": "bad"}).status_code == 422
    assert client.post("/api/v1/jobs", json={"platform": "wb", "keywords": "test", "worker_command": "bad"}).status_code == 422


def test_restart_marks_interruption_and_removes_only_owned_workspace(tmp_path):
    manager = SelfTests(tmp_path)
    manager.workspace.mkdir()
    (manager.workspace / "temporary.json").write_text("{}")
    personal = tmp_path / "account.bin"
    personal.write_bytes(b"unchanged")
    atomic_json(manager.file, {"id": "test", "state": "running", "steps": [{"id": "runtime", "state": "running"}]})
    restarted = SelfTests(tmp_path)
    assert restarted.view()["state"] == "interrupted"
    assert restarted.view()["local_state"] == "incomplete"
    assert not restarted.workspace.exists()
    assert personal.read_bytes() == b"unchanged"


@pytest.mark.parametrize("kwargs,state", [
    ({}, "passed"), ({"restricted": True}, "limited"), ({"empty": True}, "unconfirmed"),
    ({"error": TimeoutError()}, "unconfirmed"), ({"error": httpx.ConnectError("secret")}, "unconfirmed"),
    ({"error": ValueError("cookie=secret; token=secret")}, "failed"),
])
def test_network_outcomes_and_redaction(kwargs, state):
    result, message = classify_network(**kwargs)
    assert result == state
    assert "secret" not in message


def test_completed_is_not_a_pass_if_checks_missing():
    assert local_result([{"id": "data", "state": "passed"}, {"id": "host", "state": "skipped"}]) == "incomplete"
    assert local_result([{"id": "data", "state": "passed"}, {"id": "network", "state": "limited"}]) == "passed"
    assert local_result([{"id": "data", "state": "failed"}]) == "failed"


def test_cancel_races_do_not_stop_next_run(host):
    client, app = host
    first = client.post("/api/v1/diagnostics/self-tests", json={}).json()["id"]
    client.post(f"/api/v1/diagnostics/self-tests/{first}/cancel", json={})
    app.state.self_tests.thread.join(timeout=3)
    second = client.post("/api/v1/diagnostics/self-tests", json={}).json()["id"]
    assert second != first
    assert client.post(f"/api/v1/diagnostics/self-tests/{first}/cancel", json={}).status_code == 404
    assert app.state.self_tests.active()


@pytest.mark.skipif(os.name != "nt", reason="Windows diagnostic process ownership")
@pytest.mark.parametrize("cancel", [False, True])
def test_real_diagnostic_timeout_and_cancel_cleanup(tmp_path, cancel):
    manager = SelfTests(tmp_path, budget=0.05 if not cancel else 180)
    manager.start(False)
    if cancel:
        manager.cancel(manager.view()["id"])
    try:
        manager.thread.join(timeout=15)
        assert not manager.active()
        assert manager.view()["state"] == ("cancelled" if cancel else "timed_out")
        assert manager.view()["cleanup_complete"]
        assert not manager.workspace.exists()
    finally:
        manager.close()
