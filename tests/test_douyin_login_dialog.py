import pytest
from playwright.async_api import async_playwright
from media_platform.douyin.login import DouYinLogin


@pytest.mark.asyncio
@pytest.mark.parametrize("dialog_id", ["login-panel-new", "login-full-panel-current"])
async def test_visible_dialog_does_not_click_covered_login_button(dialog_id):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(f'''<p onclick="window.clicked=true">登录</p>
            <div id="login-panel-new" style="display:none"></div>
            <div id="{dialog_id}" style="position:fixed;inset:0;background:white">扫码登录</div>''')
        login = DouYinLogin("qrcode", page.context, page)
        await login.popup_login_dialog()
        assert not await page.evaluate("Boolean(window.clicked)")
        await browser.close()


@pytest.mark.asyncio
async def test_only_qrcode_container_is_also_recognized():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content('<div id="animate_qrcode_container">二维码</div>')
        await DouYinLogin("qrcode", page.context, page).popup_login_dialog()
        await browser.close()
