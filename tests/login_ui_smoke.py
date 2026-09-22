"""Built-UI checks with simulated API responses; never uses real account sessions."""

import asyncio
import functools
import json
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".cache/login-review"
OUT.mkdir(parents=True, exist_ok=True)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


async def main():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        functools.partial(QuietHandler, directory=str(ROOT / "api/webui")),
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    names = dict(
        xhs="小红书",
        dy="抖音",
        ks="快手",
        bili="哔哩哔哩",
        wb="微博",
        tieba="贴吧",
        zhihu="知乎",
    )
    platforms = [
        dict(
            id=id, name=name, login_state="not_checked", saved_session=False, login=None
        )
        for id, name in names.items()
    ]
    errors, submissions, session_actions = [], [], []
    gate = asyncio.Event()
    stamp = datetime.now(timezone.utc).isoformat()
    checks = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 950})
        page.on("pageerror", lambda e: errors.append(str(e)))

        async def api(route):
            path = route.request.url.split("/api/v1", 1)[1]
            if path.startswith("/platforms/") and route.request.method == "POST":
                session_actions.append(path)
                p = next(p for p in platforms if p["id"] == path.split("/")[2])
                if path.endswith("/forget"):
                    p.update(
                        login_state="not_checked",
                        can_select=False,
                        account={},
                        saved_session=False,
                        session_status="not_saved",
                        saved_at=None,
                        last_check={},
                        login=None,
                    )
                    return await route.fulfill(json={"forgotten": True})
                return await route.fulfill(
                    json={"id": "check-" + p["id"], "state": "queued"}
                )
            if path == "/jobs" and route.request.method == "POST":
                submissions.append(route.request.post_data_json)
                p = next(p for p in platforms if p["id"] == submissions[-1]["platform"])
                p["login"] = dict(
                    job_id="login-" + p["id"],
                    phase="queued",
                    active=True,
                    task_state="queued",
                    started=stamp,
                )
                await gate.wait()
                return await route.fulfill(
                    json={"id": p["login"]["job_id"], "state": "queued"}
                )
            body = (
                {"token": "test-only"}
                if path == "/session"
                else {"items": platforms if path == "/platforms" else []}
            )
            await route.fulfill(json=body)

        await page.route("**/api/v1/**", api)
        await page.goto(f"http://127.0.0.1:{server.server_port}/#workbench")
        if await page.get_by_role("link", name="任务台", exact=True).count():
            await page.get_by_role("link", name="任务台", exact=True).click()
        await page.get_by_role("button", name="采集任务", exact=True).click()
        boxes = page.locator(".wb-platform-select input")
        await expect(boxes).to_have_count(7)
        assert await boxes.evaluate_all("xs=>xs.every(x=>!x.checked && x.disabled)")
        checks.append("fresh_install_none_selected")
        await (
            page.locator(".wb-platform-choice")
            .first.get_by_role("button", name="去登录")
            .click()
        )
        await expect(page.locator("#login-xhs")).to_be_focused()
        card = page.locator("#login-xhs")
        await card.get_by_role("button", name="打开登录", exact=True).evaluate(
            "b=>{b.click();b.click()}"
        )
        await expect(card.get_by_role("status")).to_contain_text("正在提交登录请求")
        assert len(submissions) == 1
        assert set(submissions[0]) == {"platform", "crawler_type"}
        await expect(
            page.locator("#login-dy").get_by_role("button", name="打开登录")
        ).to_be_enabled()
        gate.set()
        await expect(card.get_by_role("status")).to_contain_text("登录已排队")
        checks.append("immediate_feedback_double_click_guard_independent_platform")
        for phase, text in [
            ("starting_browser", "正在启动登录浏览器"),
            ("opening_login", "正在准备登录页面"),
            ("waiting_scan", "并在手机上确认"),
            ("verifying", "正在确认登录状态"),
        ]:
            platforms[0]["login"].update(phase=phase, task_state="running")
            await expect(card.get_by_role("status")).to_contain_text(text, timeout=6000)
            await expect(
                card.get_by_role("button", name="登录进行中…")
            ).to_be_disabled()
        await page.screenshot(path=str(OUT / "verifying-light.png"), full_page=True)
        checks.append("queue_browser_scan_verification_stages")
        platforms[0].update(login_state="authenticated", verified_at=stamp)
        platforms[0]["login"].update(
            phase="authenticated", active=False, task_state="completed", restored=True
        )
        await expect(card.get_by_role("status")).to_contain_text(
            "已恢复登录，无需再次扫码", timeout=6000
        )
        await page.get_by_role("button", name="采集任务", exact=True).click()
        await expect(boxes.first).to_be_checked()
        await boxes.first.uncheck()
        await page.wait_for_timeout(3300)
        await expect(boxes.first).not_to_be_checked()
        checks.append(
            "newly_authenticated_auto_select_manual_deselection_survives_poll"
        )
        await page.reload()
        await page.get_by_role("button", name="采集任务", exact=True).click()
        await expect(boxes.first).to_be_checked()
        platforms[0].update(login_state="logged_out")
        platforms[0]["login"].update(
            phase="failed", message="登录已失效或尚未登录，请重新登录。"
        )
        await expect(boxes.first).not_to_be_checked(timeout=6000)
        await expect(boxes.first).to_be_disabled()
        checks.append("remember_verified_on_reload_and_deselect_expired")
        await page.get_by_role("button", name="平台登录", exact=True).click()
        for theme in ("light", "dark"):
            await page.evaluate(
                "theme=>document.documentElement.dataset.theme=theme", theme
            )
            await page.screenshot(
                path=str(OUT / f"platform-login-{theme}.png"), full_page=True
            )
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.screenshot(
            path=str(OUT / "platform-login-mobile.png"), full_page=True
        )
        assert await page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        await page.set_viewport_size({"width": 320, "height": 800})
        assert await page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        await card.get_by_role("button", name="重新登录").focus()
        await page.keyboard.press("Enter")
        await expect(card.get_by_role("status")).to_contain_text("登录已排队")
        checks.append("light_dark_320_390_keyboard")
        # Simulate a response timeout after the server has accepted the request.
        gate.clear()
        await page.evaluate(
            "() => { const timeout = AbortSignal.timeout.bind(AbortSignal); AbortSignal.timeout = ms => timeout(Math.min(ms, 200)); }"
        )
        dy = page.locator("#login-dy")
        await dy.get_by_role("button", name="打开登录", exact=True).click()
        await expect(dy.get_by_role("status")).to_contain_text(
            "登录已排队", timeout=5000
        )
        await expect(dy.get_by_role("button", name="登录进行中…")).to_be_disabled()
        assert len([s for s in submissions if s["platform"] == "dy"]) == 1
        gate.set()
        await page.wait_for_timeout(100)
        checks.append("timed_out_response_reconciles_accepted_job")
        platforms[0].update(
            login_state="authenticated",
            can_select=True,
            account={"name": "测试账号"},
            session_status="saved",
            saved_at=stamp,
            last_check={"state": "network_error", "at": stamp},
            login=None,
        )
        await expect(card.get_by_role("status")).to_contain_text(
            "账号仍保留", timeout=6000
        )
        await expect(card.locator(".wb-status")).to_have_text("连接异常")
        await page.get_by_role("button", name="采集任务", exact=True).click()
        await expect(boxes.first).to_be_checked()
        await boxes.first.uncheck()
        await page.wait_for_timeout(3200)
        await expect(boxes.first).not_to_be_checked()
        await page.reload()
        await page.get_by_role("button", name="采集任务", exact=True).click()
        await expect(boxes.first).to_be_checked()
        checks.append("network_preserves_account_and_manual_selection")
        await page.get_by_role("button", name="平台登录", exact=True).click()
        await card.locator("summary").click()
        await card.get_by_role("button", name="忘记此账号", exact=True).click()
        await expect(card.get_by_role("alertdialog")).to_be_focused()
        await page.keyboard.press("Escape")
        assert not session_actions
        await card.get_by_role("button", name="忘记此账号", exact=True).click()
        await card.get_by_role("button", name="确认忘记").click()
        await expect(card.locator(".wb-status")).to_have_text("尚未登录")
        assert session_actions == ["/platforms/xhs/session/forget"]
        checks.append("forget_requires_confirmation_keyboard_escape")
        await page.screenshot(
            path=str(OUT / "session-management-dark-mobile.png"), full_page=True
        )
        assert not errors, errors
        (OUT / "ui-validation.json").write_text(
            json.dumps(
                {
                    "checks": checks,
                    "page_errors": errors,
                    "real_platform_authentication": "not_tested_by_this_script",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(json.dumps({"checks": checks, "page_errors": errors}, ensure_ascii=False))
        await browser.close()
    server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
