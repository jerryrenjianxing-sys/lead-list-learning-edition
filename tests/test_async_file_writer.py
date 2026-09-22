from pathlib import Path
import json

import pytest

import config
from tools import async_file_writer


@pytest.mark.asyncio
async def test_search_files_use_first_safe_keyword_and_one_run_time(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVE_DATA_PATH", str(tmp_path))
    monkeypatch.setattr(config, "KEYWORDS", "一次性雨衣/采购:厂家,雨衣批发")
    monkeypatch.setattr(config, "ENABLE_GET_WORDCLOUD", False)
    monkeypatch.setattr(async_file_writer.utils, "get_current_date", lambda: "2026-07-23")
    monkeypatch.setattr(
        async_file_writer.utils,
        "get_current_time",
        lambda: "2026-07-23 18:05:30",
    )

    writer = async_file_writer.AsyncFileWriter(platform="douyin", crawler_type="search")
    await writer.write_single_item_to_json({"id": "1"}, "contents")
    await writer.write_single_item_to_json({"id": "2"}, "comments")

    files = sorted(path.name for path in Path(tmp_path, "douyin", "json").iterdir())
    assert files == [
        "search_comments_一次性雨衣_采购_厂家_2026-07-23_18-05-30.json",
        "search_contents_一次性雨衣_采购_厂家_2026-07-23_18-05-30.json",
    ]


@pytest.mark.asyncio
async def test_search_writers_from_same_process_share_one_run_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVE_DATA_PATH", str(tmp_path))
    monkeypatch.setattr(config, "KEYWORDS", "食品包装")
    monkeypatch.setattr(config, "ENABLE_GET_WORDCLOUD", False)

    times = iter([
        "2026-07-24 09:20:02",
        "2026-07-24 09:20:07",
    ])
    monkeypatch.setattr(async_file_writer.utils, "get_current_time", lambda: next(times))

    first_writer = async_file_writer.AsyncFileWriter(
        platform="kuaishou",
        crawler_type="search",
    )
    second_writer = async_file_writer.AsyncFileWriter(
        platform="kuaishou",
        crawler_type="search",
    )
    await first_writer.write_single_item_to_json({"id": "1"}, "comments")
    await second_writer.write_single_item_to_json({"id": "2"}, "comments")

    files = list(Path(tmp_path, "kuaishou", "json").iterdir())
    assert [path.name for path in files] == [
        "search_comments_食品包装_2026-07-24_09-20-02.json",
    ]
    assert json.loads(files[0].read_text(encoding="utf-8")) == [
        {"id": "1"},
        {"id": "2"},
    ]
