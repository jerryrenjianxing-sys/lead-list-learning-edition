"""Collection outcomes observed through the authenticated job interface."""

import sys
import time
import json
from pathlib import Path
import pytest

from fastapi.testclient import TestClient

from workbench.app import make_app
from workbench.queue import Queue


def crawl_search(tmp_path, scenario, resume_job_id=None):
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
    app = make_app(tmp_path, run_queue=False)
    driver = Path(__file__).parent / "fixtures" / "douyin_search_worker.py"
    queue = Queue(
        app.state.db,
        lambda job_id: [sys.executable, str(driver), str(tmp_path), job_id, str(scenario_path)],
    )
    with TestClient(app) as client:
        client.get("/api/v1/session")
        response = client.post(
            "/api/v1/jobs/" + resume_job_id + "/resume" if resume_job_id else "/api/v1/jobs",
            json={} if resume_job_id else {
                "platform": "dy", "keywords": "AI Agent", "max_notes_count": scenario.get("limit", 0)
            },
        )
        assert response.status_code == 200, response.text
        job_id = response.json()["id"]
        queue.start()
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                job = client.get("/api/v1/jobs/" + job_id).json()
                if job["state"] not in {"queued", "running", "stopping"}:
                    return job
                time.sleep(0.02)
            raise AssertionError(job)
        finally:
            queue.close()


def test_platform_error_is_not_hidden_by_an_empty_search_page(tmp_path):
    job = crawl_search(tmp_path, {"responses": [{
        "status_code": 400, "status_msg": "fixture-platform-error",
        "data": [], "has_more": 0,
    }]})
    assert job["state"] == "interrupted", job
    assert job["checkpoint"]["collection"]["issues"][0]["reason"] == "platform_error"


@pytest.mark.parametrize("response,reason", [
    ({}, "response_unconfirmed"),
    ({"data": [], "has_more": 1}, "empty_unconfirmed"),
])
def test_ambiguous_search_preserves_a_structured_stop_reason(tmp_path, response, reason):
    job = crawl_search(tmp_path, {"responses": [response]})
    assert job["state"] == "interrupted", job
    outcome = job["checkpoint"]["collection"]
    assert outcome["issues"][0]["reason"] == reason
    assert outcome["issues"][0]["page"] == 1
    assert outcome["issues"][0]["response"]["data_present"] == ("data" in response)


def test_explicit_search_end_completes_with_saved_records(tmp_path):
    job = crawl_search(tmp_path, {"responses": [{
        "status_code": 0, "has_more": 0,
        "data": [{"aweme_info": {"aweme_id": "v1", "desc": "fixture", "author": {}}}],
    }]})
    assert job["state"] == "completed", job
    assert job["checkpoint"]["saved_records"] == 1
    assert job["checkpoint"]["collection"]["stops"][0]["reason"] == "source_exhausted"


def test_successful_platform_response_with_no_next_page_reports_no_results(tmp_path):
    job = crawl_search(tmp_path, {"responses": [{"status_code": 0, "data": [], "has_more": 0}]})
    assert job["state"] == "completed", job
    assert job["checkpoint"]["saved_records"] == 0
    assert job["checkpoint"]["collection"]["empty"] is True
    assert job["checkpoint"]["collection"]["stops"][0]["reason"] == "no_results"


def test_short_search_page_does_not_count_as_the_requested_record_limit(tmp_path):
    job = crawl_search(tmp_path, {"limit": 10, "responses": [
        {"data": [{"aweme_info": {"aweme_id": "v1", "author": {}}}], "has_more": 1},
        {"data": [{"aweme_info": {"aweme_id": "v2", "author": {}}}], "has_more": 0},
    ]})
    assert job["state"] == "completed", job
    assert job["checkpoint"]["saved_records"] == 2
    assert job["checkpoint"]["collection"]["stops"][0]["reason"] == "source_exhausted"


def test_requested_search_count_caps_saved_contents_without_counting_duplicates(tmp_path):
    video = lambda video_id: {"aweme_info": {"aweme_id": video_id, "author": {}}}
    job = crawl_search(tmp_path, {"limit": 2, "responses": [
        {"data": [video("v1"), video("v1")], "has_more": 1},
        {"data": [video("v1"), video("v2"), video("v3")], "has_more": 1},
    ]})
    assert job["state"] == "completed", job
    assert job["checkpoint"]["saved_records"] == 2
    assert job["checkpoint"]["collection"]["stops"][0]["reason"] == "limit_reached"


@pytest.mark.parametrize("repeat", [False, True])
def test_saved_search_data_is_partial_when_pagination_is_unconfirmed(tmp_path, repeat):
    first = {"data": [{"aweme_info": {"aweme_id": "v1", "author": {}}}]}
    responses = [first]
    if repeat:
        first["has_more"] = 1
        responses.append(first)
    job = crawl_search(tmp_path, {"responses": responses})
    assert job["state"] == "partial", job
    assert job["checkpoint"]["saved_records"] == 1
    assert job["checkpoint"]["collection"]["issues"][0]["reason"] == (
        "pagination_stalled" if repeat else "response_unconfirmed"
    )


@pytest.mark.parametrize("reply,reason", [
    ({"body": ""}, "empty_response"),
    ({"http_status": 403, "body": "<html>fixture-secret-cookie</html>"}, "http_error"),
    ({"body": "<html>fixture-secret-cookie</html>"}, "non_json_response"),
])
def test_comment_request_failure_does_not_claim_account_block_or_expose_body(tmp_path, reply, reason):
    job = crawl_search(tmp_path, {
        "responses": [{"data": [{"aweme_info": {"aweme_id": "v1", "author": {}}}], "has_more": 0}],
        "comments_http": [reply],
    })
    assert job["state"] == "partial", job
    assert "account blocked" not in job["error"]
    assert "fixture-secret-cookie" not in job["error"]
    assert job["checkpoint"]["collection"]["issues"][0]["reason"] == reason
    with TestClient(make_app(tmp_path, run_queue=False)) as client:
        client.get("/api/v1/session")
        events = client.get("/api/v1/jobs/" + job["id"] + "/events").text
        assert "fixture-secret-cookie" not in events


@pytest.mark.parametrize("page,reason", [
    ({"has_more": 1, "cursor": 0, "comments": []}, "empty_unconfirmed"),
    ({"has_more": 0}, "response_unconfirmed"),
    ({"status_code": 400, "has_more": 0, "comments": []}, "platform_error"),
])
def test_silent_comment_stop_marks_saved_video_partial(tmp_path, page, reason):
    job = crawl_search(tmp_path, {
        "responses": [{"data": [{"aweme_info": {"aweme_id": "v1", "author": {}}}], "has_more": 0}],
        "comments_http": [{"body": json.dumps(page)}],
    })
    assert job["state"] == "partial", job
    assert job["checkpoint"]["collection"]["issues"][0]["reason"] == reason


def test_retry_uses_new_attempt_outcomes_and_preserves_the_original_dataset(tmp_path):
    video = {"aweme_info": {"aweme_id": "v1", "author": {}}}
    first = crawl_search(tmp_path, {"responses": [
        {"data": [video], "has_more": 1}, {"data": [], "has_more": 1},
    ]})
    assert first["state"] == "partial"
    second = crawl_search(tmp_path, {"responses": [{"data": [video], "has_more": 0}]}, first["id"])
    assert second["state"] == "completed", second
    assert second["attempt"] == 2
    assert second["dataset_id"] == first["dataset_id"]
    assert second["checkpoint"]["saved_records"] == 1
    assert second["checkpoint"]["collection"]["issue_count"] == 0


def test_new_reply_failure_keeps_saved_comments_and_reports_partial(tmp_path):
    job = crawl_search(tmp_path, {
        "responses": [{"data": [{"aweme_info": {"aweme_id": "v1", "author": {}}}], "has_more": 0}],
        "replies": True,
        "comments_http": [
            {"body": json.dumps({"comments": [{"cid": "c1", "aweme_id": "v1", "text": "fixture", "reply_comment_total": 1, "user": {}}], "has_more": 0})},
            {"body": json.dumps({"comments": [], "has_more": 1})},
        ],
    })
    assert job["state"] == "partial", job
    assert job["checkpoint"]["saved_records"] == 2
    assert job["checkpoint"]["collection"]["issues"][0]["stage"] == "replies"


def test_explicitly_empty_comment_page_is_a_normal_end(tmp_path):
    job = crawl_search(tmp_path, {
        "responses": [{"data": [{"aweme_info": {"aweme_id": "v1", "author": {}}}], "has_more": 0}],
        "comments_http": [{"body": json.dumps({"comments": None, "has_more": 0})}],
    })
    assert job["state"] == "completed", job
    assert job["checkpoint"]["collection"]["complete"] is True


def test_received_comments_are_saved_even_when_paging_metadata_is_missing(tmp_path):
    job = crawl_search(tmp_path, {
        "responses": [{"data": [{"aweme_info": {"aweme_id": "v1", "author": {}}}], "has_more": 0}],
        "comments_http": [{"body": json.dumps({"comments": [{"cid": "c1", "aweme_id": "v1", "text": "fixture", "user": {}}]})}],
    })
    assert job["state"] == "partial", job
    assert job["checkpoint"]["saved_records"] == 2
    assert job["checkpoint"]["collection"]["issues"][0]["reason"] == "response_unconfirmed"


def test_broken_worker_outcome_keeps_data_and_does_not_claim_success(tmp_path):
    job = crawl_search(tmp_path, {
        "responses": [{"data": [{"aweme_info": {"aweme_id": "v1", "author": {}}}], "has_more": 0}],
        "corrupt_outcome": True,
    })
    assert job["state"] == "partial", job
    assert job["checkpoint"]["saved_records"] == 1
    assert job["checkpoint"]["collection"]["issues"][0]["reason"] == "outcome_unconfirmed"


def test_captured_empty_search_is_not_reported_as_success(tmp_path):
    # Minimal replay of the real 2026-10-21 AI Agent job (zero exit).
    lines = [
        "INFO [DouYinCrawler.search] search douyin keyword: AI Agent, page: 1 is empty,[]`",
    ]
    app = make_app(tmp_path, run_queue=False)
    queue = Queue(
        app.state.db,
        lambda _: [sys.executable, "-c", "print(" + repr("\n".join(lines)) + ")"],
    )
    with TestClient(app) as client:
        client.get("/api/v1/session")
        created = client.post(
            "/api/v1/jobs",
            json={"platform": "dy", "keywords": "AI Agent", "max_notes_count": 0},
        )
        assert created.status_code == 200, created.text
        job_id = created.json()["id"]
        queue.start()
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = client.get("/api/v1/jobs/" + job_id).json()
                if job["state"] not in {"queued", "running", "stopping"}:
                    break
                time.sleep(0.02)
            assert job["state"] in {"interrupted", "failed"}, job
            assert job["error"], "An unconfirmed empty response needs an actionable reason"
        finally:
            queue.close()
