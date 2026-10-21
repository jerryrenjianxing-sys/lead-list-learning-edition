"""Read-only Douyin capability control. Never logs bodies or enters login flow."""

import asyncio
import json
import os
from pathlib import Path
import sys
import time

from .database import Database
from .paths import data_root
from tools.request_budget import RequestBudget


def classify(capability, response):
    if not isinstance(response, dict):
        return "interface_error", "response_unconfirmed"
    if response.get("status_code") not in (None, 0, "0"):
        return "unconfirmed", "platform_error"
    if capability == "detail":
        return (
            ("available", "control_success")
            if response.get("aweme_id")
            else ("content_unavailable", "control_unavailable")
        )
    if capability in ("comments", "replies"):
        from media_platform.douyin.client import _comment_page

        _, _, error = _comment_page(response)
        return ("available", "control_success") if not error else ("unconfirmed", error)
    if capability == "search":
        if not isinstance(response.get("data"), list):
            return "interface_error", "response_unconfirmed"
        return (
            ("available", "control_success")
            if response["data"]
            else ("unconfirmed", "empty_control")
        )
    if capability in ("creator", "profile"):
        from .platform_observation import outcome

        reason = outcome("dy", capability, response)
        return (
            ("available", "control_success")
            if reason in ("source_exhausted", "no_results", "request_succeeded")
            else ("unconfirmed", reason)
        )
    return "unconfirmed", "control_not_supported"


async def inspect(operation, deadline):
    from playwright.async_api import async_playwright
    from media_platform.douyin.client import DouYinClient

    budget = RequestBudget(min(deadline, time.monotonic() + 30))
    result = {
        "attempted": False,
        "requests": 0,
        "verdict": "unconfirmed",
        "reason": "control_missing",
    }
    session = json.loads(os.environ["MEDIAWORKBENCH_SESSION"])
    control = json.loads(operation["control"])
    page = None
    try:
        async with asyncio.timeout(budget.remaining()):
            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp(
                    session["endpoint"], timeout=budget.remaining() * 1000
                )
                context = browser.contexts[0]
                page = await context.new_page()
                await page.goto(
                    "https://www.douyin.com/",
                    wait_until="domcontentloaded",
                    timeout=budget.take() * 1000,
                )
                cookies = {c["name"]: c["value"] for c in await context.cookies()}
                agent = await page.evaluate("navigator.userAgent")
                client = DouYinClient(
                    headers={
                        "User-Agent": agent,
                        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
                        "Origin": "https://www.douyin.com",
                        "Referer": "https://www.douyin.com/",
                    },
                    playwright_page=page,
                    cookie_dict=cookies,
                    timeout=30,
                )
                client._workbench_request_budget = budget
                capability = operation["capability"]
                result["attempted"] = True
                if capability == "search":
                    response = await client.search_info_by_keyword("美食")
                elif capability == "detail":
                    response = await client.get_video_by_id(control["aweme_id"])
                elif capability in ("comments", "replies"):
                    detail = await client.get_video_by_id(control["aweme_id"])
                    if not detail.get("aweme_id"):
                        return {
                            **result,
                            "verdict": "content_unavailable",
                            "reason": "control_unavailable",
                            "requests": budget.requests,
                        }
                    response = (
                        await client.get_aweme_comments(control["aweme_id"])
                        if capability == "comments"
                        else await client.get_sub_comments(
                            control["aweme_id"], control["comment_id"]
                        )
                    )
                elif capability == "creator":
                    response = await client.get_user_aweme_posts(control["sec_user_id"])
                elif capability == "profile":
                    response = await client.get_user_info(control["sec_user_id"])
                else:
                    return {**result, "reason": "control_not_supported"}
                verdict, reason = classify(capability, response)
                result.update(verdict=verdict, reason=reason)
    except (TimeoutError, asyncio.TimeoutError):
        result.update(verdict="network_error", reason="timeout")
    except Exception as exc:
        reason = getattr(exc, "reason", "request_failed")
        result.update(
            verdict="restricted"
            if reason == "restriction_explicit"
            else "network_error"
            if reason in ("network_error", "request_failed")
            else "interface_error"
            if reason
            in ("non_json_response", "response_unconfirmed", "interface_changed")
            else "unconfirmed",
            reason=reason,
            retry_after=getattr(exc, "retry_after", 0),
        )
    finally:
        result["requests"] = budget.requests
    return result


def main():
    identity, path = sys.argv[1:3]
    db = Database(data_root())
    rows = db.query(
        "SELECT o.capability,c.control FROM operations o JOIN access_checks c ON c.operation_id=o.id WHERE o.id=? AND o.state='running'",
        (identity,),
    )
    if not rows:
        raise SystemExit(2)
    result = asyncio.run(
        inspect(rows[0], float(os.environ["MEDIAWORKBENCH_PROBE_DEADLINE"]) - 3)
    )
    Path(path).write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
