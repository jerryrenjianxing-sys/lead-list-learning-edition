from unittest.mock import AsyncMock

import pytest

import config
from media_platform.bilibili.client import BilibiliClient
from media_platform.douyin.client import DouYinClient
from media_platform.kuaishou.client import KuaiShouClient
from media_platform.tieba.client import BaiduTieBaClient
from media_platform.weibo.client import WeiboClient
from media_platform.xhs.client import XiaoHongShuClient
from media_platform.zhihu.client import ZhiHuClient
from model.m_baidu_tieba import TiebaComment, TiebaNote
from model.m_zhihu import ZhihuContent


@pytest.mark.asyncio
async def test_douyin_zero_consumes_all_pages_positive_truncates_and_empty_stops():
    async def crawl(max_count):
        client = DouYinClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
        client.get_aweme_comments = AsyncMock(
            side_effect=[
                {
                    "has_more": 1,
                    "cursor": 1,
                    "comments": [
                        {"cid": "d1", "reply_comment_total": 0},
                        {"cid": "d2", "reply_comment_total": 0},
                    ],
                },
                {
                    "has_more": 1,
                    "cursor": 2,
                    "comments": [
                        {"cid": "d3", "reply_comment_total": 0},
                        {"cid": "d4", "reply_comment_total": 0},
                    ],
                },
                {"has_more": 1, "cursor": 3, "comments": []},
            ]
        )

        result = await client.get_aweme_all_comments(
            "video", crawl_interval=0, max_count=max_count
        )
        return [comment["cid"] for comment in result], client.get_aweme_comments.await_count

    assert await crawl(0) == (["d1", "d2", "d3", "d4"], 3)
    assert await crawl(3) == (["d1", "d2", "d3"], 2)


@pytest.mark.asyncio
async def test_xhs_zero_consumes_all_pages_positive_truncates_and_empty_stops(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", False)

    async def crawl(max_count):
        client = XiaoHongShuClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
        client.get_note_comments = AsyncMock(
            side_effect=[
                {
                    "has_more": True,
                    "cursor": "1",
                    "comments": [
                        {"id": "x1", "note_id": "note"},
                        {"id": "x2", "note_id": "note"},
                    ],
                },
                {
                    "has_more": True,
                    "cursor": "2",
                    "comments": [
                        {"id": "x3", "note_id": "note"},
                        {"id": "x4", "note_id": "note"},
                    ],
                },
                {"has_more": True, "cursor": "3", "comments": []},
            ]
        )

        result = await client.get_note_all_comments(
            "note", "token", crawl_interval=0, max_count=max_count
        )
        return [comment["id"] for comment in result], client.get_note_comments.await_count

    assert await crawl(0) == (["x1", "x2", "x3", "x4"], 3)
    assert await crawl(3) == (["x1", "x2", "x3"], 2)


@pytest.mark.asyncio
async def test_xhs_zero_does_not_override_sub_comment_switch(monkeypatch):
    async def crawl(enabled):
        monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", enabled)
        client = XiaoHongShuClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
        client.get_note_comments = AsyncMock(
            return_value={
                "has_more": False,
                "comments": [
                    {
                        "id": "root",
                        "note_id": "note",
                        "sub_comment_has_more": True,
                        "sub_comment_cursor": "next",
                    }
                ],
            }
        )
        client.get_note_sub_comments = AsyncMock(
            return_value={
                "has_more": False,
                "comments": [{"id": "reply", "note_id": "note"}],
            }
        )

        result = await client.get_note_all_comments(
            "note", "token", crawl_interval=0, max_count=0
        )
        return [comment["id"] for comment in result], client.get_note_sub_comments.await_count

    assert await crawl(False) == (["root"], 0)
    assert await crawl(True) == (["root", "reply"], 1)


@pytest.mark.asyncio
async def test_kuaishou_zero_consumes_all_cursors_positive_truncates_and_empty_stops(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", False)

    async def crawl(max_count):
        client = KuaiShouClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
        client.get_video_comments = AsyncMock(
            side_effect=[
                {
                    "pcursorV2": "1",
                    "rootCommentsV2": [{"comment_id": "k1"}, {"comment_id": "k2"}],
                },
                {
                    "pcursorV2": "2",
                    "rootCommentsV2": [{"comment_id": "k3"}, {"comment_id": "k4"}],
                },
                {"pcursorV2": "3", "rootCommentsV2": []},
            ]
        )

        result = await client.get_video_all_comments(
            "video", crawl_interval=0, max_count=max_count
        )
        return [
            comment["comment_id"] for comment in result
        ], client.get_video_comments.await_count

    assert await crawl(0) == (["k1", "k2", "k3", "k4"], 3)
    assert await crawl(3) == (["k1", "k2", "k3"], 2)


@pytest.mark.asyncio
async def test_bilibili_zero_consumes_all_pages_positive_truncates_and_empty_stops():
    async def crawl(max_count):
        client = BilibiliClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
        client.get_video_comments = AsyncMock(
            side_effect=[
                {
                    "cursor": {"is_end": False, "next": 1},
                    "replies": [{"rpid": "b1"}, {"rpid": "b2"}],
                },
                {
                    "cursor": {"is_end": False, "next": 2},
                    "replies": [{"rpid": "b3"}, {"rpid": "b4"}],
                },
                {"cursor": {"is_end": False, "next": 3}, "replies": []},
            ]
        )

        result = await client.get_video_all_comments(
            "video", crawl_interval=0, max_count=max_count
        )
        return [comment["rpid"] for comment in result], client.get_video_comments.await_count

    assert await crawl(0) == (["b1", "b2", "b3", "b4"], 3)
    assert await crawl(3) == (["b1", "b2", "b3"], 2)


@pytest.mark.asyncio
async def test_weibo_zero_consumes_all_pages_positive_truncates_and_empty_stops(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", False)

    async def crawl(max_count):
        client = WeiboClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
        client.get_note_comments = AsyncMock(
            side_effect=[
                {"max_id": 1, "max_id_type": 0, "data": [{"id": "w1"}, {"id": "w2"}]},
                {"max_id": 2, "max_id_type": 0, "data": [{"id": "w3"}, {"id": "w4"}]},
                {"max_id": 3, "max_id_type": 0, "data": []},
            ]
        )

        result = await client.get_note_all_comments(
            "note", crawl_interval=0, max_count=max_count
        )
        return [comment["id"] for comment in result], client.get_note_comments.await_count

    assert await crawl(0) == (["w1", "w2", "w3", "w4"], 3)
    assert await crawl(3) == (["w1", "w2", "w3"], 2)


def _tieba_comment(comment_id, note):
    return TiebaComment(
        comment_id=comment_id,
        content="comment",
        note_id=note.note_id,
        note_url=note.note_url,
        tieba_id="1",
        tieba_name=note.tieba_name,
        tieba_link=note.tieba_link,
    )


@pytest.mark.asyncio
async def test_tieba_zero_consumes_all_pages_positive_truncates_and_empty_stops(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", False)
    note = TiebaNote(
        note_id="note",
        title="title",
        note_url="https://tieba.baidu.com/p/note",
        tieba_name="test",
        tieba_link="https://tieba.baidu.com/f?kw=test",
        total_replay_page=3,
    )

    async def crawl(max_count):
        client = BaiduTieBaClient(playwright_page=AsyncMock())
        pages = []

        async def get_page(note_id, page=1):
            pages.append(page)
            return {"page": page}

        def extract(api_data, note_detail):
            page = api_data["page"]
            if page == 3:
                return []
            return [
                _tieba_comment(f"t{page}a", note_detail),
                _tieba_comment(f"t{page}b", note_detail),
            ]

        client._get_pc_page_data = get_page
        client._page_extractor.extract_tieba_note_parent_comments_from_api = extract
        result = await client.get_note_all_comments(
            note, crawl_interval=0, max_count=max_count
        )
        return [comment.comment_id for comment in result], pages

    assert await crawl(0) == (["t1a", "t1b", "t2a", "t2b"], [1, 2, 3])
    assert await crawl(3) == (["t1a", "t1b", "t2a"], [1, 2])


@pytest.mark.asyncio
async def test_zhihu_existing_pagination_consumes_all_pages_and_stops_on_empty(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", False)
    client = ZhiHuClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
    client.get_root_comments = AsyncMock(
        side_effect=[
            {
                "paging": {"is_end": False, "offset": "1"},
                "data": [{"comment_id": "z1"}, {"comment_id": "z2"}],
            },
            {
                "paging": {"is_end": False, "offset": "2"},
                "data": [{"comment_id": "z3"}, {"comment_id": "z4"}],
            },
            {"paging": {"is_end": False, "offset": "3"}, "data": []},
        ]
    )
    client._extractor.extract_offset = lambda paging: paging["offset"]
    client._extractor.extract_comments = lambda content, data: data

    result = await client.get_note_all_comments(
        ZhihuContent(content_id="content", content_type="answer"),
        crawl_interval=0,
    )

    assert [comment["comment_id"] for comment in result] == ["z1", "z2", "z3", "z4"]
    assert client.get_root_comments.await_count == 3


@pytest.mark.asyncio
async def test_douyin_partial_overlap_continues_then_repeated_page_stops():
    client = DouYinClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
    repeated_page = {
        "has_more": 1,
        "cursor": 3,
        "comments": [
            {"cid": "d2", "reply_comment_total": 0},
            {"cid": "d3", "reply_comment_total": 0},
        ],
    }
    client.get_aweme_comments = AsyncMock(
        side_effect=[
            {
                "has_more": 1,
                "cursor": 1,
                "comments": [
                    {"cid": "d1", "reply_comment_total": 0},
                    {"cid": "d2", "reply_comment_total": 0},
                ],
            },
            {**repeated_page, "cursor": 2},
            repeated_page,
            AssertionError("repeated page must stop pagination"),
        ]
    )

    result = await client.get_aweme_all_comments("video", crawl_interval=0, max_count=0)

    assert client.get_aweme_comments.await_count == 3
    assert [comment["cid"] for comment in result] == ["d1", "d2", "d3"]


@pytest.mark.asyncio
async def test_douyin_stalled_cursor_keeps_new_page_then_stops():
    client = DouYinClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
    client.get_aweme_comments = AsyncMock(
        side_effect=[
            {
                "has_more": 1,
                "cursor": 1,
                "comments": [{"cid": "d1", "reply_comment_total": 0}],
            },
            {
                "has_more": 1,
                "cursor": 1,
                "comments": [{"cid": "d2", "reply_comment_total": 0}],
            },
            AssertionError("stalled cursor must stop pagination"),
        ]
    )

    result = await client.get_aweme_all_comments("video", crawl_interval=0, max_count=0)

    assert [comment["cid"] for comment in result] == ["d1", "d2"]
    assert client.get_aweme_comments.await_count == 2


@pytest.mark.asyncio
async def test_douyin_zero_does_not_override_sub_comment_switch():
    async def crawl(enabled):
        client = DouYinClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
        client.get_aweme_comments = AsyncMock(
            return_value={
                "has_more": 0,
                "cursor": 1,
                "comments": [{"cid": "root", "reply_comment_total": 1}],
            }
        )
        client.get_sub_comments = AsyncMock(
            return_value={
                "has_more": 0,
                "cursor": 1,
                "comments": [{"cid": "reply"}],
            }
        )

        result = await client.get_aweme_all_comments(
            "video",
            crawl_interval=0,
            is_fetch_sub_comments=enabled,
            max_count=0,
        )
        return [comment["cid"] for comment in result], client.get_sub_comments.await_count

    assert await crawl(False) == (["root"], 0)
    assert await crawl(True) == (["root", "reply"], 1)


@pytest.mark.asyncio
async def test_douyin_sub_comments_empty_page_stops():
    client = DouYinClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
    client.get_aweme_comments = AsyncMock(
        return_value={
            "has_more": 0,
            "cursor": 1,
            "comments": [{"cid": "root", "reply_comment_total": 1}],
        }
    )
    client.get_sub_comments = AsyncMock(
        side_effect=[
            {"has_more": 1, "cursor": 1, "comments": []},
            AssertionError("empty sub-comment page must stop pagination"),
        ]
    )

    result = await client.get_aweme_all_comments(
        "video",
        crawl_interval=0,
        is_fetch_sub_comments=True,
        max_count=0,
    )

    assert [comment["cid"] for comment in result] == ["root"]
    assert client.get_sub_comments.await_count == 1


@pytest.mark.asyncio
async def test_douyin_idless_root_pages_follow_platform_end_marker():
    client = DouYinClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
    client.get_aweme_comments = AsyncMock(
        side_effect=[
            {
                "has_more": 1,
                "cursor": 1,
                "comments": [{"cid": None, "text": "first", "reply_comment_total": 0}],
            },
            {
                "has_more": 0,
                "cursor": 2,
                "comments": [{"cid": None, "text": "second", "reply_comment_total": 0}],
            },
        ]
    )

    result = await client.get_aweme_all_comments("video", crawl_interval=0, max_count=0)

    assert [comment["text"] for comment in result] == ["first", "second"]
    assert client.get_aweme_comments.await_count == 2


@pytest.mark.asyncio
async def test_xhs_partial_overlap_continues_then_repeated_page_stops(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", False)
    client = XiaoHongShuClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
    repeated_page = {
        "has_more": True,
        "cursor": "3",
        "comments": [
            {"id": "x2", "note_id": "note"},
            {"id": "x3", "note_id": "note"},
        ],
    }
    client.get_note_comments = AsyncMock(
        side_effect=[
            {
                "has_more": True,
                "cursor": "1",
                "comments": [
                    {"id": "x1", "note_id": "note"},
                    {"id": "x2", "note_id": "note"},
                ],
            },
            {**repeated_page, "cursor": "2"},
            repeated_page,
            AssertionError("repeated page must stop pagination"),
        ]
    )

    result = await client.get_note_all_comments(
        "note", "token", crawl_interval=0, max_count=0
    )

    assert client.get_note_comments.await_count == 3
    assert [comment["id"] for comment in result] == ["x1", "x2", "x3"]


@pytest.mark.asyncio
async def test_xhs_stalled_cursor_keeps_new_page_then_stops(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", False)
    client = XiaoHongShuClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
    client.get_note_comments = AsyncMock(
        side_effect=[
            {
                "has_more": True,
                "cursor": "1",
                "comments": [{"id": "x1", "note_id": "note"}],
            },
            {
                "has_more": True,
                "cursor": "1",
                "comments": [{"id": "x2", "note_id": "note"}],
            },
            AssertionError("stalled cursor must stop pagination"),
        ]
    )

    result = await client.get_note_all_comments(
        "note", "token", crawl_interval=0, max_count=0
    )

    assert [comment["id"] for comment in result] == ["x1", "x2"]
    assert client.get_note_comments.await_count == 2


@pytest.mark.asyncio
async def test_xhs_sub_comments_partial_overlap_then_repeated_page_stops(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", True)
    client = XiaoHongShuClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
    callback = AsyncMock()
    client.get_note_comments = AsyncMock(
        return_value={
            "has_more": False,
            "comments": [
                {
                    "id": "root",
                    "note_id": "note",
                    "sub_comment_has_more": True,
                    "sub_comment_cursor": "start",
                }
            ],
        }
    )
    client.get_note_sub_comments = AsyncMock(
        side_effect=[
            {
                "has_more": True,
                "cursor": "1",
                "comments": [
                    {"id": "s1", "note_id": "note"},
                    {"id": "s2", "note_id": "note"},
                ],
            },
            {
                "has_more": True,
                "cursor": "2",
                "comments": [
                    {"id": "s2", "note_id": "note"},
                    {"id": "s3", "note_id": "note"},
                ],
            },
            {
                "has_more": True,
                "cursor": "3",
                "comments": [
                    {"id": "s2", "note_id": "note"},
                    {"id": "s3", "note_id": "note"},
                ],
            },
            AssertionError("repeated sub-comment page must stop pagination"),
        ]
    )

    result = await client.get_note_all_comments(
        "note", "token", crawl_interval=0, callback=callback, max_count=0
    )

    assert [comment["id"] for comment in result] == ["root", "s1", "s2", "s3"]
    assert client.get_note_sub_comments.await_count == 3
    assert [
        [comment["id"] for comment in call.args[1]]
        for call in callback.await_args_list
    ] == [["root"], ["s1", "s2"], ["s3"]]


@pytest.mark.asyncio
async def test_kuaishou_partial_overlap_continues_then_repeated_page_stops(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", False)
    client = KuaiShouClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
    repeated_page = {
        "pcursorV2": "3",
        "rootCommentsV2": [{"comment_id": "k2"}, {"comment_id": "k3"}],
    }
    client.get_video_comments = AsyncMock(
        side_effect=[
            {
                "pcursorV2": "1",
                "rootCommentsV2": [{"comment_id": "k1"}, {"comment_id": "k2"}],
            },
            {**repeated_page, "pcursorV2": "2"},
            repeated_page,
            AssertionError("repeated page must stop pagination"),
        ]
    )

    result = await client.get_video_all_comments("video", crawl_interval=0, max_count=0)

    assert client.get_video_comments.await_count == 3
    assert [comment["comment_id"] for comment in result] == ["k1", "k2", "k3"]


@pytest.mark.asyncio
async def test_kuaishou_stalled_cursor_keeps_new_page_then_stops(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", False)
    client = KuaiShouClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
    client.get_video_comments = AsyncMock(
        side_effect=[
            {"pcursorV2": "1", "rootCommentsV2": [{"comment_id": "k1"}]},
            {"pcursorV2": "1", "rootCommentsV2": [{"comment_id": "k2"}]},
            AssertionError("stalled cursor must stop pagination"),
        ]
    )

    result = await client.get_video_all_comments("video", crawl_interval=0, max_count=0)

    assert [comment["comment_id"] for comment in result] == ["k1", "k2"]
    assert client.get_video_comments.await_count == 2


@pytest.mark.asyncio
async def test_kuaishou_sub_comments_stalled_cursor_keeps_new_page_then_stops(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", True)
    client = KuaiShouClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
    callback = AsyncMock()
    client.get_video_comments = AsyncMock(
        return_value={
            "pcursorV2": "no_more",
            "rootCommentsV2": [{"comment_id": "root", "hasSubComments": True}],
        }
    )
    client.get_video_sub_comments = AsyncMock(
        side_effect=[
            {"pcursorV2": "1", "subCommentsV2": [{"comment_id": "s1"}]},
            {"pcursorV2": "1", "subCommentsV2": [{"comment_id": "s2"}]},
            AssertionError("stalled sub-comment cursor must stop pagination"),
        ]
    )

    result = await client.get_video_all_comments(
        "video", crawl_interval=0, callback=callback, max_count=0
    )

    assert [comment["comment_id"] for comment in result] == ["root", "s1", "s2"]
    assert client.get_video_sub_comments.await_count == 2
    assert [
        [comment["comment_id"] for comment in call.args[1]]
        for call in callback.await_args_list
    ] == [["root"], ["s1"], ["s2"]]


@pytest.mark.asyncio
async def test_bilibili_partial_overlap_continues_then_repeated_page_stops():
    client = BilibiliClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
    repeated_page = {
        "cursor": {"is_end": False, "next": 3},
        "replies": [{"rpid": "b2"}, {"rpid": "b3"}],
    }
    client.get_video_comments = AsyncMock(
        side_effect=[
            {
                "cursor": {"is_end": False, "next": 1},
                "replies": [{"rpid": "b1"}, {"rpid": "b2"}],
            },
            {
                **repeated_page,
                "cursor": {"is_end": False, "next": 2},
            },
            repeated_page,
            AssertionError("repeated page must stop pagination"),
        ]
    )

    result = await client.get_video_all_comments("video", crawl_interval=0, max_count=0)

    assert client.get_video_comments.await_count == 3
    assert [comment["rpid"] for comment in result] == ["b1", "b2", "b3"]


@pytest.mark.asyncio
async def test_bilibili_stalled_page_keeps_new_page_then_stops():
    client = BilibiliClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
    client.get_video_comments = AsyncMock(
        side_effect=[
            {
                "cursor": {"is_end": False, "next": 1},
                "replies": [{"rpid": "b1"}],
            },
            {
                "cursor": {"is_end": False, "next": 1},
                "replies": [{"rpid": "b2"}],
            },
            AssertionError("stalled page must stop pagination"),
        ]
    )

    result = await client.get_video_all_comments("video", crawl_interval=0, max_count=0)

    assert [comment["rpid"] for comment in result] == ["b1", "b2"]
    assert client.get_video_comments.await_count == 2


@pytest.mark.asyncio
async def test_weibo_partial_overlap_continues_then_repeated_page_stops(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", False)
    client = WeiboClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
    repeated_page = {
        "max_id": 3,
        "max_id_type": 0,
        "data": [{"id": "w2"}, {"id": "w3"}],
    }
    client.get_note_comments = AsyncMock(
        side_effect=[
            {
                "max_id": 1,
                "max_id_type": 0,
                "data": [{"id": "w1"}, {"id": "w2"}],
            },
            {**repeated_page, "max_id": 2},
            repeated_page,
            AssertionError("repeated page must stop pagination"),
        ]
    )

    result = await client.get_note_all_comments("note", crawl_interval=0, max_count=0)

    assert client.get_note_comments.await_count == 3
    assert [comment["id"] for comment in result] == ["w1", "w2", "w3"]


@pytest.mark.asyncio
async def test_weibo_stalled_cursor_keeps_new_page_then_stops(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", False)
    client = WeiboClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
    client.get_note_comments = AsyncMock(
        side_effect=[
            {"max_id": 1, "max_id_type": 0, "data": [{"id": "w1"}]},
            {"max_id": 1, "max_id_type": 0, "data": [{"id": "w2"}]},
            AssertionError("stalled cursor must stop pagination"),
        ]
    )

    result = await client.get_note_all_comments("note", crawl_interval=0, max_count=0)

    assert [comment["id"] for comment in result] == ["w1", "w2"]
    assert client.get_note_comments.await_count == 2


@pytest.mark.asyncio
async def test_bilibili_limit_filters_parents_before_optional_sub_comments():
    async def crawl(enabled):
        client = BilibiliClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
        client.get_video_comments = AsyncMock(
            return_value={
                "cursor": {"is_end": False, "next": 1},
                "replies": [
                    {"rpid": "b1", "rcount": 1},
                    {"rpid": "b2", "rcount": 1},
                ],
            }
        )
        client.get_video_all_level_two_comments = AsyncMock()

        result = await client.get_video_all_comments(
            "video",
            crawl_interval=0,
            is_fetch_sub_comments=enabled,
            max_count=1,
        )
        return [comment["rpid"] for comment in result], [
            call.args[1]
            for call in client.get_video_all_level_two_comments.await_args_list
        ]

    assert await crawl(False) == (["b1"], [])
    assert await crawl(True) == (["b1"], ["b1"])
