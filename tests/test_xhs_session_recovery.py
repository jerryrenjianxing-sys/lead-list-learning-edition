import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from media_platform.xhs import client as xhs_client_module
from media_platform.xhs.client import XiaoHongShuClient
from media_platform.xhs.core import XiaoHongShuCrawler


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class _HttpClient:
    def __init__(self, responses):
        self.responses = responses
        self.request_count = 0

    async def request(self, *args, **kwargs):
        response = self.responses[self.request_count]
        self.request_count += 1
        return response


class _ClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
async def test_expired_session_refreshes_browser_cookie_before_retry(monkeypatch):
    http_client = _HttpClient(
        [
            _Response({"success": False, "code": -1, "msg": "登录已过期"}),
            _Response({"success": True, "data": {"items": ["ok"]}}),
        ]
    )
    monkeypatch.setattr(
        xhs_client_module,
        "make_async_client",
        lambda **_: _ClientContext(http_client),
    )

    client = XiaoHongShuClient(
        headers={},
        playwright_page=AsyncMock(),
        cookie_dict={},
    )
    client._pre_headers = AsyncMock(return_value={})
    client.session_refresh_callback = AsyncMock()

    result = await client.get("/api/test", {})

    assert result == {"items": ["ok"]}
    client.session_refresh_callback.assert_awaited_once()
    assert http_client.request_count == 2


@pytest.mark.asyncio
async def test_captcha_opens_browser_recovery_before_single_retry(monkeypatch):
    http_client = _HttpClient(
        [
            _Response({}, status_code=471),
            _Response({"success": True, "data": {"items": ["ok"]}}),
        ]
    )
    monkeypatch.setattr(
        xhs_client_module,
        "make_async_client",
        lambda **_: _ClientContext(http_client),
    )

    client = XiaoHongShuClient(
        headers={},
        playwright_page=AsyncMock(),
        cookie_dict={},
    )
    client._pre_headers = AsyncMock(return_value={})
    client.captcha_callback = AsyncMock()

    result = await client.get("/api/test", {})

    assert result == {"items": ["ok"]}
    client.captcha_callback.assert_awaited_once()
    assert http_client.request_count == 2


@pytest.mark.asyncio
async def test_session_refresh_reuses_visible_browser_and_existing_login(monkeypatch):
    crawler = XiaoHongShuCrawler()
    crawler.browser_context = AsyncMock()
    crawler.context_page = AsyncMock()
    crawler.xhs_client = AsyncMock()
    begin = AsyncMock()
    monkeypatch.setattr(
        "media_platform.xhs.core.XiaoHongShuLogin",
        lambda **_: SimpleNamespace(begin=begin),
    )

    await crawler.refresh_xhs_session()

    crawler.browser_context.clear_cookies.assert_awaited_once_with(name="web_session")
    crawler.context_page.goto.assert_awaited_once_with(crawler.index_url)
    crawler.context_page.bring_to_front.assert_awaited_once()
    begin.assert_awaited_once()
    crawler.xhs_client.update_cookies.assert_awaited_once_with(
        browser_context=crawler.browser_context,
        urls=crawler.cookie_urls,
    )


@pytest.mark.asyncio
async def test_captcha_waits_for_explicit_resume_signal(monkeypatch, tmp_path):
    real_sleep = asyncio.sleep

    async def yield_control(_seconds):
        await real_sleep(0)

    monkeypatch.setattr("media_platform.xhs.core.config.SAVE_DATA_PATH", str(tmp_path))
    monkeypatch.setattr("media_platform.xhs.core.asyncio.sleep", yield_control)
    crawler = XiaoHongShuCrawler()
    crawler.browser_context = AsyncMock()
    crawler.context_page = AsyncMock()
    crawler.context_page.content.return_value = "<html>normal page</html>"
    crawler.xhs_client = AsyncMock()

    recovery = asyncio.create_task(crawler.recover_xhs_captcha())
    marker = tmp_path / "xhs_verification_required"
    for _ in range(20):
        if marker.exists():
            break
        await real_sleep(0)

    assert marker.exists()
    assert not recovery.done()
    crawler.context_page.bring_to_front.assert_not_awaited()

    (tmp_path / "xhs_resume").touch()
    await asyncio.wait_for(recovery, timeout=1)

    crawler.context_page.bring_to_front.assert_awaited_once()
    crawler.context_page.goto.assert_not_awaited()
    assert not marker.exists()
    assert not (tmp_path / "xhs_resume").exists()
    crawler.xhs_client.update_cookies.assert_awaited_once_with(
        browser_context=crawler.browser_context,
        urls=crawler.cookie_urls,
    )


@pytest.mark.asyncio
async def test_profile_browser_uses_same_captcha_recovery_before_retry():
    page = AsyncMock()
    page.content.side_effect = ["请通过验证", "<html>profile</html>"]
    page.evaluate.return_value = {"basicInfo": {"redId": "public-id"}}
    client = XiaoHongShuClient(headers={}, playwright_page=page, cookie_dict={})
    client.captcha_callback = AsyncMock()

    profile = await client.get_creator_info_by_browser("buyer")

    assert profile["basicInfo"]["redId"] == "public-id"
    client.captcha_callback.assert_awaited_once()
    assert page.goto.await_count == 2
