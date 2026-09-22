import asyncio
from datetime import datetime, timedelta
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_login_qrcode_is_only_served_for_the_running_task(tmp_path, monkeypatch):
    crawler_router = import_module("api.routers.crawler")
    assert hasattr(crawler_router, "get_login_qrcode")

    qr_path = tmp_path / ".login_qrcode.png"
    qr_path.write_bytes(b"png")
    monkeypatch.setattr(crawler_router, "LOGIN_QRCODE_PATH", qr_path)
    manager = crawler_router.crawler_manager
    previous = (manager.status, manager.started_at)

    try:
        manager.status = "running"
        manager.started_at = datetime.now() - timedelta(seconds=1)
        response = asyncio.run(crawler_router.get_login_qrcode())
        assert Path(response.path) == qr_path
        assert response.media_type == "image/png"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-qr-version"] == str(qr_path.stat().st_mtime_ns)

        manager.status = "idle"
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(crawler_router.get_login_qrcode())
        assert exc_info.value.status_code == 404
    finally:
        manager.status, manager.started_at = previous


def test_refresh_login_qrcode_signals_the_running_kuaishou_task(
    tmp_path, monkeypatch
):
    crawler_router = import_module("api.routers.crawler")
    refresh_path = tmp_path / ".login_qrcode_refresh"
    monkeypatch.setattr(
        crawler_router, "LOGIN_QRCODE_REFRESH_PATH", refresh_path
    )
    manager = crawler_router.crawler_manager
    previous = (manager.status, manager.current_config)

    try:
        manager.status = "running"
        manager.current_config = SimpleNamespace(
            platform=SimpleNamespace(value="ks"),
            login_type=SimpleNamespace(value="qrcode"),
        )
        response = asyncio.run(crawler_router.refresh_login_qrcode())
        assert response["status"] == "ok"
        assert refresh_path.is_file()
    finally:
        manager.status, manager.current_config = previous
