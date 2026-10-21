"""Isolated real crawler entrypoint with a deterministic platform adapter."""

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import httpx

import config
from media_platform.douyin.core import DouYinCrawler
from media_platform.douyin.client import DouYinClient
from media_platform.douyin import client as client_module


root, job_id, scenario_path = sys.argv[1:]
scenario = json.loads(Path(scenario_path).read_text(encoding="utf-8"))
directory = Path(root) / "runs" / job_id
os.environ.update(MEDIAWORKBENCH_JOB_ID=job_id, MEDIAWORKBENCH_LOGIN_DIR=str(directory))
config.KEYWORDS = "AI Agent"
config.START_PAGE = 1
config.CRAWLER_MAX_NOTES_COUNT = scenario.get("limit", 0)
config.CRAWLER_MAX_SLEEP_SEC = 0
config.ENABLE_GET_COMMENTS = "comments_http" in scenario
config.ENABLE_GET_SUB_COMMENTS = scenario.get("replies", False)
config.ENABLE_GET_MEDIA = False
config.LEAD_MODE = False
config.SAVE_DATA_PATH = str(directory)
config.SAVE_DATA_OPTION = "jsonl"
responses = iter(scenario["responses"])


async def search_info_by_keyword(**kwargs):
    return next(responses)


crawler = DouYinCrawler()
if config.ENABLE_GET_COMMENTS:
    responses_http = iter(scenario["comments_http"])

    def respond(request):
        item = next(responses_http)
        return httpx.Response(item.get("http_status", 200), content=item.get("body", ""),headers=item.get('headers',{}), request=request)

    client_module.make_async_client = lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond))
    crawler.dy_client = DouYinClient(headers={}, playwright_page=None, cookie_dict={})
    crawler.dy_client.search_info_by_keyword = search_info_by_keyword

    async def comments(aweme_id, cursor):
        return await crawler.dy_client.request("GET", "https://www.douyin.com/aweme/v1/web/comment/list/")

    crawler.dy_client.get_aweme_comments = comments

    async def replies(aweme_id, comment_id, cursor):
        return await crawler.dy_client.request("GET", "https://www.douyin.com/aweme/v1/web/comment/list/reply/")

    crawler.dy_client.get_sub_comments = replies
else:
    crawler.dy_client = SimpleNamespace(search_info_by_keyword=search_info_by_keyword)
asyncio.run(crawler.search())
if scenario.get("corrupt_outcome"):
    with (directory / f"collection-{os.environ['MEDIAWORKBENCH_ATTEMPT']}.jsonl").open("ab") as stream:
        stream.write(b"\xff\n")
