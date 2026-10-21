"""Production probe entrypoint with browser/network adapters; no real platform."""

import asyncio
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import httpx
import playwright.async_api
from media_platform.douyin import client as dy
from workbench import probe_worker

identity, result, marker, scenario = sys.argv[1:]
calls = []


class Page:
    async def goto(self, *args, **kwargs):
        return None

    async def evaluate(self, expression, *args):
        if "navigator.userAgent" in expression:
            return "Synthetic browser transport"
        if "window.localStorage" in expression:
            return {}
        return "synthetic-signature"


class Context:
    async def new_page(self):
        return Page()

    async def cookies(self):
        return []


async def connect(*args, **kwargs):
    return SimpleNamespace(contexts=[Context()])


class Playwright:
    chromium = SimpleNamespace(connect_over_cdp=connect)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def reply(request):
    calls.append(request.url.path)
    Path(marker).write_text(json.dumps(calls), encoding="utf-8")
    if scenario == "network":
        raise httpx.ConnectError("synthetic network", request=request)
    if "/detail/" in request.url.path:
        return httpx.Response(
            200,
            json={"status_code": 0, "aweme_detail": {"aweme_id": "sample"}},
            request=request,
        )
    return httpx.Response(
        200, json={"status_code": 0, "comments": [], "has_more": 0}, request=request
    )


playwright.async_api.async_playwright = Playwright
dy.make_async_client = lambda **kwargs: httpx.AsyncClient(
    transport=httpx.MockTransport(reply)
)
os.environ["MEDIAWORKBENCH_SESSION"] = json.dumps({"endpoint": "http://127.0.0.1:1"})
sys.argv = [sys.argv[0], identity, result]
probe_worker.main()
