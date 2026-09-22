# -*- coding: utf-8 -*-
import pytest
import config
from config import base_config
import importlib
import json
import os
import subprocess
import sys
import time
from io import StringIO
from pydantic import ValidationError
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from cmd_arg import parse_cmd
from api.schemas import (
    CrawlerStartRequest,
    PlatformEnum,
    LoginTypeEnum,
    CrawlerTypeEnum,
)
from api.services.crawler_manager import (
    CrawlerManager,
    CrawlerTaskCancelled,
    CrawlerTaskConflict,
)
from api.main import app


def test_login_only_job_builds_without_crawl_arguments():
    cm = CrawlerManager()
    request = CrawlerStartRequest(
        platform=PlatformEnum.XHS,
        crawler_type="login",
        keywords="must-not-run",
    )

    command = cm._build_command(request)

    assert command[:2] == [sys.executable, "main.py"]
    assert command[command.index("--type") + 1] == "login"
    assert "--keywords" not in command


def test_kuaishou_lead_task_writes_recovery_manifest(tmp_path):
    cm = CrawlerManager()
    cm._project_root = tmp_path
    request = CrawlerStartRequest(
        task_id="ks-manifest",
        platform=PlatformEnum.KUAISHOU,
        keywords="test",
        lead_mode=True,
    )

    cm._write_task_manifest(request, "running")

    manifest = json.loads(
        (tmp_path / "data" / "lead" / "ks-manifest" / "task.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["state"] == "running"
    assert manifest["config"]["platform"] == "ks"


@pytest.mark.asyncio
async def test_nonzero_crawler_exit_sets_error_status():
    cm = CrawlerManager()
    cm.status = "running"
    cm.process = SimpleNamespace(
        poll=lambda: 1,
        returncode=1,
        stdout=StringIO(""),
    )

    await cm._read_output()

    assert cm.status == "error"
    assert cm.error_message == "Crawler exited with code: 1"


@pytest.mark.asyncio
async def test_output_reader_failure_does_not_leave_running_status():
    class BrokenOutput:
        def readline(self):
            raise OSError("broken pipe")

    cm = CrawlerManager()
    cm.status = "running"
    cm.process = SimpleNamespace(
        poll=lambda: None,
        returncode=None,
        stdout=BrokenOutput(),
    )

    await cm._read_output()

    assert cm.status == "error"
    assert cm.error_message == "Error reading crawler output: broken pipe"


@pytest.mark.asyncio
async def test_xhs_recovery_signal_resumes_live_task(tmp_path):
    cm = CrawlerManager()
    cm._project_root = tmp_path
    cm.status = "running"
    cm.current_task_id = "xhs-live"
    cm.current_config = CrawlerStartRequest(
        task_id="xhs-live",
        platform=PlatformEnum.XHS,
        keywords="test",
        lead_mode=True,
    )
    cm.process = SimpleNamespace(pid=200, poll=lambda: None)
    cm._memory_snapshot = lambda: {}
    task_dir = tmp_path / "data" / "lead" / "xhs-live"
    task_dir.mkdir(parents=True)
    (task_dir / "xhs_verification_required").touch()

    status = cm.get_status()
    assert status["recovery_type"] == "xhs_verification"
    assert status["recovery_task_id"] == "xhs-live"

    assert await cm.recover_xhs("xhs-live") is True
    assert (task_dir / "xhs_resume").exists()


@pytest.mark.asyncio
async def test_xhs_recovery_restarts_same_checkpointed_task_after_api_restart(
    tmp_path, monkeypatch
):
    cm = CrawlerManager()
    cm._project_root = tmp_path
    task_dir = tmp_path / "data" / "lead" / "xhs-resume"
    task_dir.mkdir(parents=True)
    request = CrawlerStartRequest(
        task_id="xhs-resume",
        platform=PlatformEnum.XHS,
        keywords="包装袋",
        lead_mode=True,
    )
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "state": "running",
                "config": request.model_dump(mode="json", exclude={"cookies"}),
            }
        ),
        encoding="utf-8",
    )
    (task_dir / "xhs_verification_required").touch()
    start = AsyncMock(return_value=True)
    monkeypatch.setattr(cm, "start", start)

    status = cm.get_status()
    assert status["recovery_type"] == "xhs_resume"
    assert status["recovery_task_id"] == "xhs-resume"

    assert await cm.recover_xhs("xhs-resume") is True
    resumed = start.await_args.args[0]
    assert resumed.task_id == "xhs-resume"
    assert resumed.keywords == "包装袋"
    assert resumed.crawler_type == CrawlerTypeEnum.LOGIN
    assert resumed.cookies == ""


@pytest.mark.asyncio
async def test_profile_recovery_keeps_legacy_douyin_checkpoint(
    tmp_path, monkeypatch
):
    cm = CrawlerManager()
    cm._project_root = tmp_path
    task_dir = tmp_path / "data" / "lead" / "dy-resume"
    task_dir.mkdir(parents=True)
    request = CrawlerStartRequest(
        task_id="dy-resume",
        platform=PlatformEnum.DOUYIN,
        keywords="test",
        lead_mode=True,
    )
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "state": "error",
                "config": request.model_dump(mode="json", exclude={"cookies"}),
            }
        ),
        encoding="utf-8",
    )
    (task_dir / "douyin_profile_blocked").touch()
    start = AsyncMock(return_value=True)
    monkeypatch.setattr(cm, "start", start)

    status = cm.get_status()
    assert status["recovery_type"] == "profile_resume"
    assert status["recovery_task_id"] == "dy-resume"
    assert status["recovery_platform"] == "dy"
    assert status["recovery_reason"] == "risk_control"

    assert await cm.recover_profiles("dy-resume") is True
    resumed = start.await_args.args[0]
    assert resumed.task_id == "dy-resume"
    assert resumed.crawler_type == CrawlerTypeEnum.LOGIN


@pytest.mark.asyncio
async def test_profile_recovery_restarts_same_kuaishou_task(tmp_path, monkeypatch):
    cm = CrawlerManager()
    cm._project_root = tmp_path
    task_dir = tmp_path / "data" / "lead" / "ks-resume"
    task_dir.mkdir(parents=True)
    request = CrawlerStartRequest(
        task_id="ks-resume",
        platform=PlatformEnum.KUAISHOU,
        keywords="test",
        lead_mode=True,
    )
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "state": "error",
                "config": request.model_dump(mode="json", exclude={"cookies"}),
            }
        ),
        encoding="utf-8",
    )
    (task_dir / "profile_resume_required.json").write_text(
        json.dumps({"platform": "ks", "reason": "network_error"}),
        encoding="utf-8",
    )
    start = AsyncMock(return_value=True)
    monkeypatch.setattr(cm, "start", start)

    status = cm.get_status()
    assert status["recovery_type"] == "profile_resume"
    assert status["recovery_task_id"] == "ks-resume"
    assert status["recovery_platform"] == "ks"
    assert status["recovery_reason"] == "network_error"

    assert await cm.recover_profiles("ks-resume") is True
    resumed = start.await_args.args[0]
    assert resumed.task_id == "ks-resume"
    assert resumed.crawler_type == CrawlerTypeEnum.LOGIN


@pytest.mark.asyncio
async def test_profile_recovery_start_failure_keeps_checkpoint(tmp_path, monkeypatch):
    cm = CrawlerManager()
    cm._project_root = tmp_path
    task_dir = tmp_path / "data" / "lead" / "ks-resume-failed"
    task_dir.mkdir(parents=True)
    request = CrawlerStartRequest(
        task_id="ks-resume-failed",
        platform=PlatformEnum.KUAISHOU,
        keywords="test",
        lead_mode=True,
    )
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "state": "error",
                "config": request.model_dump(mode="json", exclude={"cookies"}),
            }
        ),
        encoding="utf-8",
    )
    marker = task_dir / "profile_resume_required.json"
    marker.write_text(
        json.dumps({"platform": "ks", "reason": "network_error"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cm, "start", AsyncMock(return_value=False))

    assert await cm.recover_profiles("ks-resume-failed") is False
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "platform": "ks",
        "reason": "network_error",
    }


def test_newer_douyin_recovery_is_not_hidden_by_old_xhs_task(tmp_path):
    cm = CrawlerManager()
    cm._project_root = tmp_path
    manifests = {}

    for task_id, platform, state in (
        ("xhs-old", PlatformEnum.XHS, "running"),
        ("dy-current", PlatformEnum.DOUYIN, "error"),
    ):
        task_dir = tmp_path / "data" / "lead" / task_id
        task_dir.mkdir(parents=True)
        request = CrawlerStartRequest(
            task_id=task_id,
            platform=platform,
            keywords="test",
            lead_mode=True,
        )
        manifest = task_dir / "task.json"
        manifest.write_text(
            json.dumps(
                {
                    "state": state,
                    "config": request.model_dump(mode="json", exclude={"cookies"}),
                }
            ),
            encoding="utf-8",
        )
        manifests[task_id] = manifest

    (tmp_path / "data" / "lead" / "dy-current" / "douyin_profile_blocked").touch()
    os.utime(manifests["xhs-old"], (1, 1))
    os.utime(manifests["dy-current"], (2, 2))

    status = cm.get_status()

    assert status["recovery_type"] == "profile_resume"
    assert status["recovery_task_id"] == "dy-current"
    assert status["recovery_platform"] == "dy"
    assert status["recovery_reason"] == "risk_control"


@pytest.mark.asyncio
async def test_cmd_arg_crawler_max_notes_count():
    # Store original values
    orig_notes = config.CRAWLER_MAX_NOTES_COUNT
    orig_comments = config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES
    orig_keywords = config.KEYWORDS

    try:
        assert base_config.CRAWLER_MAX_NOTES_COUNT == 0
        assert base_config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES == 0
        await parse_cmd([
            "--platform", "xhs",
            "--keywords", "test",
            "--crawler_max_notes_count", "42",
            "--max_comments_count_singlenotes", "24"
        ])
        assert config.CRAWLER_MAX_NOTES_COUNT == 42
        assert config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES == 24
        await parse_cmd([
            "--platform", "xhs",
            "--keywords", "test",
            "--crawler_max_notes_count", "0",
            "--max_comments_count_singlenotes", "0"
        ])
        assert config.CRAWLER_MAX_NOTES_COUNT == 0
        assert config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES == 0
    finally:
        config.CRAWLER_MAX_NOTES_COUNT = orig_notes
        config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = orig_comments
        config.KEYWORDS = orig_keywords

def test_crawler_manager_build_command():
    cm = CrawlerManager()

    # 1. No max limits passed in API request
    req1 = CrawlerStartRequest(
        platform=PlatformEnum.XHS,
        login_type=LoginTypeEnum.QRCODE,
        crawler_type=CrawlerTypeEnum.SEARCH,
        keywords="test",
        max_notes_count=None,
        max_comments_count=None
    )
    cmd1 = cm._build_command(req1)
    # Check that the custom arguments are NOT present
    assert "--crawler_max_notes_count" not in cmd1
    assert "--max_comments_count_singlenotes" not in cmd1

    # 2. Both limits passed in API request
    req2 = CrawlerStartRequest(
        platform=PlatformEnum.XHS,
        login_type=LoginTypeEnum.QRCODE,
        crawler_type=CrawlerTypeEnum.SEARCH,
        keywords="test",
        max_notes_count=50,
        max_comments_count=5
    )
    cmd2 = cm._build_command(req2)
    # Check that they are correctly added
    assert "--crawler_max_notes_count" in cmd2
    idx_notes = cmd2.index("--crawler_max_notes_count")
    assert cmd2[idx_notes + 1] == "50"

    assert "--max_comments_count_singlenotes" in cmd2
    idx_comments = cmd2.index("--max_comments_count_singlenotes")
    assert cmd2[idx_comments + 1] == "5"

    req3 = CrawlerStartRequest(
        platform=PlatformEnum.XHS,
        login_type=LoginTypeEnum.QRCODE,
        crawler_type=CrawlerTypeEnum.SEARCH,
        keywords="test",
        max_notes_count=0,
        max_comments_count=0,
    )
    cmd3 = cm._build_command(req3)
    assert cmd3[cmd3.index("--crawler_max_notes_count") + 1] == "0"
    assert cmd3[cmd3.index("--max_comments_count_singlenotes") + 1] == "0"


def test_zero_means_unlimited_for_notes_comments_and_profiles():
    request = CrawlerStartRequest(
        platform="dy",
        login_type="qrcode",
        crawler_type="search",
        keywords="雨衣",
        max_notes_count=0,
        max_comments_count=0,
        lead_max_accounts=0,
    )
    assert request.max_notes_count == 0
    assert request.max_comments_count == 0
    assert request.lead_max_accounts == 0


def test_crawler_status_reports_private_process_tree_memory_without_rss_double_count(monkeypatch):
    cm = CrawlerManager()
    cm.process = SimpleNamespace(pid=200, poll=lambda: None)
    crawler_manager_module = importlib.import_module("api.services.crawler_manager")

    api_process = SimpleNamespace(
        pid=100,
        name=lambda: "api-python",
        memory_info=lambda: SimpleNamespace(rss=10_000),
        memory_full_info=lambda: SimpleNamespace(uss=100),
    )
    crawler_process = SimpleNamespace(
        pid=200,
        name=lambda: "uv",
        memory_info=lambda: SimpleNamespace(rss=20_000),
        memory_full_info=lambda: SimpleNamespace(uss=200),
        children=lambda recursive: [worker_process],
    )
    worker_process = SimpleNamespace(
        pid=201,
        name=lambda: "python",
        memory_info=lambda: SimpleNamespace(rss=30_000),
        memory_full_info=lambda: SimpleNamespace(uss=300),
    )

    monkeypatch.setattr(crawler_manager_module.os, "getpid", lambda: 100)
    monkeypatch.setattr(
        crawler_manager_module,
        "psutil",
        SimpleNamespace(Process=lambda pid: api_process if pid == 100 else crawler_process),
        raising=False,
    )

    status = cm.get_status()

    assert status["api_process_id"] == 100
    assert status["crawler_process_id"] == 200
    assert status["memory_metric"] == "uss"
    assert status["api_private_memory_bytes"] == 100
    assert status["crawler_tree_private_memory_bytes"] == 500
    assert status["total_private_memory_bytes"] == 600
    assert status["peak_total_private_memory_bytes"] == 600
    assert status["largest_process_name"] == "python"
    assert status["largest_process_id"] == 201
    assert status["largest_process_private_memory_bytes"] == 300


@pytest.mark.asyncio
async def test_stop_terminates_the_crawler_process_tree():
    cm = CrawlerManager()
    cm.status = "running"
    cm.current_task_id = "task-a"
    cm.process = SimpleNamespace(pid=200, poll=lambda: None)
    cm._terminate_process_tree = AsyncMock(return_value=[])

    assert await cm.stop("task-a") is True
    cm._terminate_process_tree.assert_awaited_once_with(200)
    assert cm.status == "idle"


@pytest.mark.asyncio
async def test_stop_rejects_a_different_task_identity():
    cm = CrawlerManager()
    cm.status = "running"
    cm.current_task_id = "task-a"
    cm.process = SimpleNamespace(pid=200, poll=lambda: None)
    cm._terminate_process_tree = AsyncMock(return_value=[])

    with pytest.raises(CrawlerTaskConflict):
        await cm.stop("task-b")

    cm._terminate_process_tree.assert_not_awaited()
    assert cm.status == "running"


@pytest.mark.asyncio
async def test_stop_before_start_cancels_pending_task_without_spawning():
    cm = CrawlerManager()
    cm.current_task_id = "task-a"
    cm.process = SimpleNamespace(poll=lambda: 0)
    request = CrawlerStartRequest(
        task_id="task-b",
        platform=PlatformEnum.XHS,
        keywords="test",
    )

    assert await cm.stop("task-b") is True

    with patch("api.services.crawler_manager.subprocess.Popen") as mock_popen:
        with pytest.raises(CrawlerTaskCancelled):
            await cm.start(request)

    mock_popen.assert_not_called()
    assert "task-b" not in cm._cancelled_task_ids


@pytest.mark.asyncio
async def test_start_keeps_log_ids_monotonic_across_queue_jobs():
    cm = CrawlerManager()
    cm._log_id = 12
    request = CrawlerStartRequest(
        task_id="task-b",
        platform=PlatformEnum.XHS,
        keywords="test",
    )

    with patch(
        "api.services.crawler_manager.subprocess.Popen",
        side_effect=OSError("spawn failed"),
    ):
        assert await cm.start(request) is False

    assert [entry.id for entry in cm._logs] == [13, 14]


@pytest.mark.asyncio
async def test_stop_reports_error_when_processes_survive_forced_kill():
    cm = CrawlerManager()
    cm.status = "running"
    cm.current_task_id = "task-a"
    cm.process = SimpleNamespace(pid=200, poll=lambda: None)
    cm._terminate_process_tree = AsyncMock(return_value=[201])

    assert await cm.stop("task-a") is False
    assert cm.status == "error"
    assert cm.current_task_id == "task-a"
    assert "201" in cm.error_message


@pytest.mark.asyncio
async def test_terminate_process_tree_stops_real_child_and_grandchild():
    grandchild_code = "import time; time.sleep(60)"
    child_code = (
        "import subprocess,sys,time;"
        f"p=subprocess.Popen([sys.executable,'-c',{grandchild_code!r}]);"
        "print(p.pid,flush=True);time.sleep(60)"
    )
    root_code = (
        "import subprocess,sys,time;"
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}],"
        "stdout=subprocess.PIPE,text=True);"
        "g=int(p.stdout.readline());print(f'{p.pid} {g}',flush=True);time.sleep(60)"
    )
    root = subprocess.Popen(
        [sys.executable, "-c", root_code],
        stdout=subprocess.PIPE,
        text=True,
    )
    child_pid, grandchild_pid = map(int, root.stdout.readline().split())
    manager = CrawlerManager()

    try:
        survivors = await manager._terminate_process_tree(root.pid)
        assert survivors == []
        for _ in range(50):
            if all(
                not importlib.import_module("psutil").pid_exists(pid)
                for pid in (root.pid, child_pid, grandchild_pid)
            ):
                break
            time.sleep(0.02)
        assert all(
            not importlib.import_module("psutil").pid_exists(pid)
            for pid in (root.pid, child_pid, grandchild_pid)
        )
    finally:
        for pid in (grandchild_pid, child_pid, root.pid):
            try:
                importlib.import_module("psutil").Process(pid).kill()
            except importlib.import_module("psutil").Error:
                pass


@pytest.mark.asyncio
async def test_terminate_process_tree_orders_descendants_before_root(monkeypatch):
    events = []

    class FakeProcess:
        def __init__(self, pid, children=None):
            self.pid = pid
            self._children = children or []

        def children(self):
            return self._children

        def terminate(self):
            events.append(self.pid)

    grandchild = FakeProcess(3)
    child = FakeProcess(2, [grandchild])
    root = FakeProcess(1, [child])
    crawler_manager_module = importlib.import_module("api.services.crawler_manager")
    fake_psutil = SimpleNamespace(
        Process=lambda pid: root,
        Error=Exception,
        wait_procs=lambda processes, timeout: (processes, []),
        STATUS_ZOMBIE="zombie",
    )
    monkeypatch.setattr(crawler_manager_module, "psutil", fake_psutil)

    assert await CrawlerManager()._terminate_process_tree(1) == []
    assert events == [3, 2, 1]


@pytest.mark.asyncio
async def test_terminate_process_tree_returns_pids_that_survive_kill(monkeypatch):
    waits = 0

    class FakeProcess:
        pid = 7

        def children(self):
            return []

        def terminate(self):
            pass

        def kill(self):
            pass

        def is_running(self):
            return True

        def status(self):
            return "running"

    process = FakeProcess()

    def wait_procs(processes, timeout):
        nonlocal waits
        waits += 1
        return [], [process]

    crawler_manager_module = importlib.import_module("api.services.crawler_manager")
    fake_psutil = SimpleNamespace(
        Process=lambda pid: process,
        Error=Exception,
        wait_procs=wait_procs,
        STATUS_ZOMBIE="zombie",
    )
    monkeypatch.setattr(crawler_manager_module, "psutil", fake_psutil)

    assert await CrawlerManager()._terminate_process_tree(7) == [7]
    assert waits == 2


@pytest.mark.asyncio
async def test_log_queue_is_bounded_when_no_websocket_is_connected():
    cm = CrawlerManager()
    queue = cm.get_log_queue()

    for index in range(700):
        await cm._push_log(cm._create_log_entry(f"log-{index}"))

    assert queue.maxsize == 500
    assert queue.qsize() == 500
    assert (await queue.get()).message == "log-200"



def test_crawler_manager_disables_media_for_legacy_requests():
    cm = CrawlerManager()

    req_off = CrawlerStartRequest(
        platform=PlatformEnum.XHS,
        login_type=LoginTypeEnum.QRCODE,
        crawler_type=CrawlerTypeEnum.DETAIL,
        specified_ids="note-1",
    )
    cmd_off = cm._build_command(req_off)
    idx_off = cmd_off.index("--get_media")
    assert cmd_off[idx_off + 1] == "false"

    req_on = CrawlerStartRequest(
        platform=PlatformEnum.XHS,
        login_type=LoginTypeEnum.QRCODE,
        crawler_type=CrawlerTypeEnum.DETAIL,
        specified_ids="note-1",
        enable_media=True,
    )
    cmd_on = cm._build_command(req_on)
    idx_on = cmd_on.index("--get_media")
    assert cmd_on[idx_on + 1] == "false"


def test_api_schema_omits_retired_media_switch():
    assert "enable_media" not in CrawlerStartRequest.model_json_schema()["properties"]

def test_api_start_crawler_with_limits():
    client = TestClient(app)

    with patch("api.routers.crawler.crawler_manager.start", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = True

        # Test case 1: with limits
        response = client.post("/api/crawler/start", json={
            "task_id": "task-limits",
            "platform": "xhs",
            "login_type": "qrcode",
            "crawler_type": "search",
            "keywords": "test",
            "max_notes_count": 50,
            "max_comments_count": 5
        })

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "message": "Crawler started successfully",
            "task_id": "task-limits",
        }

        mock_start.assert_called_once()
        called_request = mock_start.call_args[0][0]
        assert called_request.platform == PlatformEnum.XHS
        assert called_request.max_notes_count == 50
        assert called_request.max_comments_count == 5
        assert called_request.task_id == "task-limits"

def test_api_start_crawler_without_limits():
    client = TestClient(app)

    with patch("api.routers.crawler.crawler_manager.start", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = True

        # Test case 2: without limits
        response = client.post("/api/crawler/start", json={
            "task_id": "task-defaults",
            "platform": "xhs",
            "login_type": "qrcode",
            "crawler_type": "search",
            "keywords": "test"
        })

        assert response.status_code == 200
        mock_start.assert_called_once()
        called_request = mock_start.call_args[0][0]
        assert called_request.platform == PlatformEnum.XHS
        assert called_request.max_notes_count is None
        assert called_request.max_comments_count is None
        assert called_request.task_id == "task-defaults"


def test_api_stop_and_status_reject_a_different_task_identity():
    client = TestClient(app)
    crawler_manager = importlib.import_module("api.routers.crawler").crawler_manager
    running_process = SimpleNamespace(poll=lambda: None)

    with (
        patch.object(
            crawler_manager,
            "current_task_id",
            "task-a",
        ),
        patch.object(crawler_manager, "process", running_process),
        patch(
            "api.routers.crawler.crawler_manager.stop",
            new_callable=AsyncMock,
        ) as mock_stop,
    ):
        stop_response = client.post("/api/crawler/stop", json={"task_id": "task-b"})
        status_response = client.get(
            "/api/crawler/status",
            params={"task_id": "task-b"},
        )

    assert stop_response.status_code == 409
    assert status_response.status_code == 409
    mock_stop.assert_not_awaited()


def test_api_stop_delegates_pending_task_without_active_process():
    client = TestClient(app)
    crawler_manager = importlib.import_module("api.routers.crawler").crawler_manager
    completed_process = SimpleNamespace(poll=lambda: 0)

    with (
        patch.object(crawler_manager, "current_task_id", "task-a"),
        patch.object(crawler_manager, "process", completed_process),
        patch(
            "api.routers.crawler.crawler_manager.stop",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_stop,
    ):
        response = client.post("/api/crawler/stop", json={"task_id": "task-b"})

    assert response.status_code == 200
    mock_stop.assert_awaited_once_with("task-b")


def test_api_start_reports_cancelled_task_as_conflict():
    client = TestClient(app)

    with patch(
        "api.routers.crawler.crawler_manager.start",
        new_callable=AsyncMock,
        side_effect=CrawlerTaskCancelled("Crawler task task-b was cancelled"),
    ):
        response = client.post(
            "/api/crawler/start",
            json={"task_id": "task-b", "platform": "xhs", "keywords": "test"},
        )

    assert response.status_code == 409


def test_api_start_conflict_and_matching_stop_use_task_identity():
    client = TestClient(app)
    crawler_manager = importlib.import_module("api.routers.crawler").crawler_manager
    running_process = SimpleNamespace(poll=lambda: None)

    with (
        patch.object(crawler_manager, "current_task_id", "task-a"),
        patch.object(crawler_manager, "process", running_process),
        patch(
            "api.routers.crawler.crawler_manager.start",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
            conflict = client.post(
                "/api/crawler/start",
                json={"task_id": "task-b", "platform": "xhs", "keywords": "test"},
            )

    with (
        patch.object(crawler_manager, "current_task_id", "task-a"),
        patch(
            "api.routers.crawler.crawler_manager.stop",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_stop,
    ):
        stopped = client.post("/api/crawler/stop", json={"task_id": "task-a"})

    assert conflict.status_code == 409
    assert stopped.status_code == 200
    assert stopped.json()["task_id"] == "task-a"
    mock_stop.assert_awaited_once_with("task-a")


def test_health_exposes_lead_demo_version_and_safety_capabilities():
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    lead_demo = response.json()["lead_demo"]
    assert lead_demo["version"]
    assert lead_demo["capabilities"] == {
        "task_identity": True,
        "private_memory": True,
        "process_tree_stop": True,
    }


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("max_notes_count", -1),
        ("max_notes_count", 10001),
        ("max_comments_count", -1),
        ("max_comments_count", 10001),
        ("lead_max_accounts", -1),
    ],
)
def test_api_rejects_invalid_limits(field_name, value):
    client = TestClient(app)
    payload = {
        "platform": "xhs",
        "login_type": "qrcode",
        "crawler_type": "search",
        "keywords": "test",
        field_name: value,
    }

    with patch("api.routers.crawler.crawler_manager.start", new_callable=AsyncMock) as mock_start:
        response = client.post("/api/crawler/start", json=payload)

    assert response.status_code == 422
    mock_start.assert_not_called()


def test_lead_api_keeps_comment_options_limits_explicit_output_and_redacted_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIACRAWLER_ALLOWED_RUN_ROOT", str(tmp_path))
    cm = CrawlerManager()
    req = CrawlerStartRequest(
        platform=PlatformEnum.XHS,
        login_type=LoginTypeEnum.COOKIE,
        crawler_type=CrawlerTypeEnum.SEARCH,
        keywords="test",
        cookies="top-secret-cookie",
        save_data_path=str(tmp_path),
        lead_mode=True,
        lead_max_accounts=12,
        lead_deep_profile_limit=4,
        enable_comments=False,
        enable_sub_comments=False,
        max_notes_count=7,
        max_comments_count=42,
    )

    assert req.save_data_path == str(tmp_path)
    assert req.lead_mode is True
    assert req.enable_sub_comments is False
    assert req.lead_max_accounts == 12
    assert req.lead_deep_profile_limit == 4
    cmd = cm._build_command(req)
    assert cmd[cmd.index("--save_data_path") + 1] == str(tmp_path)
    assert cmd[cmd.index("--get_comment") + 1] == "false"
    assert cmd[cmd.index("--get_sub_comment") + 1] == "false"
    assert cmd[cmd.index("--crawler_max_notes_count") + 1] == "7"
    assert cmd[cmd.index("--max_comments_count_singlenotes") + 1] == "42"

    env = cm._build_env(req)
    assert env["MEDIACRAWLER_LEAD_MODE"] == "1"
    assert env["MEDIACRAWLER_LEAD_MAX_ACCOUNTS"] == "12"
    assert env["MEDIACRAWLER_LEAD_DEEP_PROFILE_LIMIT"] == "30"
    assert env["MEDIACRAWLER_LEAD_MAX_COMMENTS"] == "300"
    summary = cm._command_summary(req)
    assert "top-secret-cookie" not in summary
    assert "cookie" not in summary.lower()


def test_lead_api_uses_a_task_specific_default_output_directory():
    cm = CrawlerManager()
    req = CrawlerStartRequest(
        task_id="task-isolated-output",
        platform=PlatformEnum.XHS,
        keywords="test",
        lead_mode=True,
    )

    cmd = cm._build_command(req)

    assert cmd[cmd.index("--save_data_path") + 1] == str(
        cm._project_root / "data" / "lead" / req.task_id
    )
    assert "data/lead/task-isolated-output" in cm._command_summary(req)


def test_lead_api_defaults_and_cdp_uses_own_profile():
    req = CrawlerStartRequest(
        platform=PlatformEnum.XHS,
        crawler_type=CrawlerTypeEnum.SEARCH,
        keywords="test",
    )
    assert req.lead_mode is False
    assert req.lead_max_accounts == 0
    assert req.lead_deep_profile_limit == 30
    assert config.CDP_CONNECT_EXISTING is False


@pytest.mark.parametrize(
    ("crawler_type", "field_name"),
    [
        (CrawlerTypeEnum.SEARCH, "keywords"),
        (CrawlerTypeEnum.DETAIL, "specified_ids"),
        (CrawlerTypeEnum.CREATOR, "creator_ids"),
    ],
)
def test_crawl_modes_reject_empty_required_input(crawler_type, field_name):
    values = {
        "platform": PlatformEnum.DOUYIN,
        "crawler_type": crawler_type,
        field_name: " ， , ",
    }

    with pytest.raises(ValueError):
        CrawlerStartRequest(**values)


def test_login_only_mode_does_not_require_crawl_input():
    request = CrawlerStartRequest(
        platform=PlatformEnum.DOUYIN,
        crawler_type=CrawlerTypeEnum.LOGIN,
    )

    assert request.crawler_type is CrawlerTypeEnum.LOGIN


def test_api_allows_lead_on_three_supported_platforms_and_rejects_unsafe_paths(monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("MEDIACRAWLER_ALLOWED_RUN_ROOT", str(allowed))

    for platform in (
        PlatformEnum.XHS,
        PlatformEnum.DOUYIN,
        PlatformEnum.KUAISHOU,
    ):
        request = CrawlerStartRequest(
            platform=platform,
            keywords="test",
            lead_mode=True,
            save_data_path=str(allowed / platform.value),
        )
        assert request.enable_sub_comments is False
    with pytest.raises(ValidationError):
        CrawlerStartRequest(
            platform=PlatformEnum.BILIBILI,
            lead_mode=True,
            save_data_path=str(allowed / "bili"),
        )
    with pytest.raises(ValidationError):
        CrawlerStartRequest(
            platform=PlatformEnum.XHS,
            lead_mode=False,
            save_data_path=str(allowed / "ordinary"),
        )
    with pytest.raises(ValidationError):
        CrawlerStartRequest(
            platform=PlatformEnum.XHS,
            lead_mode=True,
            save_data_path=str(tmp_path / "outside"),
        )


def test_api_stdout_log_sanitizer_removes_verification_and_route_secrets():
    line = (
        "ERROR CAPTCHA Verifyuuid: captcha-uuid "
        "xsec_token=route-secret&safe=1 xsec_source=pc_search session=session-secret "
        "cookie=cookie-secret Response: raw-response-secret"
    )

    sanitized = CrawlerManager._sanitize_log_line(line)
    sanitized_json = CrawlerManager._sanitize_log_line(
        '{"xsec":"json-route-secret","cookie":"json-cookie-secret","safe":"ok"}'
    )

    assert "CAPTCHA" in sanitized
    assert "safe=1" in sanitized
    assert all(
        secret not in output
        for output in (sanitized, sanitized_json)
        for secret in (
            "captcha-uuid",
            "route-secret",
            "pc_search",
            "session-secret",
            "cookie-secret",
            "raw-response-secret",
            "json-route-secret",
            "json-cookie-secret",
        )
    )


def test_windows_viewer_warning_is_not_misclassified_as_an_error():
    line = "MediaCrawler WARNING system image viewer unavailable: WinError 1155"

    assert CrawlerManager()._parse_log_level(line) == "warning"
