"""Exercise history cleanup on an isolated real host; never touches user data."""

import json
import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright, expect
from workbench.app import make_app
from workbench.models import CrawlRequest
from workbench.queue import create_job

out = Path(__file__).resolve().parents[1] / ".cache/history-review"
out.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as temp:
    app = make_app(Path(temp), run_queue=False)
    for title, state in [
        ("完成示例", "completed"),
        ("失败示例", "failed"),
        ("排队示例", "queued"),
    ]:
        opts = CrawlRequest(platform="dy", keywords=title).model_dump()
        job = app.state.db.write(None, "create", opts, lambda c: create_job(c, opts))
        app.state.db.execute("UPDATE jobs SET state=? WHERE id=?", (state, job["id"]))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    address = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(address)
            page.get_by_role("link", name="任务台", exact=True).click()
            page.get_by_role("button", name="采集任务", exact=True).click()
            expect(page.get_by_role("checkbox", name="下载", exact=False)).to_have_count(0)
            expect(page.locator(".wb-row")).to_have_count(3)
            row = page.locator(".wb-row").filter(has_text="完成示例")
            row.get_by_role("button", name="清理", exact=True).click()
            expect(page.locator(".wb-row")).to_have_count(2)
            expect(
                page.get_by_role("button", name="恢复已清理记录（1）")
            ).to_be_visible()
            page.reload()
            page.get_by_role("button", name="采集任务", exact=True).click()
            expect(page.locator(".wb-row")).to_have_count(2)
            page.get_by_role("button", name="清理已结束任务", exact=True).click()
            expect(page.locator(".wb-row")).to_have_count(1)
            expect(page.locator(".wb-row")).to_contain_text("排队示例")
            expect(
                page.get_by_role("button", name="清理已结束任务", exact=True)
            ).to_be_disabled()
            page.get_by_role("button", name="恢复已清理记录（2）").click()
            expect(page.locator(".wb-row")).to_have_count(3)
            assert app.state.db.query("SELECT COUNT(*) n FROM datasets")[0]["n"] == 3
            for theme in ["light", "dark"]:
                page.evaluate("t=>document.documentElement.dataset.theme=t", theme)
                page.screenshot(path=str(out / f"history-{theme}.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            page.screenshot(path=str(out / "history-mobile.png"), full_page=True)
            browser.close()
        assert not errors, errors
        print(
            json.dumps(
                {
                    "checks": [
                        "media_option_removed",
                        "single_clear",
                        "reload",
                        "bulk_clear",
                        "active_preserved",
                        "restore",
                        "data_preserved",
                        "themes_mobile",
                    ],
                    "page_errors": errors,
                }
            )
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
