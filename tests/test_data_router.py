import asyncio
import json
from pathlib import Path
import zipfile

import pytest
from fastapi import HTTPException

from api.routers import data as data_router


def test_jsonl_files_are_listed_counted_and_previewed(tmp_path, monkeypatch):
    monkeypatch.setattr(data_router, "DATA_DIR", tmp_path)
    lead_dir = tmp_path / "lead"
    lead_dir.mkdir()
    rows = [
        {"type": "comment", "accountId": "buyer-1"},
        {"type": "profile", "accountId": "buyer-1"},
    ]
    (lead_dir / "raw.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    files = asyncio.run(data_router.list_data_files())
    assert Path(files["files"][0]["path"]) == Path("lead/raw.jsonl")
    assert files["files"][0]["record_count"] == 2

    preview = asyncio.run(data_router.get_file_content("lead/raw.jsonl", limit=1))
    assert preview == {"data": rows[:1], "total": 2}


def test_archive_download_contains_only_requested_category(tmp_path, monkeypatch):
    monkeypatch.setattr(data_router, "DATA_DIR", tmp_path)
    xhs_dir = tmp_path / "xhs" / "json"
    ks_dir = tmp_path / "kuaishou" / "json"
    xhs_dir.mkdir(parents=True)
    ks_dir.mkdir(parents=True)

    xhs_comment = xhs_dir / "search_comments_食品包装_2026-07-24_09-20-02.json"
    ks_comment = ks_dir / "search_comments_食品包装_2026-07-24_09-20-03.json"
    content = ks_dir / "search_contents_食品包装_2026-07-24_09-20-03.json"
    xhs_comment.write_text("[]", encoding="utf-8")
    ks_comment.write_text("[]", encoding="utf-8")
    content.write_text("[]", encoding="utf-8")

    response = asyncio.run(data_router.download_archive(category="search_comments"))
    archive_path = Path(response.path)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            assert set(archive.namelist()) == {
                "xhs/json/search_comments_食品包装_2026-07-24_09-20-02.json",
                "kuaishou/json/search_comments_食品包装_2026-07-24_09-20-03.json",
            }
        assert response.filename.startswith("MediaCrawler_search_comments_")
    finally:
        archive_path.unlink(missing_ok=True)


def test_prompt_download_returns_the_real_text_file(tmp_path, monkeypatch):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("你是一名社交媒体数据分析顾问。", encoding="utf-8")
    monkeypatch.setattr(data_router, "PROMPT_FILE", prompt)

    response = asyncio.run(data_router.download_prompt())

    assert Path(response.path) == prompt
    assert response.filename == "社交媒体B端采购潜客报告_轻量最终版.txt"


def test_batch_delete_removes_only_valid_data_files(tmp_path, monkeypatch):
    monkeypatch.setattr(data_router, "DATA_DIR", tmp_path)
    monkeypatch.setattr(data_router.crawler_manager, "status", "idle")
    first = tmp_path / "xhs" / "search_comments_one.json"
    second = tmp_path / "dy" / "search_comments_two.jsonl"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("[]", encoding="utf-8")
    second.write_text("{}\n", encoding="utf-8")

    result = asyncio.run(
        data_router.delete_data_files(
            data_router.DeleteFilesRequest(
                paths=[
                    "xhs/search_comments_one.json",
                    "dy/search_comments_two.jsonl",
                ]
            )
        )
    )

    assert result == {"deleted": 2}
    assert not first.exists()
    assert not second.exists()


def test_batch_delete_rejects_paths_outside_data_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(data_router, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(data_router.crawler_manager, "status", "idle")
    outside = tmp_path / "outside.json"
    outside.write_text("[]", encoding="utf-8")

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            data_router.delete_data_files(
                data_router.DeleteFilesRequest(paths=["../outside.json"])
            )
        )

    assert error.value.status_code == 403
    assert outside.exists()


def test_batch_delete_is_blocked_while_crawler_is_running(tmp_path, monkeypatch):
    monkeypatch.setattr(data_router, "DATA_DIR", tmp_path)
    monkeypatch.setattr(data_router.crawler_manager, "status", "running")
    target = tmp_path / "active.json"
    target.write_text("[]", encoding="utf-8")

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            data_router.delete_data_files(
                data_router.DeleteFilesRequest(paths=["active.json"])
            )
        )

    assert error.value.status_code == 409
    assert target.exists()
