"""Account changes through real HTTP, owned Chromium and worker processes.

Authentication is a controlled platform adapter, not a real-platform claim.
"""

import json
import os
import socket
import sys
import threading
import time
import httpx
import pytest
import uvicorn

from workbench.app import make_app
from workbench.session_store import SessionStore


@pytest.mark.skipif(os.name != "nt", reason="Windows Chromium cookie database ownership")
def test_saved_browser_account_is_visible_after_profile_check_without_gc(tmp_path):
    """No snapshot or platform network: the real browser must load its own disk cookies."""
    import gc
    from playwright.sync_api import sync_playwright

    app = make_app(tmp_path)
    store = SessionStore(app.state.db)
    with sync_playwright() as pw:
        browser = pw.chromium.executable_path
        profile = store.bind("dy", browser)["profile"]
        context = pw.chromium.launch_persistent_context(profile, headless=False)
        try:
            context.add_cookies([{
                "name": "saved-account", "value": "synthetic-account",
                "url": "https://account.example.invalid/", "expires": time.time() + 3600,
            }])
        finally:
            context.close()
    app.state.db.execute("INSERT INTO settings VALUES('browser_path',?)", (json.dumps(browser),))
    # This platform adapter reads an owned Chromium profile through the real
    # worker lease; it never contacts a social platform or pretends to validate one.
    script = """
import asyncio,json,os,sys
os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1]
from playwright.async_api import async_playwright
from workbench.auth import report
from workbench.session_client import command
async def main():
 async with async_playwright() as pw:
  browser=await pw.chromium.connect_over_cdp(json.loads(os.environ['MEDIAWORKBENCH_SESSION'])['endpoint'])
  cookies=await browser.contexts[0].cookies()
  if any(c['name']=='saved-account' and c['value']=='synthetic-account' for c in cookies):
   command('save',identity={'id':'synthetic-account'})
   report('authenticated',state='authenticated')
  else:report('failed',state='logged_out',reason='logged_out')
asyncio.run(main())
"""
    app.state.queue.worker_command = lambda identity: [sys.executable, "-c", script, identity]
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); sock.listen(16)
    base = "http://127.0.0.1:" + str(sock.getsockname()[1])
    app.state.sessions.url = base
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    was_enabled = gc.isenabled()
    gc.disable()  # Correct resource ownership must not depend on a later cyclic collection.
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline: time.sleep(.02)
        assert server.started
        with httpx.Client(base_url=base, headers={"Authorization": "Bearer " + app.state.token}, timeout=20) as client:
            created = client.post("/api/v1/platforms/dy/session/check", json={}).json()
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                job = client.get("/api/v1/jobs/" + created["id"]).json()
                if job["state"] not in ("queued", "running", "stopping"): break
                time.sleep(.05)
            assert job["state"] == "completed", job["status_detail"]
            assert client.get("/api/v1/datasets").json()["items"] == []
            assert store.view("dy")["last_check"]["state"] == "authenticated"
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        if was_enabled: gc.enable()
        gc.collect()
        assert not thread.is_alive()


@pytest.mark.skipif(
    os.name != "nt", reason="Windows account snapshots and browser lifecycle"
)
@pytest.mark.parametrize(
    "mode,expected", [("login", "completed"), ("search", "needs_login")]
)
def test_login_can_change_account_without_replaying_old_account_research(
    tmp_path, mode, expected
):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.executable_path
    app = make_app(tmp_path)
    app.state.db.execute(
        "INSERT INTO settings VALUES('browser_path',?)", (json.dumps(browser),)
    )
    store = SessionStore(app.state.db)
    store.bind("dy", browser)
    assert store.save(
        "dy",
        {"cookies": [], "origins": []},
        {"id": "old-synthetic-account"},
        new_auth=True,
    )
    script = """
import os,sys
os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1]
from workbench.session_client import command
from workbench.auth import report
result=command('save',identity={'id':'new-synthetic-account'},new_auth=True)
if result['account_changed']:report('failed',state='unknown',reason='account_changed')
else:report('authenticated',state='authenticated')
"""
    app.state.queue.worker_command = lambda identity: [
        sys.executable,
        "-c",
        script,
        identity,
    ]
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    base = "http://127.0.0.1:" + str(sock.getsockname()[1])
    app.state.sessions.url = base
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        with httpx.Client(
            base_url=base,
            headers={"Authorization": "Bearer " + app.state.token},
            timeout=20,
        ) as client:
            created = client.post(
                "/api/v1/jobs",
                json={"platform": "dy", "crawler_type": mode, "keywords": "synthetic"},
            ).json()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                job = client.get("/api/v1/jobs/" + created["id"]).json()
                if job["state"] not in ("queued", "running", "stopping"):
                    break
                time.sleep(0.05)
            assert job["state"] == expected, job
            assert store.get("dy")["account_id"] == "new-synthetic-account"
            if mode == "login":
                assert (
                    client.get("/api/v1/operations/" + created["operation_id"]).json()[
                        "state"
                    ]
                    == "completed"
                )
            else:
                assert (
                    client.get(
                        "/api/v1/datasets/" + created["dataset_id"] + "/records"
                    ).json()["items"]
                    == []
                )
                assert job["status_detail"]["reason_code"] == "account_changed"
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        assert not thread.is_alive()
