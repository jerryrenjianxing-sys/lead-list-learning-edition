import asyncio
import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import config
from media_platform.douyin.client import DouYinClient
from media_platform.douyin.core import DouYinCrawler
from media_platform.douyin.exception import DataFetchError
from store import douyin as douyin_store
from tools.lead_sink import LeadSink


@pytest.mark.asyncio
async def test_transport_failure_retries_then_becomes_data_fetch_error(monkeypatch):
    request = httpx.Request("GET", "https://www.douyin.com/aweme/v1/web/comment/list/reply/")
    transport_error = httpx.ConnectError("offline", request=request)
    http_client = AsyncMock()
    http_client.request.side_effect = transport_error
    context = AsyncMock()
    context.__aenter__.return_value = http_client
    monkeypatch.setattr("media_platform.douyin.client.make_async_client", Mock(return_value=context))
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    client = DouYinClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})

    with pytest.raises(DataFetchError, match="3 attempts"):
        await client.request("GET", str(request.url))

    assert http_client.request.await_count == 3


@pytest.mark.asyncio
async def test_lead_mode_keeps_native_comment_and_subcomment_results(monkeypatch):
    async def crawl(lead_mode):
        monkeypatch.setattr(config, "LEAD_MODE", lead_mode)
        client = DouYinClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
        client.get_aweme_comments = AsyncMock(
            return_value={
                "has_more": 0,
                "cursor": 20,
                "comments": [{"cid": "c1", "reply_comment_total": 1}],
            }
        )
        client.get_sub_comments = AsyncMock(
            return_value={
                "has_more": 0,
                "cursor": 20,
                "comments": [{"cid": "s1", "reply_id": "c1"}],
            }
        )

        result = await client.get_aweme_all_comments(
            "a1",
            crawl_interval=0,
            is_fetch_sub_comments=True,
            max_count=1,
        )
        return [item["cid"] for item in result], client.get_sub_comments.await_count

    assert await crawl(True) == await crawl(False) == (["c1", "s1"], 1)


@pytest.mark.asyncio
async def test_lead_mode_keeps_native_missing_comment_response_semantics(monkeypatch):
    async def crawl(lead_mode):
        monkeypatch.setattr(config, "LEAD_MODE", lead_mode)
        client = DouYinClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
        client.get_aweme_comments = AsyncMock(return_value={"has_more": 0})
        return await client.get_aweme_all_comments("a1", crawl_interval=0)

    assert await crawl(True) == await crawl(False) == []


@pytest.mark.asyncio
async def test_lead_search_uses_native_result_count_and_records_content(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LEAD_MODE", True)
    monkeypatch.setattr(config, "KEYWORDS", "雨衣")
    monkeypatch.setattr(config, "CRAWLER_MAX_NOTES_COUNT", 10)
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    crawler = DouYinCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "dy")
    crawler._search_leads = AsyncMock()
    crawler.dy_client = AsyncMock()
    crawler.dy_client.search_info_by_keyword.return_value = {
        "data": [
            {"aweme_info": {"aweme_id": "a1", "author": {}, "video": {"url": "media-secret"}}},
            *[{"aweme_info": {"aweme_id": item, "author": {}}} for item in ("a2", "a3")],
        ],
        "has_more": 0,
        "extra": {},
    }
    monkeypatch.setattr(douyin_store, "update_douyin_aweme", AsyncMock())
    crawler.get_aweme_media = AsyncMock()
    crawler.batch_get_note_comments = AsyncMock()

    await crawler.search()

    crawler._search_leads.assert_not_awaited()
    assert crawler.batch_get_note_comments.await_args.args[0] == ["a1", "a2", "a3"]
    assert crawler.lead_sink.content_count == 3
    assert "media-secret" not in (tmp_path / "raw.jsonl").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_lead_comments_keep_native_limit_and_subcomment_setting(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LEAD_MODE", True)
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", False)
    monkeypatch.setattr(config, "CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES", 7)
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    crawler = DouYinCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "dy", max_comments=1)
    crawler.lead_sink.record_dy_comments("old", [{"cid": "old", "user": {}}])
    crawler.dy_client = AsyncMock()
    monkeypatch.setattr(douyin_store, "batch_update_dy_aweme_comments", AsyncMock())

    await crawler.get_comments("a1", asyncio.Semaphore(1))

    kwargs = crawler.dy_client.get_aweme_all_comments.await_args.kwargs
    assert kwargs["max_count"] == 7
    assert kwargs["is_fetch_sub_comments"] is False


@pytest.mark.asyncio
async def test_lead_sidecar_does_not_swallow_regular_comment_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LEAD_MODE", True)
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    crawler = DouYinCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "dy")
    crawler.dy_client = AsyncMock()
    crawler.dy_client.get_aweme_all_comments.side_effect = RuntimeError("blocked")

    with pytest.raises(RuntimeError, match="blocked"):
        await crawler.get_comments("a1", asyncio.Semaphore(1))


@pytest.mark.asyncio
async def test_comment_sidecar_failure_keeps_native_store_and_crawl_running(monkeypatch):
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    crawler = DouYinCrawler()
    crawler.lead_sink = Mock()
    crawler.lead_sink.record_dy_comments.side_effect = OSError("disk full")
    crawler.dy_client = AsyncMock()
    native_store = AsyncMock()
    monkeypatch.setattr(douyin_store, "batch_update_dy_aweme_comments", native_store)

    async def get_comments(**kwargs):
        await kwargs["callback"]("a1", [{"cid": "c1"}])
        return [{"cid": "c1"}]

    crawler.dy_client.get_aweme_all_comments.side_effect = get_comments

    await crawler.get_comments("a1", asyncio.Semaphore(1))

    native_store.assert_awaited_once_with("a1", [{"cid": "c1"}])


@pytest.mark.asyncio
async def test_creator_content_is_recorded_without_breaking_native_store(monkeypatch):
    crawler = DouYinCrawler()
    crawler.lead_sink = Mock()
    crawler.lead_sink.record_content.side_effect = OSError("disk full")
    crawler.get_aweme_detail = AsyncMock(
        return_value={"aweme_id": "a1", "author": {"sec_uid": "owner"}}
    )
    crawler.get_aweme_media = AsyncMock()
    native_store = AsyncMock()
    monkeypatch.setattr(douyin_store, "update_douyin_aweme", native_store)

    await crawler.fetch_creator_video_detail([{"aweme_id": "a1"}])

    native_store.assert_awaited_once()
    crawler.lead_sink.record_content.assert_called_once_with(
        "a1",
        {"aweme_id": "a1", "author": {"sec_uid": "owner"}},
        source_keyword="",
        source_url="https://www.douyin.com/video/a1",
    )


@pytest.mark.asyncio
async def test_lead_batch_uses_native_shared_concurrency(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LEAD_MODE", True)
    crawler = DouYinCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "dy", max_comments=1)
    crawler.lead_sink.record_dy_comments("old", [{"cid": "old", "user": {}}])
    crawler.get_comments = AsyncMock()

    await crawler.batch_get_note_comments(["a1", "a2", "a3"])

    assert [call.args[0] for call in crawler.get_comments.await_args_list] == ["a1", "a2", "a3"]
    assert len({id(call.args[1]) for call in crawler.get_comments.await_args_list}) == 1


@pytest.mark.asyncio
async def test_profile_collection_does_not_fetch_posts(tmp_path):
    crawler = DouYinCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "dy", deep_profile_limit=1)
    crawler.lead_sink.record_dy_comments("a1", [{"cid": "c1", "text": "采购报价", "user": {"sec_uid": "buyer"}}])
    crawler.dy_client = AsyncMock()
    crawler._collect_lead_profile_refs = AsyncMock()

    await crawler.collect_lead_profiles()

    crawler._collect_lead_profile_refs.assert_awaited_once_with(crawler.lead_sink.profile_references())
    crawler.dy_client.get_user_aweme_posts.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_collection_marks_recovery_after_account_block(
    monkeypatch, tmp_path
):
    crawler = DouYinCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "dy")
    crawler.lead_sink.record_dy_comments(
        "a1",
        [
            {"cid": "c1", "text": "采购报价", "user": {"sec_uid": "u1"}},
            {"cid": "c2", "text": "需要定制", "user": {"sec_uid": "u2"}},
            {"cid": "c3", "text": "批量拿货", "user": {"sec_uid": "u3"}},
        ],
    )
    crawler.dy_client = AsyncMock()
    crawler.dy_client.get_user_info.side_effect = [
        {"user": {"unique_id": "buyer"}},
        DataFetchError("account blocked"),
    ]
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    with pytest.raises(DataFetchError, match="account blocked"):
        await crawler._collect_lead_profile_refs(crawler.lead_sink.profile_references())

    assert [call.args[0] for call in crawler.dy_client.get_user_info.await_args_list] == ["u1", "u2"]
    sleep.assert_awaited_once_with(config.CRAWLER_MAX_SLEEP_SEC)
    assert json.loads(
        (tmp_path / "profile_resume_required.json").read_text(encoding="utf-8")
    ) == {"platform": "dy", "reason": "risk_control"}
