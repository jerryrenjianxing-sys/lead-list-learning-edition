from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import config
from media_platform.bilibili.core import BilibiliCrawler
from media_platform.douyin.client import DouYinClient
from media_platform.kuaishou.client import KuaiShouClient
from media_platform.tieba.client import BaiduTieBaClient
from media_platform.weibo.client import WeiboClient
from media_platform.zhihu.client import ZhiHuClient


@pytest.mark.asyncio
async def test_douyin_creator_partial_overlap_continues_then_repeated_page_stops():
    client = object.__new__(DouYinClient)
    client.get_user_aweme_posts = AsyncMock(
        side_effect=[
            {"has_more": 1, "max_cursor": "1", "aweme_list": [{"aweme_id": "a"}, {"aweme_id": "b"}]},
            {"has_more": 1, "max_cursor": "2", "aweme_list": [{"aweme_id": "b"}, {"aweme_id": "c"}]},
            {"has_more": 1, "max_cursor": "3", "aweme_list": [{"aweme_id": "b"}, {"aweme_id": "c"}]},
        ]
    )

    result = await client.get_all_user_aweme_posts("creator")

    assert client.get_user_aweme_posts.await_count == 3
    assert {item["aweme_id"] for item in result} == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_kuaishou_creator_empty_page_stops_without_waiting_for_no_more():
    client = object.__new__(KuaiShouClient)
    client.get_video_by_creater_v2 = AsyncMock(
        side_effect=[
            {"result": 1, "pcursor": "next", "feeds": []},
            AssertionError("empty page must stop pagination"),
        ]
    )

    result = await client.get_all_videos_by_creator("creator", crawl_interval=0)

    assert result == []
    client.get_video_by_creater_v2.assert_awaited_once()


@pytest.mark.asyncio
async def test_weibo_creator_repeated_page_stops_before_reported_total():
    client = object.__new__(WeiboClient)
    page = {
        "cardlistInfo": {"since_id": "next", "total": 1000},
        "cards": [{"card_type": 9, "mblog": {"id": "w1"}}],
    }
    client.get_notes_by_creator = AsyncMock(side_effect=[page, page])

    result = await client.get_all_notes_by_creator_id(
        "creator", "container", crawl_interval=0
    )

    assert client.get_notes_by_creator.await_count == 2
    assert [item["mblog"]["id"] for item in result] == ["w1"]


@pytest.mark.asyncio
async def test_zhihu_creator_empty_page_stops_when_is_end_is_false():
    client = object.__new__(ZhiHuClient)
    client._extractor = Mock()
    client._extractor.extract_content_list_from_creator.return_value = []
    client.get_creator_answers = AsyncMock(
        side_effect=[
            {"paging": {"is_end": False}, "data": []},
            AssertionError("empty page must stop pagination"),
        ]
    )

    result = await client.get_all_anwser_by_creator("creator", crawl_interval=0)

    assert result == []
    client.get_creator_answers.assert_awaited_once()


@pytest.mark.asyncio
async def test_tieba_zero_fetches_multiple_pages_and_seen_is_per_creator():
    client = object.__new__(BaiduTieBaClient)
    client._extract_creator_portrait = lambda creator_url: creator_url
    client._page_extractor = Mock()
    client._page_extractor.extract_creator_thread_id_list_from_api.side_effect = (
        lambda response: response["ids"]
    )

    async def get_page(*, portrait, page_number, page_size):
        pages = {
            ("creator-1", 1): {"ids": ["t1"], "data": {"has_more": 1}},
            ("creator-1", 2): {"ids": ["t2"], "data": {"has_more": 0}},
            ("creator-2", 1): {"ids": ["t1"], "data": {"has_more": 0}},
        }
        return pages[(portrait, page_number)]

    client.get_notes_by_creator_portrait = AsyncMock(side_effect=get_page)
    client.get_note_by_id = AsyncMock(
        side_effect=lambda thread_id: SimpleNamespace(note_id=thread_id)
    )

    first = await client.get_all_notes_by_creator_url(
        "creator-1", crawl_interval=0, max_note_count=0
    )
    second = await client.get_all_notes_by_creator_url(
        "creator-2", crawl_interval=0, max_note_count=0
    )

    assert [note.note_id for note in first] == ["t1", "t2"]
    assert [note.note_id for note in second] == ["t1"]


@pytest.mark.asyncio
async def test_bilibili_creator_does_not_pass_aid_as_bvid(monkeypatch):
    monkeypatch.setattr(config, "CRAWLER_MAX_NOTES_COUNT", 0)
    crawler = object.__new__(BilibiliCrawler)
    crawler.bili_client = AsyncMock()
    crawler.bili_client.get_creator_videos.return_value = {
        "list": {"vlist": [{"aid": 123}]},
        "page": {"count": 1},
    }
    crawler.get_specified_videos = AsyncMock()

    await crawler.get_creator_videos(1)

    crawler.get_specified_videos.assert_not_awaited()
