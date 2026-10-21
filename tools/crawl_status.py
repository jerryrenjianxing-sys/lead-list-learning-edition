"""Attempt-scoped collection outcomes shared by crawlers and the local queue.

Only structural response metadata is retained, never response bodies or headers.
Standalone MediaCrawler runs do not write workbench files.
"""

import json
import os
from pathlib import Path
from typing import NotRequired, TypedDict


class Outcome(TypedDict):
    stage: str
    reason: str
    scope: NotRequired[str]
    page: NotRequired[int]
    response: NotRequired[dict[str, int | bool | str]]


class CollectionStatus(TypedDict):
    complete: bool
    empty: bool
    issue_count: int
    issues: list[Outcome]
    stops: list[Outcome]
    message: str


MESSAGES = {
    "platform_error": "平台返回业务错误，当前采集未完成；可查看详情后重试。",
    "empty_unconfirmed": "平台返回空列表，尚未确认无结果或访问受限；可查看详情后重试。",
    "response_unconfirmed": "平台响应结构不完整，尚未确认采集结果；可查看详情后重试。",
    "pagination_stalled": "分页重复或游标未前进，已停止翻页以避免循环；采集尚未确认完整。",
    "request_failed": "平台请求未成功，当前范围尚未采集完整；可查看详情后重试。",
    "empty_response": "平台返回空响应，尚未确认账号状态；可稍后重试。",
    "http_error": "平台请求返回 HTTP 错误，尚未确认账号状态；可查看详情后重试。",
    "non_json_response": "平台返回非 JSON 响应，尚未确认账号状态；可查看详情后重试。",
    "request_rejected": "平台拒绝了本次请求，尚未确认账号状态；可查看详情后重试。",
    "outcome_unconfirmed": "采集结束记录缺失或损坏，尚未确认结果。",
    "started": "采集未正常记录结束原因，尚未确认结果。",
}
COMPLETE = {"source_exhausted", "limit_reached", "no_results"}


def response_summary(response: object) -> dict[str, int | bool | str]:
    if not isinstance(response, dict):
        return {"response_type": type(response).__name__}
    summary = {}
    for key in ("http_status", "status_code", "has_more", "cursor"):
        value = response.get(key)
        if isinstance(value, (int, bool)) or (
            isinstance(value, str) and value.isdigit() and len(value) <= 20
        ):
            summary[key] = value
    for key in ("data", "comments"):
        summary[key + "_present"] = key in response
        if isinstance(response.get(key), list):
            summary[key + "_count"] = len(response[key])
    return summary


def record(stage: str, reason: str, *, scope: str = "", page: int | None = None, response: object = None) -> None:
    directory = os.environ.get("MEDIAWORKBENCH_LOGIN_DIR")
    job_id = os.environ.get("MEDIAWORKBENCH_JOB_ID")
    attempt = os.environ.get("MEDIAWORKBENCH_ATTEMPT", "")
    if not directory or not job_id or not attempt.isdigit():
        return
    item = {
        "job_id": job_id, "attempt": int(attempt),
        "stage": stage, "scope": str(scope), "reason": reason,
    }
    if page is not None:
        item["page"] = page
    if response is not None:
        item["response"] = response_summary(response)
    with (Path(directory) / f"collection-{attempt}.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(item, ensure_ascii=False) + "\n")


def read(directory: Path | str, job_id: str, attempt: int) -> CollectionStatus | None:
    path = Path(directory) / f"collection-{attempt}.jsonl"
    if not path.exists():
        return None
    latest: dict[tuple[str, str], Outcome] = {}
    invalid = False
    try:
        with path.open("rb") as stream:
            for line in stream:
                try:
                    item = json.loads(line.decode("utf-8"))
                    if (
                        not line.endswith(b"\n")
                        or item["job_id"] != job_id or item["attempt"] != attempt
                        or item["reason"] not in COMPLETE | MESSAGES.keys()
                        or not isinstance(item["stage"], str)
                        or not isinstance(item["scope"], str)
                    ):
                        raise ValueError("Invalid collection outcome")
                    outcome: Outcome = {"stage": item["stage"], "scope": item["scope"], "reason": item["reason"]}
                    if isinstance(item.get("page"), int):
                        outcome["page"] = item["page"]
                    if isinstance(item.get("response"), dict):
                        allowed = {"http_status", "status_code", "has_more", "cursor", "data_present", "comments_present", "data_count", "comments_count", "response_type"}
                        outcome["response"] = {
                            key: value for key, value in item["response"].items()
                            if key in allowed and (
                                isinstance(value, (int, bool)) or
                                isinstance(value, str) and value.isdigit() and len(value) <= 20 or
                                key == "response_type" and value in ("list", "NoneType", "str", "int", "bool", "float")
                            )
                        }
                    latest[(item["stage"], item["scope"])] = outcome
                except (ValueError, KeyError, TypeError, UnicodeError):
                    invalid = True
    except OSError:
        invalid = True
    issues = [item for item in latest.values() if item["reason"] not in COMPLETE]
    if invalid or not latest:
        issues.append({"stage": "worker", "reason": "outcome_unconfirmed"})
    searches = [item for item in latest.values() if item["stage"] == "search"]
    empty = not issues and bool(searches) and all(item["reason"] == "no_results" for item in searches)
    return {
        "complete": not issues, "issue_count": len(issues), "issues": issues[:20],
        "stops": [item for item in latest.values() if item["reason"] in COMPLETE][:20],
        "empty": empty,
        "message": "平台本轮返回无搜索结果。" if empty else (
            MESSAGES[issues[0]["reason"]] if issues else "已按平台结束标志或设置数量结束本轮采集。"
        ),
    }
