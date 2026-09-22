import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

import config
from media_platform.xhs.client import XiaoHongShuClient
from media_platform.xhs.core import XiaoHongShuCrawler
from store import xhs as xhs_store
from tools.lead_sink import LeadSink


@pytest.mark.asyncio
async def test_lead_mode_keeps_native_comment_and_subcomment_results(monkeypatch):
    async def crawl(lead_mode):
        monkeypatch.setattr(config, "LEAD_MODE", lead_mode)
        monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", True)
        client = XiaoHongShuClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
        client.get_note_comments = AsyncMock(
            return_value={
                "has_more": False,
                "comments": [
                    {
                        "id": "c1",
                        "note_id": "n1",
                        "sub_comment_has_more": True,
                        "sub_comment_cursor": "next",
                    }
                ],
            }
        )
        client.get_note_sub_comments = AsyncMock(
            return_value={
                "has_more": False,
                "comments": [{"id": "s1", "note_id": "n1"}],
            }
        )

        result = await client.get_note_all_comments(
            "n1",
            "route-secret",
            crawl_interval=0,
            max_count=1,
        )
        return [item["id"] for item in result], client.get_note_sub_comments.await_count

    assert await crawl(True) == await crawl(False) == (["c1", "s1"], 1)


@pytest.mark.asyncio
async def test_lead_mode_keeps_native_missing_response_semantics(monkeypatch):
    async def crawl(lead_mode):
        monkeypatch.setattr(config, "LEAD_MODE", lead_mode)
        monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", True)
        client = XiaoHongShuClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
        client.get_note_comments = AsyncMock(return_value={"has_more": False})
        top_level = await client.get_note_all_comments(
            "n1", "route-secret", crawl_interval=0
        )
        client.get_note_sub_comments = AsyncMock(return_value={"has_more": False})
        sub_level = await client.get_comments_all_sub_comments(
            comments=[
                {
                    "id": "c1",
                    "note_id": "n1",
                    "sub_comment_has_more": True,
                    "sub_comment_cursor": "next",
                }
            ],
            xsec_token="route-secret",
            crawl_interval=0,
        )
        return top_level, sub_level

    assert await crawl(True) == await crawl(False) == ([], [])


@pytest.mark.asyncio
async def test_lead_search_uses_native_result_count_and_records_content(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LEAD_MODE", True)
    monkeypatch.setattr(config, "KEYWORDS", "雨衣")
    monkeypatch.setattr(config, "CRAWLER_MAX_NOTES_COUNT", 20)
    monkeypatch.setattr(config, "START_PAGE", 1)
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    crawler = XiaoHongShuCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "xhs")
    crawler._search_leads = AsyncMock()
    crawler.xhs_client = AsyncMock()
    crawler.xhs_client.get_note_by_keyword.return_value = {
        "has_more": True,
        "items": [{"id": item, "xsec_token": f"token-{item}"} for item in ("n1", "n2", "n3")],
    }
    crawler.get_note_detail_async_task = AsyncMock(side_effect=lambda note_id, xsec_token, **_: {"note_id": note_id, "xsec_token": xsec_token, "user": {}, "image_list": [{"url": "media-secret"}]})
    monkeypatch.setattr(xhs_store, "update_xhs_note", AsyncMock())
    crawler.get_notice_media = AsyncMock()
    crawler.batch_get_note_comments = AsyncMock()

    await crawler.search()

    crawler._search_leads.assert_not_awaited()
    assert crawler.batch_get_note_comments.await_args.args[0] == ["n1", "n2", "n3"]
    assert crawler.lead_sink.content_count == 3
    assert "media-secret" not in (tmp_path / "raw.jsonl").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_resume_retries_comments_without_rewriting_existing_content(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LEAD_MODE", True)
    monkeypatch.setattr(config, "KEYWORDS", "雨衣")
    monkeypatch.setattr(config, "CRAWLER_MAX_NOTES_COUNT", 20)
    monkeypatch.setattr(config, "START_PAGE", 1)
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    crawler = XiaoHongShuCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "xhs")
    crawler.lead_sink.record_content(
        "n1", {"note_id": "n1"}, source_keyword="雨衣", source_url=""
    )
    crawler.xhs_client = AsyncMock()
    crawler.xhs_client.get_note_by_keyword.return_value = {
        "has_more": True,
        "items": [{"id": "n1", "xsec_token": "token-n1"}],
    }
    crawler.get_note_detail_async_task = AsyncMock(
        return_value={"note_id": "n1", "xsec_token": "token-n1", "user": {}}
    )
    native_store = AsyncMock()
    monkeypatch.setattr(xhs_store, "update_xhs_note", native_store)
    crawler.get_notice_media = AsyncMock()
    crawler.batch_get_note_comments = AsyncMock()

    await crawler.search()

    native_store.assert_not_awaited()
    crawler.get_notice_media.assert_not_awaited()
    crawler.batch_get_note_comments.assert_awaited_once_with(["n1"], ["token-n1"])


@pytest.mark.asyncio
async def test_lead_comments_keep_native_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LEAD_MODE", True)
    monkeypatch.setattr(config, "CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES", 7)
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    crawler = XiaoHongShuCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "xhs", max_comments=1)
    crawler.lead_sink.record_xhs_comments("old", [{"id": "old", "user_info": {}}])
    crawler.xhs_client = AsyncMock()
    monkeypatch.setattr(xhs_store, "batch_update_xhs_note_comments", AsyncMock())

    await crawler.get_comments("n1", "route-secret", asyncio.Semaphore(1))

    assert crawler.xhs_client.get_note_all_comments.await_args.kwargs["max_count"] == 7


@pytest.mark.asyncio
async def test_lead_sidecar_does_not_swallow_regular_comment_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LEAD_MODE", True)
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    crawler = XiaoHongShuCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "xhs")
    crawler.xhs_client = AsyncMock()
    crawler.xhs_client.get_note_all_comments.side_effect = RuntimeError("blocked")

    with pytest.raises(RuntimeError, match="blocked"):
        await crawler.get_comments("n1", "route-secret", asyncio.Semaphore(1))

    assert not crawler.lead_sink.is_completed("n1")


@pytest.mark.asyncio
async def test_successful_comment_crawl_checkpoints_note(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    crawler = XiaoHongShuCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "xhs")
    crawler.xhs_client = AsyncMock()
    crawler.xhs_client.get_note_all_comments.return_value = []

    await crawler.get_comments("n1", "route-secret", asyncio.Semaphore(1))

    assert crawler.lead_sink.is_completed("n1")


@pytest.mark.asyncio
async def test_comment_sidecar_failure_keeps_native_store_and_crawl_running(monkeypatch):
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    crawler = XiaoHongShuCrawler()
    crawler.lead_sink = Mock()
    crawler.lead_sink.record_xhs_comments.side_effect = OSError("disk full")
    crawler.xhs_client = AsyncMock()
    native_store = AsyncMock()
    monkeypatch.setattr(xhs_store, "batch_update_xhs_note_comments", native_store)

    async def get_comments(**kwargs):
        await kwargs["callback"]("n1", [{"id": "c1"}])
        return [{"id": "c1"}]

    crawler.xhs_client.get_note_all_comments.side_effect = get_comments

    await crawler.get_comments("n1", "route-secret", asyncio.Semaphore(1))

    native_store.assert_awaited_once_with("n1", [{"id": "c1"}])


@pytest.mark.asyncio
async def test_creator_content_is_recorded_without_breaking_native_store(monkeypatch):
    crawler = XiaoHongShuCrawler()
    crawler.lead_sink = Mock()
    crawler.lead_sink.record_content.side_effect = OSError("disk full")
    crawler.get_note_detail_async_task = AsyncMock(
        return_value={
            "note_id": "n1",
            "xsec_token": "token",
            "user": {"user_id": "owner"},
        }
    )
    crawler.get_notice_media = AsyncMock()
    native_store = AsyncMock()
    monkeypatch.setattr(xhs_store, "update_xhs_note", native_store)

    await crawler.fetch_creator_notes_detail(
        [{"note_id": "n1", "xsec_source": "pc_feed", "xsec_token": "token"}]
    )

    native_store.assert_awaited_once()
    crawler.lead_sink.record_content.assert_called_once_with(
        "n1",
        {
            "note_id": "n1",
            "xsec_token": "token",
            "user": {"user_id": "owner"},
        },
        source_keyword="",
        source_url=f"{crawler.index_url}/explore/n1",
    )


@pytest.mark.asyncio
async def test_lead_batch_uses_native_shared_concurrency(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LEAD_MODE", True)
    crawler = XiaoHongShuCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "xhs", max_comments=1)
    crawler.lead_sink.record_xhs_comments("old", [{"id": "old", "user_info": {}}])
    crawler.get_comments = AsyncMock()

    await crawler.batch_get_note_comments(["n1", "n2", "n3"], ["t1", "t2", "t3"])

    assert [call.kwargs["note_id"] for call in crawler.get_comments.await_args_list] == ["n1", "n2", "n3"]
    assert len({id(call.kwargs["semaphore"]) for call in crawler.get_comments.await_args_list}) == 1


@pytest.mark.asyncio
async def test_lead_batch_skips_checkpointed_notes(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LEAD_MODE", True)
    crawler = XiaoHongShuCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "xhs")
    crawler.lead_sink.checkpoint("n1")
    crawler.get_comments = AsyncMock()

    await crawler.batch_get_note_comments(["n1", "n2"], ["t1", "t2"])

    crawler.get_comments.assert_awaited_once()
    assert crawler.get_comments.await_args.kwargs["note_id"] == "n2"


@pytest.mark.asyncio
async def test_profile_collection_does_not_fetch_posts(tmp_path):
    crawler = XiaoHongShuCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "xhs", deep_profile_limit=1)
    crawler.lead_sink.record_xhs_comments("n1", [{"id": "c1", "content": "采购报价", "user_info": {"user_id": "buyer"}}])
    crawler.xhs_client = AsyncMock()
    crawler._collect_lead_profile_refs = AsyncMock()

    await crawler.collect_lead_profiles()

    crawler._collect_lead_profile_refs.assert_awaited_once_with(crawler.lead_sink.profile_references())
    crawler.xhs_client.get_notes_by_creator.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_collection_marks_recovery_after_network_failure(tmp_path):
    crawler = XiaoHongShuCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "xhs")
    crawler.lead_sink.record_xhs_comments(
        "n1",
        [
            {
                "id": "c1",
                "content": "采购报价",
                "user_info": {"user_id": "u1"},
            },
            {
                "id": "c2",
                "content": "需要定制",
                "user_info": {"user_id": "u2"},
            },
        ],
    )
    crawler.xhs_client = AsyncMock()
    crawler.xhs_client.get_creator_info_by_browser.side_effect = [
        {"basicInfo": {"redId": "public-u1"}},
        RuntimeError("All connection attempts failed"),
    ]

    with pytest.raises(RuntimeError, match="connection attempts failed"):
        await crawler._collect_lead_profile_refs(
            crawler.lead_sink.profile_references()
        )

    assert json.loads(
        (tmp_path / "profile_resume_required.json").read_text(encoding="utf-8")
    ) == {"platform": "xhs", "reason": "network_error"}
    assert crawler.lead_sink.profile_references()[0]["profiled"] is True
