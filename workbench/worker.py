"""Launch the unchanged platform entrypoint with isolated data and cooperative stop."""

import asyncio
import json
import os
import sys
import time
from .database import Database
from .paths import ROOT, data_root


def main():
    job_id = sys.argv[1]
    root = data_root()
    db = Database(root)
    job = db.one("jobs", job_id)
    options = json.loads(job["options"])
    run_dir = root / "runs" / job_id
    run_dir.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "MEDIAWORKBENCH_JOB_ID": job_id,
            "MEDIAWORKBENCH_BROWSER_ROOT": str(root / "browser_data"),
            "MEDIAWORKBENCH_LOGIN_DIR": str(run_dir),
        }
    )
    os.chdir(ROOT)
    import config

    config.SAVE_DATA_PATH = str(run_dir)
    # Enforce this for old persisted jobs too, independently of their options.
    config.ENABLE_GET_MEDIA = False
    config.LEAD_MODE = options["enrich_profiles"]
    config.LEAD_MAX_ACCOUNTS = 0
    config.LEAD_DEEP_PROFILE_LIMIT = 0
    config.LEAD_MAX_COMMENTS = options["max_comments_count"]
    config.CDP_DEBUG_PORT = 19339
    config.AUTO_CLOSE_BROWSER = True
    config.ENABLE_CDP_MODE = True
    config.SAVE_LOGIN_STATE = True
    config.WORKBENCH_SESSION_ACTION = options.get("session_action", "login")
    settings = {
        r["key"]: json.loads(r["value"]) for r in db.query("SELECT * FROM settings")
    }
    custom = settings.get("browser_path", "")
    if custom:
        config.CUSTOM_BROWSER_PATH = custom
    # The bundled Playwright browser is a fallback when no desktop browser exists.
    from tools.browser_launcher import BrowserLauncher

    try:
        launcher = BrowserLauncher()
        detected = launcher.detect_browser_paths()
    except Exception:
        detected = None
    if not custom and not detected:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            if os.path.isfile(pw.chromium.executable_path):
                config.CUSTOM_BROWSER_PATH = pw.chromium.executable_path
    args = [
        "main.py",
        "--platform",
        options["platform"],
        "--type",
        options["crawler_type"],
        "--lt",
        options["login_type"],
        "--save_data_option",
        "jsonl",
        "--save_data_path",
        str(run_dir),
        "--start",
        str(options["start_page"]),
    ]
    for key, flag in (
        ("keywords", "--keywords"),
        ("specified_ids", "--specified_id"),
        ("creator_ids", "--creator_id"),
    ):
        if options.get(key):
            args += [flag, options[key]]
    for key, flag in (
        ("enable_comments", "--get_comment"),
        ("enable_sub_comments", "--get_sub_comment"),
        ("headless", "--headless"),
    ):
        args += [flag, str(options[key]).lower()]
    args += [
        "--get_media",
        "false",
        "--crawler_max_notes_count",
        str(options["max_notes_count"]),
        "--max_comments_count_singlenotes",
        str(options["max_comments_count"]),
    ]
    if options.get("cookies"):
        # Passed through config, never exposed on the process command line.
        from .credentials import unseal

        config.COOKIES = unseal(options["cookies"])
    sys.argv = args
    import main as engine
    from tools.app_runner import run

    async def crawl():
        task = asyncio.create_task(engine.main())
        import psutil

        parent = psutil.Process(os.getpid()).parent()
        parent_created = parent.create_time() if parent else None
        next_check = time.monotonic() + 60
        monitor = None

        async def check_session():
            from .auth import (
                checked_probe,
                save_verified,
                session_check,
                report,
                AuthError,
            )

            crawler = getattr(engine, "crawler", None)
            names = {
                "dy": "dy_client",
                "xhs": "xhs_client",
                "ks": "ks_client",
                "bili": "bili_client",
                "wb": "wb_client",
                "tieba": "tieba_client",
                "zhihu": "zhihu_client",
            }
            client = getattr(crawler, names[options["platform"]], None)
            if not client:
                return
            result = await checked_probe(
                options["platform"], client, crawler.context_page
            )
            await session_check(result)
            if result == "authenticated":
                try:
                    await save_verified(options["platform"], client)
                except AuthError as exc:
                    report("failed", state="unknown", reason=exc.reason)
                    raise
            else:
                reason = "unconfirmed" if result == "unknown" else result
                report(
                    "failed",
                    state="logged_out" if result == "logged_out" else "unknown",
                    reason=reason,
                )
                raise AuthError(reason)

        while not task.done():
            try:
                parent_alive = (
                    parent is not None
                    and parent.is_running()
                    and parent.create_time() == parent_created
                )
            except psutil.Error:
                parent_alive = False
            if (run_dir / ".stop").exists() or not parent_alive:
                if monitor:
                    monitor.cancel()
                    await asyncio.gather(monitor, return_exceptions=True)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                return
            if monitor and monitor.done():
                error = monitor.exception()
                monitor = None
                if error:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise error
            if (
                os.environ.get("MEDIAWORKBENCH_SESSION")
                and not monitor
                and time.monotonic() >= next_check
            ):
                next_check = time.monotonic() + 60
                progress = db.query(
                    "SELECT phase FROM login_progress WHERE job_id=? AND attempt=?",
                    (job_id, job["attempt"]),
                )
                if (
                    progress
                    and progress[0]["phase"] == "authenticated"
                    and options["crawler_type"] != "login"
                ):
                    monitor = asyncio.create_task(check_session())
            await asyncio.sleep(0.2)
        if monitor:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        try:
            await task
        except Exception as exc:
            from .auth import PENDING, report

            progress = db.query(
                "SELECT phase FROM login_progress WHERE job_id=? AND attempt=?",
                (job_id, job["attempt"]),
            )
            if progress and progress[0]["phase"] in PENDING:
                reason = (
                    "browser_closed"
                    if type(exc).__name__ == "TargetClosedError"
                    else (
                        "startup_failed"
                        if progress[0]["phase"] == "starting_browser"
                        else "network_error"
                    )
                )
                report("failed", state="unknown", reason=reason)
            raise

    run(crawl, engine.async_cleanup, cleanup_timeout_seconds=15)


if __name__ == "__main__":
    main()
