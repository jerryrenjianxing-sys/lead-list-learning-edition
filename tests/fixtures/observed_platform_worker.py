"""Real inherited client methods with a controlled network adapter, no account."""

import asyncio
import importlib
import os
import sys

platform, job_id = sys.argv[1:]
os.environ["MEDIAWORKBENCH_JOB_ID"] = job_id
names = {
    "dy": ("douyin", "DouYinClient"),
    "xhs": ("xhs", "XiaoHongShuClient"),
    "ks": ("kuaishou", "KuaiShouClient"),
    "bili": ("bilibili", "BilibiliClient"),
    "wb": ("weibo", "WeiboClient"),
    "zhihu": ("zhihu", "ZhiHuClient"),
    "tieba": ("tieba", "BaiduTieBaClient"),
}
folder, name = names[platform]
klass = getattr(importlib.import_module("media_platform." + folder + ".client"), name)
options = {"headers": {}, "playwright_page": object()}
if platform != "tieba":
    options["cookie_dict"] = {}
client = klass(**options)
responses = {
    "dy": {"status_code": 0, "has_more": 0, "data": []},
    "xhs": {"items": [], "has_more": False},
    "ks": {"result": 1, "feeds": [], "pcursor": "no_more"},
    "bili": {"result": [], "numPages": 0, "page": 1},
    "wb": {"cards": [], "cardlistInfo": {"total": 0}},
    "zhihu": {"data": [], "paging": {"is_end": True}},
    "tieba": {"no": 0, "data": {"card_list": [], "has_more": False}},
}


async def network(*args, **kwargs):
    return responses[platform]


client.get = client.post = client.request_rest_v2_signed = (
    client._fetch_json_by_browser
) = network
methods = {
    "dy": "search_info_by_keyword",
    "xhs": "get_note_by_keyword",
    "ks": "search_info_by_keyword_v2",
    "bili": "search_video_by_keyword",
    "wb": "get_note_by_keyword",
    "zhihu": "get_note_by_keyword",
    "tieba": "get_notes_by_keyword",
}
args = ["fixture", ""] if platform == "ks" else ["fixture"]
asyncio.run(getattr(client, methods[platform])(*args))
