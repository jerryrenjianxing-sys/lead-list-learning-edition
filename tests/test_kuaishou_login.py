from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from tenacity import RetryError, stop_after_attempt

from media_platform.kuaishou.login import KuaishouLogin


@pytest.mark.asyncio
async def test_qrcode_login_clicks_through_page_overlay():
    login_button = SimpleNamespace(evaluate=AsyncMock())
    page = SimpleNamespace(locator=lambda _: login_button)
    login = KuaishouLogin(
        login_type="qrcode",
        browser_context=SimpleNamespace(),
        context_page=page,
    )
    login.check_login_state = AsyncMock(return_value=True)

    with (
        patch(
            "media_platform.kuaishou.login.utils.find_login_qrcode",
            new=AsyncMock(return_value="data:image/png;base64,test"),
        ),
        patch("media_platform.kuaishou.login.utils.show_qrcode"),
        patch("media_platform.kuaishou.login.asyncio.sleep", new=AsyncMock()),
    ):
        await login.login_by_qrcode()

    login_button.evaluate.assert_awaited_once_with("element => element.click()")


@pytest.mark.asyncio
async def test_qrcode_refresh_reloads_and_publishes_a_new_code():
    page = SimpleNamespace(reload=AsyncMock())
    login = KuaishouLogin(
        login_type="qrcode",
        browser_context=SimpleNamespace(cookies=AsyncMock(return_value=[])),
        context_page=page,
    )
    login._publish_qrcode = AsyncMock(return_value=True)

    with (
        patch(
            "media_platform.kuaishou.login.utils.consume_login_qrcode_refresh",
            return_value=True,
        ),
        pytest.raises(RetryError),
    ):
        await login.check_login_state.retry_with(stop=stop_after_attempt(1))(login)

    page.reload.assert_awaited_once()
    login._publish_qrcode.assert_awaited_once()
