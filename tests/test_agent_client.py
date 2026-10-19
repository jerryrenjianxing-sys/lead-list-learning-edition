"""The distributed client's public Interface against owned HTTP/ASGI adapters."""

import importlib.util
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from workbench.app import make_app
import uvicorn


SCRIPT = Path(__file__).resolve().parents[1] / "skills/media-workbench/scripts/workbench.py"
spec = importlib.util.spec_from_file_location("distributed_agent_client", SCRIPT)
client_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client_module)


def saved_receipt(discovery, request_id):
    reports = [json.loads(path.read_text(encoding="utf-8"))
               for path in (discovery.parent / "client-receipts").glob("*.json")]
    return next(report for report in reports if report["request_id"] == request_id)


class ASGITransport:
    """Transport Adapter; it leaves real middleware and routes intact."""

    def __init__(self, http):
        self.http = http
        self.paths = []

    def open(self, request, timeout=None):
        path = urlsplit(request.full_url).path
        self.paths.append(path)
        self.http.cookies.clear()
        response = self.http.request(
            request.method, path, content=request.data,
            headers=dict(request.header_items()),
        )
        if response.status_code >= 400:
            raise HTTPError(request.full_url, response.status_code, "rejected", {}, io.BytesIO(response.content))
        return io.BytesIO(response.content)


@pytest.fixture
def local_host(tmp_path):
    root = tmp_path / "data"
    app = make_app(root, run_queue=False)
    discovery = tmp_path / "instance.json"
    info = {
        "product": "MediaWorkbench", "version": app.version, "api_version": 1,
        "base_url": "http://127.0.0.1:12345", "token": app.state.token,
        "data_directory": str(root),
    }
    access = getattr(app.state, "agent_access", None)
    if access:
        info.update(instance_id=access.instance_id, workspace_id=access.workspace_id)
    discovery.write_text(json.dumps(info), encoding="utf-8")
    with TestClient(app) as http:
        yield app, discovery, info, ASGITransport(http)


def test_check_cannot_report_ready_with_invalid_credential(local_host):
    _, discovery, info, transport = local_host
    discovery.write_text(json.dumps({**info, "token": "synthetic-invalid-token"}), encoding="utf-8")
    client = client_module.Client(discovery)
    client.opener = transport
    with pytest.raises(Exception) as rejected:
        client.check()
    assert getattr(rejected.value, "code", None) == "AUTH_FAILED"


def test_unknown_write_recovers_receipt_after_same_workspace_restarts(local_host):
    app, discovery, info, first_transport = local_host
    root = Path(info["data_directory"])
    with ExitStack() as stack:
        class RestartingTransport:
            restarted = False
            post_count = 0

            def open(self, request, timeout=None):
                url = urlsplit(request.full_url)
                if self.restarted and url.port == 12345:
                    raise ConnectionResetError("old host is gone")
                transport = self.second if self.restarted else first_transport
                response = transport.open(request, timeout)
                if request.method == "POST":
                    self.post_count += 1
                if request.method == "POST" and not self.restarted:
                    self.restarted = True
                    new_app = make_app(root, run_queue=False)
                    self.second = ASGITransport(stack.enter_context(TestClient(new_app)))
                    access = new_app.state.agent_access
                    discovery.write_text(json.dumps({**info, "base_url": "http://127.0.0.1:12346",
                        "instance_id": access.instance_id, "workspace_id": access.workspace_id}), encoding="utf-8")
                    raise ConnectionResetError("response lost after durable commit")
                return response

        transport = RestartingTransport()
        client = client_module.Client(discovery)
        client.opener = transport
        result = client.request("POST", "/api/v1/datasets", json.dumps({"name": "自由研究", "records": []}).encode(), request_id="same-workspace-retry")
        # Replay is a second HTTP POST, but the durable request has one effect.
        assert transport.post_count == 2
        assert len(json.loads(client.send("GET", "/api/v1/datasets"))["items"]) == 1
        assert result["id"]
        receipt = saved_receipt(discovery, "same-workspace-retry")
        assert receipt["state"] == "completed"


def test_fresh_client_connects_from_arbitrary_cwd_without_diagnostics(tmp_path):
    app = make_app(tmp_path / "数据", run_queue=False)
    visited = []

    @app.middleware("http")
    async def trace(request, call_next):
        visited.append((request.method, request.url.path))
        return await call_next(request)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        until = time.monotonic() + 5
        while not server.started and time.monotonic() < until:
            time.sleep(0.01)
        assert server.started
        discovery = tmp_path / "实例.json"
        access = app.state.agent_access
        discovery.write_text(json.dumps({
            "product": "MediaWorkbench", "api_version": 1, "version": app.version,
            "base_url": f"http://127.0.0.1:{sock.getsockname()[1]}", "token": app.state.token,
            "data_directory": str(tmp_path / "数据"), "workspace_id": access.workspace_id,
            "instance_id": access.instance_id,
        }), encoding="utf-8")
        outside = tmp_path / "任意 cwd"
        outside.mkdir()
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        result = subprocess.run([sys.executable, str(SCRIPT), "--instance", str(discovery), "connect"],
                                cwd=outside, env=env, capture_output=True, encoding="utf-8", timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["connection"] == "ready"
        assert visited == [("GET", "/api/v1/agent/handshake")]
        assert app.state.token not in result.stdout + result.stderr
        # The complete distributed Skill must work without repo imports or PYTHONPATH.
        client = client_module.Client(discovery)
        archive = client.send("GET", "/api/v1/skill")
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            zipped.extractall(tmp_path / "全新 Skill")
        fresh = tmp_path / "全新 Skill/media-workbench/scripts/workbench.py"
        result = subprocess.run([sys.executable, str(fresh), "--instance", str(discovery), "check"],
                                cwd=outside, env=env, capture_output=True, encoding="utf-8", timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["capabilities"]["analysis"] == "user_defined"
        before_api = len(visited)
        result = subprocess.run([sys.executable, str(fresh), "--instance", str(discovery), "api", "GET", "/api/v1/datasets"],
                                cwd=outside, env=env, capture_output=True, encoding="utf-8", timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["items"] == []
        assert visited[before_api:] == [("GET", "/api/v1/agent/handshake"), ("GET", "/api/v1/datasets")]
        assert not any(method == "POST" for method, _ in visited)
        request_file = outside / "请求.json"
        request_file.write_text('{"name":"concurrent CLI","records":[]}', encoding="utf-8")
        command = [sys.executable, str(fresh), "--instance", str(discovery), "api", "POST",
                   "/api/v1/datasets", "--json-file", str(request_file), "--request-id", "two-real-agent-processes"]
        processes = [subprocess.Popen(command, cwd=outside, env=env, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, encoding="utf-8") for _ in range(2)]
        results = []
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=15)
                assert process.returncode == 0, stdout + stderr
                results.append(json.loads(stdout))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
        assert results[0]["id"] == results[1]["id"]
        assert len(json.loads(client.send("GET", "/api/v1/datasets"))["items"]) == 1
        saved = saved_receipt(discovery, "two-real-agent-processes")
        assert saved["state"] == "completed"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()


def test_same_request_id_cannot_change_content_type(local_host):
    _, discovery, _, transport = local_host
    client = client_module.Client(discovery)
    client.opener = transport
    body = b'{"name":"one","records":[]}'
    client.request("POST", "/api/v1/datasets", body, request_id="type-must-match")
    with pytest.raises(client_module.ClientError) as rejected:
        client.request("POST", "/api/v1/datasets", body, content_type="text/plain", request_id="type-must-match")
    assert rejected.value.code == "REQUEST_ID_CONFLICT"


def test_legacy_connection_verifies_protected_settings_and_preserves_check_fields(local_host):
    app, discovery, _, transport = local_host
    app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", None) != "/api/v1/agent/handshake"]
    client = client_module.Client(discovery)
    client.opener = transport
    assert client.connect()["connection_mode"] == "legacy"
    check = client.check()
    assert check["capabilities"]["exports"] == ["csv", "json", "xlsx", "docx", "md"]
    assert transport.paths == ["/api/v1/agent/handshake", "/api/v1/health", "/api/v1/capabilities", "/api/v1/settings"]


def test_unauthorized_handshake_never_falls_back_to_public_routes(local_host):
    _, discovery, info, transport = local_host
    discovery.write_text(json.dumps({**info, "token": "synthetic-bad"}), encoding="utf-8")
    client = client_module.Client(discovery)
    client.opener = transport
    with pytest.raises(client_module.ClientError) as rejected:
        client.connect()
    assert rejected.value.code == "AUTH_FAILED"
    assert set(transport.paths) == {"/api/v1/agent/handshake"}


def test_multiple_clients_share_one_durable_request_without_regressing_journal(local_host):
    _, discovery, _, transport = local_host

    def submit(_):
        client = client_module.Client(discovery)
        client.opener = transport
        return client.request("POST", "/api/v1/datasets", b'{"name":"shared","records":[]}', request_id="shared-by-agents")

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(submit, range(12)))
    assert len({result["id"] for result in results}) == 1
    client = client_module.Client(discovery)
    client.opener = transport
    assert len(json.loads(client.send("GET", "/api/v1/datasets"))["items"]) == 1
    saved = saved_receipt(discovery, "shared-by-agents")
    assert saved["state"] == "completed"
    assert saved["response"] == results[0]
    assert not list((discovery.parent / "client-receipts").glob("*.tmp"))


def test_completed_request_replay_never_reverts_to_pending(local_host):
    _, discovery, _, transport = local_host
    client = client_module.Client(discovery)
    client.opener = transport
    body = b'{"name":"saved","records":[]}'
    first = client.request("POST", "/api/v1/datasets", body, request_id="already-completed")

    class OfflineMutationTransport:
        def open(self, request, timeout=None):
            if request.method == "POST":
                raise ConnectionResetError("must not issue a completed mutation again")
            return transport.open(request, timeout)

    another = client_module.Client(discovery)
    another.opener = OfflineMutationTransport()
    assert another.request("POST", "/api/v1/datasets", body, request_id="already-completed") == first
    saved = saved_receipt(discovery, "already-completed")
    assert saved["state"] == "completed"


def test_unknown_write_never_moves_to_another_workspace(local_host, tmp_path):
    _, discovery, info, transport = local_host
    other = make_app(tmp_path / "other-data", run_queue=False)
    with TestClient(other) as http:
        other_transport = ASGITransport(http)

        class SwitchingTransport:
            post_count = 0

            def open(self, request, timeout=None):
                if self.post_count:
                    return other_transport.open(request, timeout)
                response = transport.open(request, timeout)
                if request.method == "POST":
                    self.post_count += 1
                    discovery.write_text(json.dumps({**info, "token": other.state.token,
                        "data_directory": str(tmp_path / "other-data"),
                        "instance_id": other.state.agent_access.instance_id,
                        "workspace_id": other.state.agent_access.workspace_id}), encoding="utf-8")
                    raise ConnectionResetError("first response lost")
                return response

        switching = SwitchingTransport()
        client = client_module.Client(discovery)
        client.opener = switching
        with pytest.raises(client_module.ClientError) as rejected:
            client.request("POST", "/api/v1/datasets", b'{"name":"stay-here","records":[]}', request_id="never-cross-workspaces")
        assert rejected.value.code == "WORKSPACE_MISMATCH"
        assert rejected.value.request_id == "never-cross-workspaces"
        assert switching.post_count == 1
        assert http.get("/api/v1/datasets", headers={"Authorization": "Bearer " + other.state.token}).json()["items"] == []


def test_unready_handshake_is_not_an_authorized_ready_result(local_host):
    _, discovery, _, transport = local_host

    class UnreadyResponseTransport:
        def open(self, request, timeout=None):
            result = transport.open(request, timeout)
            if urlsplit(request.full_url).path == "/api/v1/agent/handshake":
                data = json.load(result)
                data["status"] = "starting"
                return io.BytesIO(json.dumps(data).encode())
            return result

    client = client_module.Client(discovery)
    client.opener = UnreadyResponseTransport()
    with pytest.raises(client_module.ClientError) as rejected:
        client.connect()
    assert rejected.value.code == "HOST_NOT_READY"


def test_request_ids_preserve_case_and_accept_windows_reserved_names(local_host):
    _, discovery, _, transport = local_host
    client = client_module.Client(discovery)
    client.opener = transport
    results = []
    for name in ("Case-ID", "case-id", "CON", "COM1"):
        results.append(client.request("POST", "/api/v1/datasets",
            json.dumps({"name": name, "records": []}).encode(), request_id=name))
    assert len({result["id"] for result in results}) == 4
    for name in ("Case-ID", "case-id", "CON", "COM1"):
        assert saved_receipt(discovery, name)["state"] == "completed"


def test_auth_recovery_workspace_rejection_keeps_original_request_id(local_host, tmp_path):
    _, discovery, info, transport = local_host
    other = make_app(tmp_path / "another-workspace", run_queue=False)

    class UnauthorizedMutationTransport:
        def open(self, request, timeout=None):
            if request.method == "POST":
                discovery.write_text(json.dumps({**info,
                    "data_directory": str(tmp_path / "another-workspace"),
                    "workspace_id": other.state.agent_access.workspace_id}), encoding="utf-8")
                raise HTTPError(request.full_url, 401, "rejected", {}, io.BytesIO(b"{}"))
            return transport.open(request, timeout)

    client = client_module.Client(discovery)
    client.opener = UnauthorizedMutationTransport()
    with pytest.raises(client_module.ClientError) as rejected:
        client.request("POST", "/api/v1/datasets", b'{"name":"kept-id","records":[]}', request_id="auth-failed-original-id")
    assert rejected.value.code == "WORKSPACE_MISMATCH"
    assert rejected.value.request_id == "auth-failed-original-id"


def test_old_journal_is_read_only_when_its_exact_request_id_matches(local_host):
    _, discovery, info, transport = local_host
    body = b'{"name":"legacy","records":[]}'
    from urllib.request import Request
    response = json.load(transport.open(Request(info["base_url"] + "/api/v1/datasets", body,
        {"Authorization": "Bearer " + info["token"], "Content-Type": "application/json", "Idempotency-Key": "Legacy-ID"}, method="POST")))
    folder = discovery.parent / "client-receipts"
    folder.mkdir()
    legacy = {"request_id": "Legacy-ID", "method": "POST", "path": "/api/v1/datasets",
              "fingerprint": hashlib.sha256(b"POST/api/v1/datasets" + body).hexdigest(),
              "state": "completed", "response": response}
    old_file = folder / "Legacy-ID.json"
    old_file.write_text(json.dumps(legacy), encoding="utf-8")
    before = old_file.read_bytes()
    transport.paths.clear()
    client = client_module.Client(discovery)
    client.opener = transport
    assert client.request("POST", "/api/v1/datasets", body, request_id="Legacy-ID") == response
    assert transport.paths == ["/api/v1/agent/handshake", "/api/v1/requests/Legacy-ID", "/api/v1/datasets"]
    assert old_file.read_bytes() == before
    different = client.request("POST", "/api/v1/datasets", b'{"name":"different case","records":[]}', request_id="legacy-id")
    assert different["id"] != response["id"]
    assert old_file.read_bytes() == before


@pytest.mark.parametrize("legacy", [False, True])
def test_lost_conflict_response_cannot_accept_another_body_receipt(local_host, tmp_path, legacy):
    app, discovery, info, transport = local_host
    if legacy:
        app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", None) != "/api/v1/agent/handshake"]
    client_b = client_module.Client(discovery)
    client_b.opener = transport
    body_b = b'{"name":"belongs to B","records":[]}'
    first = client_b.request("POST", "/api/v1/datasets", body_b, request_id="colliding-manual-id")
    other_discovery = tmp_path / "another-agent" / "instance.json"
    other_discovery.parent.mkdir()
    other_discovery.write_text(json.dumps(info), encoding="utf-8")

    class LostConflictResponse:
        dropped = False

        def open(self, request, timeout=None):
            try:
                return transport.open(request, timeout)
            except HTTPError as exc:
                if request.method == "POST" and exc.code == 409 and not self.dropped:
                    self.dropped = True
                    raise ConnectionResetError("conflict response was lost") from exc
                raise

    client_a = client_module.Client(other_discovery)
    client_a.opener = LostConflictResponse()
    with pytest.raises(client_module.ClientError) as rejected:
        client_a.request("POST", "/api/v1/datasets", b'{"name":"belongs to A","records":[]}', request_id="colliding-manual-id")
    assert rejected.value.code == "CONFLICT"
    assert rejected.value.request_id == "colliding-manual-id"
    assert saved_receipt(other_discovery, "colliding-manual-id")["state"] == "pending"
    datasets = json.loads(client_b.send("GET", "/api/v1/datasets"))["items"]
    assert len(datasets) == 1
    assert datasets[0]["id"] == first["id"]
    assert datasets[0]["name"] == "belongs to B"

