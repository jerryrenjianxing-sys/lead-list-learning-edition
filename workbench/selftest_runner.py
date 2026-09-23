"""Private self-test subprocess. Never invoked by the normal crawler API."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler

import psutil

from .paths import ROOT
from .selftest import atomic_json, read_json
from .queue import kill_tree


class CheckIssue(Exception):
    def __init__(self, state, message):
        self.state, self.message = state, message


def classify_network(*, restricted=False, empty=False, error=None):
    if restricted:
        return "limited", "平台要求登录或人工验证；本次游客试采受限。"
    if empty:
        return "unconfirmed", "平台未返回公开内容，无法确认游客试采。"
    if error:
        import httpx
        from playwright.async_api import TimeoutError as BrowserTimeout
        from media_platform.weibo.exception import DataFetchError
        if isinstance(error, (TimeoutError, ConnectionError, httpx.TransportError, BrowserTimeout)):
            return "unconfirmed", "网络连接未完成，未验证游客试采。"
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in (403, 429, 502, 503, 504):
            return "unconfirmed", "平台暂未接受访问，原因未能确认。"
        if isinstance(error, DataFetchError):
            return "unconfirmed", "平台未返回可确认的公开数据，未判定为软件故障。"
        return "failed", "试采程序或响应解析异常（" + type(error).__name__ + "）。"
    return "passed", "游客搜索和详情读取通过。"


async def network_probe():
    from playwright.async_api import async_playwright
    from media_platform.weibo.core import WeiboCrawler
    from types import MethodType

    restricted = False
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(locale="zh-CN")
            assert await context.cookies() == []
            await context.route("**/*", lambda route: route.abort() if route.request.resource_type in ("media", "image", "font") else route.continue_())
            page = await context.new_page()
            await page.goto("https://m.weibo.cn", wait_until="domcontentloaded", timeout=20000)
            if "visitor.passport.weibo.cn/visitor" in page.url:
                # Anonymous cookie bootstrap is not account authentication.
                from playwright.async_api import TimeoutError as BrowserTimeout
                try:
                    await page.wait_for_url("https://m.weibo.cn/**", timeout=8000)
                except BrowserTimeout:
                    pass
            restricted = any(x in page.url.lower() for x in ("captcha", "/signin", "/login"))
            if restricted:
                return classify_network(restricted=True)
            crawler = WeiboCrawler()
            crawler.browser_context, crawler.context_page = context, page
            crawler.ip_proxy_pool = None
            crawler.user_agent = await page.evaluate("navigator.userAgent")
            client = await crawler.create_weibo_client(None)
            # One transport attempt; a self-test must not amplify rejected requests.
            if hasattr(client.request, "__wrapped__"):
                client.request = MethodType(client.request.__wrapped__, client)
            response = await client.get_note_by_keyword("Python", page=1)
            if not isinstance(response, dict):
                raise ValueError("Unexpected response shape")
            cards = response.get("cards") or []
            posts = [c["mblog"] for c in cards if c.get("mblog")]
            posts += [item["mblog"] for c in cards for item in c.get("card_group") or [] if item.get("mblog")]
            if not posts:
                return classify_network(empty=True)
            detail = await client.get_note_info_by_id(str(posts[0]["id"]))
            if not isinstance(detail, dict):
                raise ValueError("Unexpected detail shape")
            if not (detail.get("mblog") or {}).get("text"):
                return classify_network(empty=True)
            # Exercise the same normalized insertion path, only in the isolated DB.
            from .database import Database, insert_records, uid, now, dump
            db = Database(Path(os.environ["MEDIAWORKBENCH_DATA_DIR"]) / "network")
            identity = uid()
            with db.connect() as con:
                con.execute("INSERT INTO datasets VALUES(?,?,?,?)", (identity, "游客自检", now(), dump({})))
                insert_records(con, identity, posts[:3], platform="wb", kind="note")
                con.commit()
            return classify_network()
        except Exception as exc:
            # Only explicit login/challenge markers count as platform restrictions.
            restricted = restricted or any(marker in str(exc).lower() for marker in ("需要登录", "请先登录", "登录后", "验证码", "captcha", "login required"))
            return classify_network(restricted=restricted, error=exc)
        finally:
            await browser.close()


class Runner:
    def __init__(self, directory):
        self.directory = directory
        self.progress = {}
        self.host = None
        self.info = None
        self.env = {**os.environ, "MEDIAWORKBENCH_DATA_DIR": str(directory / "data"),
                    "MEDIAWORKBENCH_INSTANCE": str(directory / "instance.json")}
        self.opener = build_opener(ProxyHandler({}))

    def step(self, key, action):
        started = time.monotonic()
        self.progress[key] = dict(state="running", message="正在检查…", elapsed_seconds=0)
        atomic_json(self.directory / "progress.json", self.progress)
        try:
            message = action() or "通过。"
            result = dict(state="passed", message=message)
        except CheckIssue as exc:
            result = dict(state=exc.state, message=exc.message)
        except Exception as exc:
            result = dict(state="failed", message="检查未通过（" + type(exc).__name__ + "）。")
        result["elapsed_seconds"] = round(time.monotonic() - started, 1)
        self.progress[key] = result
        atomic_json(self.directory / "progress.json", self.progress)
        return result["state"] == "passed"

    def start_host(self):
        self.host = subprocess.Popen([sys.executable, "-m", "workbench", "--port", "0"],
                                     cwd=ROOT, env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if self.host.poll() is not None:
                raise RuntimeError("Isolated host exited")
            info = read_json(self.directory / "instance.json", {})
            owned = {self.host.pid}
            try:
                owned.update(p.pid for p in psutil.Process(self.host.pid).children(recursive=True))
            except psutil.Error:
                pass
            if info.get("pid") in owned:
                try:
                    with self.opener.open(info["base_url"] + "/api/v1/health", timeout=1) as response:
                        assert json.load(response)["product"] == "MediaWorkbench"
                    self.info = info
                    return
                except OSError:
                    pass
            time.sleep(0.1)
        raise TimeoutError("Isolated host startup")

    def stop_host(self):
        if self.host and self.host.poll() is None:
            try:
                request = Request(self.info["base_url"] + "/api/v1/host/stop", method="POST", data=b"{}",
                                  headers={"Authorization": "Bearer " + self.info["token"], "Content-Type": "application/json"})
                self.opener.open(request, timeout=5).close()
                self.host.wait(timeout=12)
            except Exception:
                kill_tree(self.host.pid)
                raise

    def runtime(self):
        node = ROOT / "runtime/node/node.exe"
        executable = str(node) if node.exists() else shutil.which("node")
        assert executable, "Node unavailable"
        result = subprocess.run([executable, "-p", "JSON.stringify({answer:21*2,temp:require('os').tmpdir()})"], capture_output=True, timeout=10, check=True)
        node_result = json.loads(result.stdout)
        assert node_result["answer"] == 42
        assert Path(node_result["temp"]).resolve().is_relative_to(self.directory.resolve())
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content("<title>本机运行自检</title><p>ready</p>")
                assert page.title() == "本机运行自检"
            finally:
                browser.close()
        return "Python、Node 与浏览器可用。"

    def host_page(self):
        self.start_host()
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(self.info["base_url"], wait_until="domcontentloaded", timeout=15000)
                page.wait_for_function("document.querySelector('#root')?.children.length > 0", timeout=10000)
                assert "Media Deep Researcher" in page.title()
            finally:
                browser.close()
        return "独立后台与首页加载通过。"

    def queue(self):
        from .database import Database
        from .models import CrawlRequest
        from .queue import Queue, create_job
        db = Database(self.directory / "queue")
        result = db.write(None, "self-test", {}, lambda con: create_job(con, CrawlRequest(platform="wb", keywords="synthetic").model_dump()))
        assert db.one("jobs", result["id"])["state"] == "queued"
        queue = Queue(db, worker_command=lambda identity: [sys.executable, "-m", "workbench.selftest_runner", "--fixture", identity])
        queue.start()
        seen_running = False
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                row = db.one("jobs", result["id"])
                seen_running |= row["state"] == "running"
                if row["state"] == "completed":
                    assert seen_running
                    assert db.query("SELECT count(*) n FROM records")[0]["n"] == 3
                    return "合成任务已排队、运行、完成；重复写入保持三条记录。"
                if row["state"] in ("failed", "partial", "interrupted"):
                    raise RuntimeError("Fixture worker failed")
                time.sleep(0.05)
            raise TimeoutError("Queue execution")
        finally:
            queue.close()

    def skill(self):
        with self.opener.open(self.info["base_url"] + "/api/v1/skill", timeout=5) as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
        destination = self.directory / "skill"
        for name in archive.namelist():
            if not (destination / name).resolve().is_relative_to(destination.resolve()):
                raise ValueError("Unsafe skill archive")
        archive.extractall(destination)
        self.script = destination / "media-workbench/scripts/workbench.py"
        spec = importlib.util.spec_from_file_location("self_test_client", self.script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.client = module.Client(self.directory / "instance.json")
        result = subprocess.run([sys.executable, str(self.script), "--instance", str(self.directory / "instance.json"), "check"],
                                env=self.env, capture_output=True, timeout=15, check=True)
        assert json.loads(result.stdout)["product"] == "MediaWorkbench"
        return "新解压的 Skill 客户端连接成功。"

    def call(self, method, path, body=None, key=None):
        return json.loads(self.client.send(method, "/api/v1" + path,
                          json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                          {"Content-Type": "application/json", **({"Idempotency-Key": key} if key else {})}, timeout=10))

    def data(self):
        dataset = self.call("POST", "/demo", {}, "self-test-demo")
        assert self.call("POST", "/demo", {}, "self-test-demo") == dataset
        self.dataset_id = dataset["id"]
        a = self.call("POST", "/analyses", {"dataset_id": dataset["id"], "name": "运行自检", "goal": "固定合成内容校验"})
        self.analysis_id = a["id"]
        records = self.call("GET", f"/analyses/{a['id']}/inputs")["items"]
        assert len(records) == 6
        batch = {"batch_id": "self-test", "processed_ids": [r["id"] for r in records],
                 "results": [{"payload": {"自检": "固定测试内容", "数量": 6}, "evidence_ids": [records[0]["id"]]}]}
        first = self.call("POST", f"/analyses/{a['id']}/batches", batch, "self-test-batch")
        assert self.call("POST", f"/analyses/{a['id']}/batches", batch, "self-test-batch") == first
        assert self.call("POST", f"/analyses/{a['id']}/finish", {})["state"] == "completed"
        for extension in ("json", "csv", "xlsx", "docx", "md"):
            artifact = self.call("POST", f"/analyses/{a['id']}/export", {"format": extension})
            content = self.client.send("GET", artifact["download_url"])
            assert len(content) > 10
            if extension in ("xlsx", "docx"):
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    assert ("xl/workbook.xml" if extension == "xlsx" else "word/document.xml") in archive.namelist()
            elif extension == "json":
                json.loads(content)
            else:
                assert "固定测试内容" in content.decode("utf-8-sig")
        return "六条合成记录、请求去重、结果保存及五种格式导出通过。"

    def recovery(self):
        self.stop_host()
        self.start_host()
        self.client = type(self.client)(self.directory / "instance.json")
        records = self.call("GET", f"/datasets/{self.dataset_id}/records")["items"]
        assert len(records) == 6
        assert self.call("GET", f"/analyses/{self.analysis_id}")["state"] == "completed"
        assert len(self.call("GET", f"/analyses/{self.analysis_id}/results")["items"]) == 1
        return "独立后台重开后数据与结果保持一致。"

    def network(self):
        try:
            state, message = asyncio.run(asyncio.wait_for(network_probe(), timeout=45))
        except TimeoutError:
            state, message = "unconfirmed", "联网试采超过四十五秒，未能确认。"
        if state != "passed":
            raise CheckIssue(state, message)
        return message


def fixture(identity):
    from .database import Database, insert_records
    from .paths import data_root
    db = Database(data_root())
    row = db.one("jobs", identity)
    time.sleep(0.3)
    with db.connect() as con:
        records = [{"source_id": str(i), "payload": {"text": "合成自检", "index": i}} for i in range(3)]
        insert_records(con, row["dataset_id"], records)
        insert_records(con, row["dataset_id"], records)
        con.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?")
    parser.add_argument("--fixture")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--parent", type=int)
    parser.add_argument("--parent-created", type=float)
    args = parser.parse_args()
    if args.fixture:
        fixture(args.fixture)
        return
    directory = Path(args.directory).resolve()
    os.environ["MEDIAWORKBENCH_DATA_DIR"] = str(directory / "data")
    deadline = time.monotonic() + 185

    def watch():
        while True:
            try:
                alive = not args.parent or abs(psutil.Process(args.parent).create_time() - args.parent_created) < 0.01
            except psutil.Error:
                alive = False
            if not alive or time.monotonic() > deadline:
                for child in psutil.Process().children(recursive=True):
                    try:
                        child.kill()
                    except psutil.Error:
                        pass
                os._exit(2)
            time.sleep(0.5)

    threading.Thread(target=watch, daemon=True).start()
    # The host persists ownership and assigns the Windows job before children start.
    if args.parent:
        for _ in range(100):
            if (directory / "owner.json").exists():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("Diagnostic ownership was not established")
    runner = Runner(directory)
    try:
        runner.step("runtime", runner.runtime)
        host_ok = runner.step("host", runner.host_page)
        runner.step("queue", runner.queue)
        if host_ok and runner.step("skill", runner.skill):
            if runner.step("data", runner.data):
                runner.step("recovery", runner.recovery)
        if args.network:
            runner.step("network", runner.network)
    finally:
        runner.stop_host()


if __name__ == "__main__":
    main()
