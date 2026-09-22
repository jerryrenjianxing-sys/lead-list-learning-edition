from unittest.mock import AsyncMock

import pytest

from media_platform.xhs.client import XiaoHongShuClient


@pytest.mark.asyncio
async def test_get_creator_info_by_browser_reads_vue_ref_value():
    page = AsyncMock()
    page.evaluate.return_value = {
        "basicInfo": {"nickname": "公开昵称", "desc": "公开简介"},
        "interactions": [{"type": "fans", "count": "1"}],
    }
    client = XiaoHongShuClient(headers={}, playwright_page=page, cookie_dict={})

    result = await client.get_creator_info_by_browser(
        user_id="user-1",
        xsec_token="token-1",
        xsec_source="pc_feed",
    )

    assert result["basicInfo"]["nickname"] == "公开昵称"
    page.goto.assert_awaited_once_with(
        "https://www.xiaohongshu.com/user/profile/user-1?xsec_token=token-1&xsec_source=pc_feed",
        wait_until="domcontentloaded",
    )
    page.wait_for_function.assert_awaited_once()
