"""Inherited parsed-comment paths with controlled page/parser adapters."""

import asyncio
import os
import sys
from types import SimpleNamespace

platform, job_id = sys.argv[1:]
os.environ["MEDIAWORKBENCH_JOB_ID"] = job_id


async def main():
    if platform == "wb":
        import config
        from media_platform.weibo.client import WeiboClient

        config.ENABLE_GET_SUB_COMMENTS = True
        await WeiboClient.get_comments_all_sub_comments(
            "n1",
            [{"id": "p1", "total_number": 3, "comments": [{"id": "c1"}]}],
            callback,
        )
    else:
        from media_platform.tieba.client import BaiduTieBaClient

        client = BaiduTieBaClient(headers={}, playwright_page=object())

        async def network(**kwargs):
            raise TimeoutError("controlled external timeout")

        client._get_pc_page_data = network
        await client.get_note_all_comments(
            SimpleNamespace(note_id="n1", total_replay_page=1), crawl_interval=0
        )


async def callback(*args):
    pass


asyncio.run(main())
