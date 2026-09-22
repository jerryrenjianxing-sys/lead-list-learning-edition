"""Exercise the shipped frontend with a real Chromium browser and synthetic data."""

import json
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
instance = json.loads((root / ".cache/smoke-instance.json").read_text(encoding="utf-8"))
out = root / ".cache/screenshots"
out.mkdir(parents=True, exist_ok=True)
errors = []
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(
        viewport={"width": 1280, "height": 900}, device_scale_factor=1
    )
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(
        "Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => { window.copiedConnection = text; }}})"
    )
    page.goto(instance["base_url"])
    page.get_by_role("heading", name="Media Deep Researcher.", exact=True).wait_for()
    page.get_by_role("button", name="复制 Skill", exact=True).click()
    page.get_by_role("button", name="已复制", exact=True).wait_for()
    assert "/api/v1/skill" in page.evaluate("window.copiedConnection")
    page.screenshot(path=str(out / "home-light.png"), full_page=True)
    page.get_by_role("button", name="切换到深色主题").click()
    page.locator('.skill-theme-transition').wait_for(state="hidden")
    page.screenshot(path=str(out / "home-dark.png"), full_page=True)
    page.get_by_role("button", name="切换到浅色主题").click()
    page.locator('.skill-theme-transition').wait_for(state="hidden")
    page.get_by_role("link", name="任务台", exact=True).click()
    page.get_by_role("button", name="加载演示数据").click()
    page.get_by_role("heading", name="数据预览").wait_for()
    page.get_by_label("任务名称", exact=True).fill("用户自定义体验分析")
    page.get_by_label("分析目标", exact=True).fill(
        "自行设计字段，归纳通勤反馈中的需求与证据。"
    )
    page.get_by_role("button", name="保存分析任务").click()
    page.get_by_role("button", name="导出 XLSX").wait_for()
    with page.expect_download() as download:
        page.get_by_role("button", name="导出 XLSX").click()
    assert download.value.suggested_filename.endswith(".xlsx")
    page.screenshot(path=str(out / "analysis.png"), full_page=True)
    page.reload()
    page.get_by_role("button", name="分析成果", exact=True).click()
    page.get_by_role("button", name="用户自定义体验分析", exact=False).first.wait_for()
    page.get_by_role("button", name="平台登录", exact=True).click()
    page.get_by_role("heading", name="小红书", exact=True).wait_for()
    page.screenshot(path=str(out / "platforms.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("link", name="Media Deep Researcher", exact=True).click()
    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
    page.screenshot(path=str(out / "home-mobile.png"), full_page=True)
    browser.close()
assert not errors, errors
print(
    json.dumps(
        {
            "ok": True,
            "screenshots": str(out),
            "checks": [
                "copy-connection-instructions",
                "theme-switch",
                "demo-import",
                "custom-analysis",
                "xlsx-download",
                "reload-persistence",
                "seven-platform-ui",
                "mobile-width",
            ],
            "page_errors": errors,
        }
    )
)
