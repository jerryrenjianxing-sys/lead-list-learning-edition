import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

import config
from media_platform.kuaishou.client import KuaiShouClient
from media_platform.kuaishou.core import KuaishouCrawler
from store import kuaishou as kuaishou_store
from tools.lead_sink import LeadSink


@pytest.mark.asyncio
async def test_home_navigation_does_not_wait_for_all_assets():
    crawler = KuaishouCrawler()
    crawler.context_page = AsyncMock()

    await crawler._goto_home()

    crawler.context_page.goto.assert_awaited_once_with(
        "https://www.kuaishou.com?isHome=1",
        wait_until="domcontentloaded",
    )


@pytest.mark.asyncio
async def test_lead_mode_keeps_native_comment_and_subcomment_results(monkeypatch):
    async def crawl(lead_mode):
        monkeypatch.setattr(config, "LEAD_MODE", lead_mode)
        monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", True)
        client = KuaiShouClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
        client.get_video_comments = AsyncMock(
            return_value={
                "pcursorV2": "no_more",
                "rootCommentsV2": [{"comment_id": 1, "hasSubComments": True}],
            }
        )
        client.get_video_sub_comments = AsyncMock(
            return_value={
                "pcursorV2": "no_more",
                "subCommentsV2": [{"comment_id": 2}],
            }
        )

        result = await client.get_video_all_comments(
            "v1", crawl_interval=0, max_count=1
        )
        return [item["comment_id"] for item in result], client.get_video_sub_comments.await_count

    assert await crawl(True) == await crawl(False) == ([1, 2], 1)


@pytest.mark.asyncio
async def test_lead_mode_keeps_native_missing_response_semantics(monkeypatch):
    async def crawl(lead_mode):
        monkeypatch.setattr(config, "LEAD_MODE", lead_mode)
        monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", True)
        client = KuaiShouClient(headers={}, playwright_page=AsyncMock(), cookie_dict={})
        client.get_video_comments = AsyncMock(
            return_value={"pcursorV2": "no_more"}
        )
        top_level = await client.get_video_all_comments(
            "v1", crawl_interval=0, max_count=1
        )
        client.get_video_sub_comments = AsyncMock(
            return_value={"pcursorV2": "no_more"}
        )
        sub_level = await client.get_comments_all_sub_comments(
            [{"comment_id": 1, "hasSubComments": True}],
            "v1",
            crawl_interval=0,
        )
        return top_level, sub_level

    assert await crawl(True) == await crawl(False) == ([], [])


@pytest.mark.asyncio
async def test_lead_search_uses_native_result_count_and_records_content(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LEAD_MODE", True)
    monkeypatch.setattr(config, "KEYWORDS", "雨衣")
    monkeypatch.setattr(config, "CRAWLER_MAX_NOTES_COUNT", 20)
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    crawler = KuaishouCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "ks")
    crawler._search_leads = AsyncMock()
    crawler.ks_client = AsyncMock()
    crawler.ks_client.search_info_by_keyword_v2.return_value = {
        "visionSearchPhoto": {
            "result": 1,
            "feeds": [
                {"photo": {"id": "v1", "manifest": "media-secret"}, "author": {}},
                *[{"photo": {"id": item}, "author": {}} for item in ("v2", "v3")],
            ],
            "searchSessionId": "search",
        }
    }
    monkeypatch.setattr(kuaishou_store, "update_kuaishou_video", AsyncMock())
    crawler.batch_get_video_comments = AsyncMock()

    await crawler.search()

    crawler._search_leads.assert_not_awaited()
    assert crawler.batch_get_video_comments.await_args.args[0] == ["v1", "v2", "v3"]
    assert crawler.lead_sink.content_count == 3
    assert "media-secret" not in (tmp_path / "raw.jsonl").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_lead_comments_keep_native_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LEAD_MODE", True)
    monkeypatch.setattr(config, "CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES", 7)
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    crawler = KuaishouCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "ks", max_comments=1)
    crawler.lead_sink.record_ks_comments("old", [{"comment_id": "old", "author_id": "buyer"}])
    crawler.ks_client = AsyncMock()
    monkeypatch.setattr(kuaishou_store, "batch_update_ks_video_comments", AsyncMock())

    await crawler.get_comments("v1", asyncio.Semaphore(1))

    assert crawler.ks_client.get_video_all_comments.await_args.kwargs["max_count"] == 7


@pytest.mark.asyncio
async def test_lead_mode_keeps_native_comment_error_recovery(monkeypatch):
    async def recover(lead_mode):
        monkeypatch.setattr(config, "LEAD_MODE", lead_mode)
        monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
        sleep = Mock()
        monkeypatch.setattr("media_platform.kuaishou.core.time.sleep", sleep)
        crawler = KuaishouCrawler()
        crawler.lead_sink = Mock() if lead_mode else None
        crawler.ks_client = AsyncMock()
        crawler.ks_client.get_video_all_comments.side_effect = RuntimeError("blocked")
        crawler.context_page = AsyncMock()
        crawler.browser_context = AsyncMock()

        await crawler.get_comments("v1", asyncio.Semaphore(1))

        return (
            crawler.context_page.goto.await_count,
            crawler.ks_client.update_cookies.await_count,
            sum(call.args == (20,) for call in sleep.call_args_list),
        )

    assert await recover(True) == await recover(False) == (1, 1, 1)


@pytest.mark.asyncio
async def test_comment_sidecar_failure_keeps_native_store_without_block_recovery(monkeypatch):
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 0)
    sleep = Mock()
    monkeypatch.setattr("media_platform.kuaishou.core.time.sleep", sleep)
    crawler = KuaishouCrawler()
    crawler.lead_sink = Mock()
    crawler.lead_sink.record_ks_comments.side_effect = OSError("disk full")
    crawler.ks_client = AsyncMock()
    crawler.context_page = AsyncMock()
    crawler.browser_context = AsyncMock()
    native_store = AsyncMock()
    monkeypatch.setattr(kuaishou_store, "batch_update_ks_video_comments", native_store)

    async def get_comments(**kwargs):
        await kwargs["callback"]("v1", [{"comment_id": "c1"}])
        return [{"comment_id": "c1"}]

    crawler.ks_client.get_video_all_comments.side_effect = get_comments

    await crawler.get_comments("v1", asyncio.Semaphore(1))

    native_store.assert_awaited_once_with("v1", [{"comment_id": "c1"}])
    sleep.assert_not_called()
    crawler.context_page.goto.assert_not_awaited()


@pytest.mark.asyncio
async def test_creator_content_is_recorded_without_breaking_native_store(monkeypatch):
    crawler = KuaishouCrawler()
    crawler.lead_sink = Mock()
    crawler.lead_sink.record_content.side_effect = OSError("disk full")
    crawler.get_video_info_task = AsyncMock(
        return_value={
            "photo": {"id": "v1"},
            "author": {"id": "owner"},
        }
    )
    native_store = AsyncMock()
    monkeypatch.setattr(kuaishou_store, "update_kuaishou_video", native_store)

    await crawler.fetch_creator_video_detail([{"photo": {"id": "v1"}}])

    native_store.assert_awaited_once()
    crawler.lead_sink.record_content.assert_called_once_with(
        "v1",
        {
            "photo": {"id": "v1"},
            "author": {"id": "owner"},
        },
        source_keyword="",
        source_url="https://www.kuaishou.com/short-video/v1",
    )


@pytest.mark.asyncio
async def test_lead_batch_uses_native_shared_concurrency(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LEAD_MODE", True)
    crawler = KuaishouCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "ks", max_comments=1)
    crawler.lead_sink.record_ks_comments("old", [{"comment_id": "old", "author_id": "buyer"}])
    crawler.get_comments = AsyncMock()

    await crawler.batch_get_video_comments(["v1", "v2", "v3"])

    assert [call.args[0] for call in crawler.get_comments.await_args_list] == ["v1", "v2", "v3"]
    assert len({id(call.args[1]) for call in crawler.get_comments.await_args_list}) == 1


@pytest.mark.asyncio
async def test_profile_collection_does_not_fetch_posts(tmp_path):
    crawler = KuaishouCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "ks", deep_profile_limit=1)
    crawler.lead_sink.record_ks_comments("v1", [{"comment_id": "c1", "content": "采购报价", "author_id": "buyer"}])
    crawler.ks_client = AsyncMock()
    crawler._collect_lead_profile_refs = AsyncMock()

    await crawler.collect_lead_profiles()

    crawler._collect_lead_profile_refs.assert_awaited_once_with(crawler.lead_sink.profile_references())
    crawler.ks_client.get_video_by_creater.assert_not_awaited()


@pytest.mark.asyncio
async def test_browser_profile_returns_full_profile_with_public_id():
    page = AsyncMock()
    profile = {
        "profile": {"user_id": "internal", "user_name": "buyer"},
        "ownerCount": {"fan": 10},
        "userDefineId": "public-id",
    }
    page.wait_for_function.return_value.json_value.return_value = profile
    client = KuaiShouClient(headers={}, playwright_page=page, cookie_dict={})

    result = await client.get_creator_info_by_browser("internal")

    assert result["userDefineId"] == "public-id"
    assert result["profile"]["user_id"] == "internal"
    page.goto.assert_awaited_once_with(
        "https://www.kuaishou.com/profile/internal",
        wait_until="domcontentloaded",
    )


@pytest.mark.asyncio
async def test_browser_profile_does_not_promote_internal_id():
    page = AsyncMock()
    profile = {
        "profile": {"user_id": "internal", "user_name": "buyer"},
    }
    page.wait_for_function.return_value.json_value.return_value = profile
    client = KuaiShouClient(headers={}, playwright_page=page, cookie_dict={})

    result = await client.get_creator_info_by_browser("internal")

    assert "userDefineId" not in result


@pytest.mark.asyncio
async def test_profile_collection_uses_browser_profile(tmp_path):
    crawler = KuaishouCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "ks")
    crawler.lead_sink.record_ks_comments(
        "v1",
        [{"comment_id": "c1", "content": "采购报价", "author_id": "buyer"}],
    )
    crawler.ks_client = AsyncMock()
    crawler.ks_client.get_creator_info_by_browser.return_value = {
        "profile": {"user_id": "buyer"},
        "userDefineId": "public-id",
    }

    await crawler.collect_lead_profiles()

    crawler.ks_client.get_creator_info_by_browser.assert_awaited_once_with("buyer")
    crawler.ks_client.get_creator_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_collection_marks_recovery_when_browser_closes(tmp_path):
    crawler = KuaishouCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "ks")
    crawler.lead_sink.record_ks_comments(
        "v1",
        [
            {"comment_id": "c1", "content": "采购报价", "author_id": "u1"},
            {"comment_id": "c2", "content": "需要定制", "author_id": "u2"},
        ],
    )
    crawler.ks_client = AsyncMock()
    crawler.ks_client.get_creator_info_by_browser.side_effect = [
        {"profile": {"user_id": "u1"}, "userDefineId": "public-u1"},
        RuntimeError("Target page, context or browser has been closed"),
    ]

    with pytest.raises(RuntimeError, match="browser has been closed"):
        await crawler._collect_lead_profile_refs(
            crawler.lead_sink.profile_references()
        )

    assert json.loads(
        (tmp_path / "profile_resume_required.json").read_text(encoding="utf-8")
    ) == {"platform": "ks", "reason": "browser_closed"}
    assert crawler.lead_sink.profile_references()[0]["profiled"] is True


@pytest.mark.asyncio
async def test_profile_resume_skips_already_completed_accounts(tmp_path):
    crawler = KuaishouCrawler()
    crawler.lead_sink = LeadSink(tmp_path, "ks")
    crawler.ks_client = AsyncMock()
    crawler.ks_client.get_creator_info_by_browser.return_value = {
        "profile": {"user_id": "remaining"},
        "userDefineId": "public-remaining",
    }

    await crawler._collect_lead_profile_refs(
        [
            {"user_id": "done", "profiled": True},
            {"user_id": "remaining", "profiled": False},
        ]
    )

    crawler.ks_client.get_creator_info_by_browser.assert_awaited_once_with(
        "remaining"
    )
