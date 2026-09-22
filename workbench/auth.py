"""Persist verified authentication separately from browser profiles and task exit codes.

Worker updates are guarded by job attempt and state, so stopped/stale workers cannot
overwrite a newer session. No cookies, account identifiers, or response bodies are stored.
"""

from __future__ import annotations

import asyncio
import os
import hashlib
import copy
from types import MethodType
from .database import Database, now
from .paths import data_root

PENDING = (
    "starting_browser",
    "browser_ready",
    "verifying",
    "opening_login",
    "waiting_scan",
)
MESSAGES = {
    "browser_closed": "登录浏览器已关闭，尚未确认登录，请重试。",
    "scan_timeout": "扫码等待超时；请重新打开登录，扫码后在手机上确认。",
    "startup_timeout": "浏览器启动超时，请检查浏览器设置后重试。",
    "startup_failed": "登录浏览器未能启动，请查看详情或检查浏览器设置。",
    "unconfirmed": "验证未完成，未收到平台的有效认证确认。可重试检查，或从更多操作打开平台浏览器查看。",
    "network_error": "暂时无法验证登录，请检查网络或平台页面后重试。",
    "login_ui_changed": "登录页面未能完成操作，请查看弹出的浏览器；可能需要手动打开二维码或完成页面验证。",
    "logged_out": "登录已失效或尚未登录，请重新登录。",
    "challenge": "平台要求完成验证，请打开浏览器完成验证后继续任务。",
    "account_changed": "检测到账号已更换，请确认使用当前账号后再继续原任务。",
    "cancelled": "登录已取消，可以重新登录。",
    "interrupted": "登录过程已中断，请重新登录。",
}


def active_login(con, platform, exclude=""):
    return con.execute(
        """SELECT j.* FROM jobs j LEFT JOIN login_progress p
        ON p.job_id=j.id AND p.attempt=j.attempt
        WHERE j.platform=? AND j.id<>? AND j.state IN ('queued','running','stopping')
        AND (json_extract(j.options,'$.crawler_type')='login'
             OR (j.state IN ('running','stopping') AND p.phase IN
                 ('starting_browser','browser_ready','verifying','opening_login','waiting_scan')))
        ORDER BY j.created,j.id LIMIT 1""",
        (platform, exclude),
    ).fetchone()


def update(db, job_id, attempt, phase, *, state=None, reason="", restored=False):
    stamp = now()
    with db.lock, db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        job = con.execute(
            "SELECT platform,state,attempt FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not job or job["attempt"] != attempt or job["state"] != "running":
            con.rollback()
            return False
        previous = con.execute(
            "SELECT phase FROM login_progress WHERE job_id=? AND attempt=?",
            (job_id, attempt),
        ).fetchone()
        # QR publication occurs in an executor. Late completion must not regress verification.
        if phase == "waiting_scan" and previous and previous[0] != "opening_login":
            con.rollback()
            return False
        con.execute(
            """INSERT INTO login_progress VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(job_id,attempt) DO UPDATE SET phase=excluded.phase,updated=excluded.updated,
            reason=excluded.reason,restored=excluded.restored""",
            (job_id, attempt, phase, stamp, stamp, reason, int(restored)),
        )
        if state:
            con.execute(
                """INSERT INTO platform_auth VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(platform) DO UPDATE SET state=CASE WHEN excluded.state='unknown' AND platform_auth.state='authenticated' THEN platform_auth.state ELSE excluded.state END,
                verified_at=COALESCE(excluded.verified_at,platform_auth.verified_at),
                checked_at=excluded.checked_at,job_id=excluded.job_id,attempt=excluded.attempt,reason=excluded.reason""",
                (
                    job["platform"],
                    state,
                    stamp if state == "authenticated" else None,
                    stamp,
                    job_id,
                    attempt,
                    reason,
                ),
            )
        con.commit()
    return True


def report(phase, **kwargs):
    job_id = os.environ.get("MEDIAWORKBENCH_JOB_ID")
    attempt = os.environ.get("MEDIAWORKBENCH_ATTEMPT")
    if job_id and attempt:
        return update(Database(data_root()), job_id, int(attempt), phase, **kwargs)
    return False


def finish_progress(db, job_id, state, reason=""):
    # Called by the queue after final state is known, including hard kill and host recovery.
    job = db.one("jobs", job_id)
    rows = db.query(
        "SELECT * FROM login_progress WHERE job_id=? AND attempt=?",
        (job_id, job["attempt"]),
    )
    if rows and rows[0]["phase"] in (*PENDING, "queued"):
        reason = reason or (
            state if state in ("cancelled", "interrupted") else "unconfirmed"
        )
        db.execute(
            "UPDATE login_progress SET phase=?,reason=?,updated=? WHERE job_id=? AND attempt=?",
            (
                state if state in ("cancelled", "interrupted") else "failed",
                reason,
                now(),
                job_id,
                job["attempt"],
            ),
        )


def platform_view(db, platform):
    auth = db.query("SELECT * FROM platform_auth WHERE platform=?", (platform,))
    with db.connect() as con:
        active = active_login(con, platform)
    latest = db.query(
        """SELECT p.*, j.state task_state FROM login_progress p JOIN jobs j ON j.id=p.job_id
        WHERE j.platform=? ORDER BY p.updated DESC,p.attempt DESC LIMIT 1""",
        (platform,),
    )
    progress = latest[0] if latest else None
    if active:
        rows = db.query(
            "SELECT * FROM login_progress WHERE job_id=? AND attempt=?",
            (active["id"], active["attempt"]),
        )
        progress = (
            dict(rows[0])
            if rows
            else {
                "job_id": active["id"],
                "attempt": active["attempt"],
                "phase": "queued",
                "started": active["updated"],
                "updated": active["updated"],
                "reason": "",
            }
        )
        progress["task_state"] = active["state"]
        if active["state"] == "queued":
            progress["phase"] = "queued"
        elif active["state"] == "stopping":
            progress["phase"] = "stopping"
    if progress:
        if (
            progress.get("task_state") in ("cancelled", "interrupted")
            and progress["phase"] != "authenticated"
        ):
            progress["phase"] = progress["task_state"]
            progress["reason"] = progress["task_state"]
        progress["active"] = bool(active)
        progress["message"] = (
            MESSAGES.get(progress.get("reason"), "")
            if progress["phase"] in ("failed", "cancelled", "interrupted")
            else ""
        )
    return {
        "login_state": auth[0]["state"] if auth else "not_checked",
        "verified_at": auth[0]["verified_at"] if auth else None,
        "auth_reason": auth[0]["reason"] if auth else "",
        "login": progress,
    }


class AuthError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(MESSAGES[reason])


async def browser_json(page, url):
    response = await page.evaluate(
        """async url => {
        const r = await fetch(url, {credentials:'include',cache:'no-store',signal:AbortSignal.timeout(15000)});
        return {status:r.status, body:await r.json()};
    }""",
        url,
    )
    if response["status"] == 401:
        return {"_unauthorized": True}
    if response["status"] != 200:
        raise AuthError("network_error")
    return response["body"]


def flag(value):
    if value is True or value in (1, "1"):
        return "authenticated"
    if value is False or value in (0, "0"):
        return "logged_out"
    return "unknown"


def remember_identity(client, platform, identity, name=None):
    if identity not in (None, "", "0", 0):
        client._workbench_identity = {
            "id": hashlib.sha256(f"{platform}:{identity}".encode()).hexdigest(),
            "name": str(name or "已保存账号")[:120],
        }


async def probe(platform, client, page):
    """A positive server response is required. An absent field is not a logout."""
    if platform == "xhs":
        data = await client.query_self()
        info = (data or {}).get("data", {})
        user = (
            info.get("user_info")
            or info.get("user")
            or info.get("result", {}).get("user_info")
            or info
        )
        remember_identity(
            client,
            platform,
            user.get("user_id") or user.get("id"),
            user.get("nickname") or user.get("nick_name"),
        )
        return flag((data or {}).get("data", {}).get("result", {}).get("success"))
    if platform == "bili":
        # Raw browser request preserves -101/unauthenticated rather than swallowing it.
        data = await browser_json(page, "https://api.bilibili.com/x/web-interface/nav")
        if data.get("code") == -101 or data.get("_unauthorized"):
            return "logged_out"
        remember_identity(
            client,
            platform,
            data.get("data", {}).get("mid"),
            data.get("data", {}).get("uname"),
        )
        return flag(data.get("data", {}).get("isLogin"))
    if platform == "wb":
        data = await client.request(
            method="GET", url=f"{client._host}/api/config", headers=client.headers
        )
        remember_identity(client, platform, data.get("uid"))
        return flag(data.get("login"))
    if platform == "ks":
        data = await client.post(
            "",
            {
                "operationName": "visionProfileUserList",
                "variables": {"ftype": 1},
                "query": client.graphql.get("vision_profile_user_list"),
            },
        )
        result = data.get("visionProfileUserList", {}).get("result")
        return "authenticated" if result == 1 else "unknown"
    if platform == "zhihu":
        data = await browser_json(page, "https://www.zhihu.com/api/v4/me")
        if data.get("_unauthorized"):
            return "logged_out"
        remember_identity(
            client, platform, data.get("uid") or data.get("id"), data.get("name")
        )
        return "authenticated" if data.get("uid") and data.get("name") else "unknown"
    if platform == "tieba":
        data = await client._fetch_json_by_browser("/dc/common/tbs")
        return flag(data.get("is_login"))
    if platform == "dy":
        data = await client.get("/aweme/v1/web/user/profile/self/", {"aid": "6383"})
        # Confirmed by the live self-profile endpoint: 8 = 用户未登录.
        if data.get("status_code") == 8:
            return "logged_out"
        if data.get("status_code") == 0 and (data.get("user") or {}).get("uid") not in (
            None,
            "",
            "0",
            0,
        ):
            remember_identity(
                client, platform, data["user"]["uid"], data["user"].get("nickname")
            )
            return "authenticated"
        return "unknown"
    raise ValueError(platform)


async def watched(awaitable, page, timeout, reason):
    task = asyncio.ensure_future(awaitable)
    try:
        deadline = asyncio.get_running_loop().time() + timeout
        while not task.done():
            if page.is_closed():
                raise AuthError("browser_closed")
            if asyncio.get_running_loop().time() >= deadline:
                raise AuthError(reason)
            await asyncio.wait({task}, timeout=0.2)
        return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def has_challenge(page):
    try:
        return (
            await page.locator(
                "#captcha_container:visible, #captcha-verify-container:visible, .captcha_verify_container:visible, iframe[src*='captcha']:visible"
            ).count()
            > 0
        )
    except Exception:
        return False


async def checked_probe(platform, client, page, budget=30):
    deadline = asyncio.get_running_loop().time() + budget
    last = "unknown"
    for attempt in range(3):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            client._workbench_identity = {}
            await watched(
                client.update_cookies(
                    browser_context=page.context,
                    urls=getattr(client, "cookie_urls", None),
                ),
                page,
                min(5, remaining),
                "network_error",
            )
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return "network_error"
            # This layer owns the total retry budget. Do not multiply it by
            # platform transport retries, or change the active collector client.
            probe_client = copy.copy(client)
            probe_client._workbench_auth_probe = True
            request = getattr(probe_client, "request", None)
            raw = getattr(request, "__wrapped__", None)
            if raw is not None:
                probe_client.request = MethodType(raw, probe_client)
            last = await watched(
                probe(platform, probe_client, page),
                page,
                min(15, remaining),
                "network_error",
            )
            if last == "authenticated":
                client._workbench_identity = getattr(
                    probe_client, "_workbench_identity", {}
                )
                return last
            if await has_challenge(page):
                return "challenge"
            # A definitive logout is not a connectivity error.
            if last == "logged_out":
                return last
            return "unknown"
        except Exception:
            if page.is_closed():
                raise AuthError("browser_closed") from None
            if await has_challenge(page):
                return "challenge"
            last = "network_error"
        if attempt < 2:
            await asyncio.sleep(
                min(
                    (2, 5)[attempt],
                    max(0, deadline - asyncio.get_running_loop().time()),
                )
            )
    return last


async def session_check(state):
    from .session_client import command

    await asyncio.to_thread(command, "check", state=state)


async def save_verified(platform, client, *, new_auth=False):
    from .session_client import command

    result = await asyncio.to_thread(
        command,
        "save",
        identity=getattr(client, "_workbench_identity", {}),
        new_auth=new_auth,
    )
    if result and result.get("account_changed"):
        raise AuthError("account_changed")
    return result


async def managed_login(platform, crawler, client, login_class):
    """Authenticate only on a clear user action; uncertainty must not trigger QR login."""
    if not os.environ.get("MEDIAWORKBENCH_JOB_ID"):
        return False
    import config
    from .session_client import command

    page = crawler.context_page
    interactive = config.CRAWLER_TYPE == "login"
    action = getattr(config, "WORKBENCH_SESSION_ACTION", "login")
    report("browser_ready")
    try:
        report("verifying")
        initial = await checked_probe(platform, client, page)
        await session_check(initial)
        if initial == "authenticated":
            await save_verified(platform, client, new_auth=interactive)
            report("authenticated", state="authenticated", restored=True)
            return True
        if action == "open" and interactive:
            await asyncio.to_thread(command, "show")
        if (
            not interactive
            or action == "check"
            or initial in ("unknown", "network_error")
        ):
            raise AuthError({"unknown": "unconfirmed"}.get(initial, initial))
        await asyncio.to_thread(command, "show")
        if initial == "challenge":
            report("opening_login", state="unknown", reason="challenge")
            # Keep the current verification page; do not open a competing QR flow.
            deadline = asyncio.get_running_loop().time() + 180
            while asyncio.get_running_loop().time() < deadline:
                result = await checked_probe(platform, client, page, budget=15)
                if result == "authenticated":
                    await save_verified(platform, client, new_auth=True)
                    report("authenticated", state="authenticated")
                    return True
                await asyncio.sleep(3)
            raise AuthError("challenge")
        report("opening_login", state="logged_out", reason="logged_out")
        login = login_class(
            login_type=config.LOGIN_TYPE,
            login_phone="",
            browser_context=crawler.browser_context,
            context_page=page,
            cookie_str=config.COOKIES,
        )
        if platform in ("dy", "tieba", "zhihu"):

            async def live_login_check(*_args):
                deadline = asyncio.get_running_loop().time() + 120
                last_result = "unknown"
                while asyncio.get_running_loop().time() < deadline:
                    last_result = await checked_probe(platform, client, page, budget=15)
                    if last_result == "authenticated":
                        report("verifying")
                        return True
                    await asyncio.sleep(3)
                raise AuthError(
                    "scan_timeout" if last_result == "logged_out" else "unconfirmed"
                )

            login.check_login_state = live_login_check

        async def begin():
            try:
                await login.begin()
            except SystemExit:
                raise AuthError("scan_timeout") from None

        try:
            await watched(begin(), page, 180, "scan_timeout")
        except Exception as exc:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError

            if isinstance(exc, PlaywrightTimeoutError):
                raise AuthError("login_ui_changed") from None
            raise
        report("verifying")
        if platform == "wb":
            await page.goto(crawler.mobile_index_url)
        result = await checked_probe(platform, client, page)
        await session_check(result)
        if result != "authenticated":
            raise AuthError("unconfirmed" if result == "unknown" else result)
        await save_verified(platform, client, new_auth=True)
        report("authenticated", state="authenticated")
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reason = (
            "browser_closed"
            if page.is_closed()
            else (exc.reason if isinstance(exc, AuthError) else "network_error")
        )
        report(
            "failed",
            state="logged_out" if reason == "logged_out" else "unknown",
            reason=reason,
        )
        raise AuthError(reason) from None
