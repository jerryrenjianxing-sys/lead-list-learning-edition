"""Observe inherited API calls at their boundary without changing platform results.

Explicit per-platform shapes provide evidence. A list alone is never a terminal
page. Standalone MediaCrawler has no workbench environment, so this is a no-op.
"""

from __future__ import annotations
from contextvars import ContextVar
from functools import wraps
import inspect
import os

current_scope = ContextVar("collection_scope", default=None)


def outcome(platform, capability, response):
    if capability in ("detail", "profile"):
        entity = (
            response
            if isinstance(response, dict)
            else response.model_dump()
            if hasattr(response, "model_dump")
            else {}
        )
        if platform == "bili" and capability == "detail":
            entity = entity.get("View", entity)
        if platform == "ks":
            entity = (
                entity.get("visionVideoDetail", {}).get("photo", {})
                if capability == "detail"
                else entity
            )
        return (
            "source_exhausted"
            if any(
                entity.get(k)
                for k in (
                    "aweme_id",
                    "note_id",
                    "bvid",
                    "aid",
                    "id",
                    "photoId",
                    "content_id",
                    "user_id",
                    "sec_uid",
                    "nickname",
                    "name",
                )
            )
            else "response_unconfirmed"
        )
    if not isinstance(response, dict):
        return "response_unconfirmed"
    if response.get("status_code") not in (None, 0, "0") or response.get(
        "code"
    ) not in (None, 0, "0"):
        return "platform_error"
    if platform in ("dy", "xhs"):
        key = (
            "data"
            if platform == "dy" and capability == "search"
            else "items"
            if capability == "search"
            else "aweme_list"
            if platform == "dy" and capability == "creator"
            else "notes"
            if capability == "creator"
            else "comments"
        )
        items = response.get(key)
        more = response.get("has_more")
        if (
            platform == "dy"
            and capability in ("comments", "replies")
            and items is None
            and key in response
        ):
            items = []
        if not isinstance(items, list) or more not in (0, 1, False, True, "0", "1"):
            return "response_unconfirmed"
        if more in (0, False, "0"):
            return "source_exhausted" if items else "no_results"
        return "request_succeeded" if items else "empty_unconfirmed"
    if platform == "ks":
        key = (
            "feeds"
            if capability in ("search", "creator")
            else "rootCommentsV2"
            if capability == "comments"
            else "subCommentsV2"
        )
        cursor = "pcursor" if capability in ("search", "creator") else "pcursorV2"
        if not isinstance(response.get(key), list) or cursor not in response:
            return "response_unconfirmed"
        if response[cursor] == "no_more":
            return "source_exhausted" if response[key] else "no_results"
        return "request_succeeded" if response[key] else "empty_unconfirmed"
    if platform == "bili":
        if capability == "search":
            if (
                not isinstance(response.get("result"), list)
                or not isinstance(response.get("numPages"), int)
                or not isinstance(response.get("page"), int)
            ):
                return "response_unconfirmed"
            end = response["page"] >= response["numPages"]
            items = response["result"]
        else:
            cursor = response.get("cursor", {})
            if type(cursor.get("is_end")) is not bool:
                return "response_unconfirmed"
            end = cursor["is_end"]
            items = response.get("replies")
            if items is None and "replies" in response:
                items = []
        if not isinstance(items, list):
            return "response_unconfirmed"
        return (
            ("source_exhausted" if items else "no_results")
            if end
            else "request_succeeded"
            if items
            else "empty_unconfirmed"
        )
    if platform == "wb" and capability == "comments":
        if not isinstance(response.get("data"), list) or "max_id" not in response:
            return "response_unconfirmed"
        return (
            ("source_exhausted" if response["data"] else "no_results")
            if response["max_id"] == 0
            else "request_succeeded"
            if response["data"]
            else "empty_unconfirmed"
        )
    if platform == "zhihu" and capability in ("comments", "replies", "creator"):
        if (
            not isinstance(response.get("data"), list)
            or type(response.get("paging", {}).get("is_end")) is not bool
        ):
            return "response_unconfirmed"
        return (
            ("source_exhausted" if response["data"] else "no_results")
            if response["paging"]["is_end"]
            else "request_succeeded"
            if response["data"]
            else "empty_unconfirmed"
        )
    return "response_unconfirmed"


def observed(platform, capability):
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        async def call(*args, **kwargs):
            if not os.environ.get("MEDIAWORKBENCH_JOB_ID"):
                return await function(*args, **kwargs)
            from tools.crawl_status import record

            values = signature.bind(*args, **kwargs).arguments
            fields = (
                ("keyword",)
                if capability == "search"
                else (
                    "aweme_id",
                    "note_id",
                    "photo_id",
                    "bvid",
                    "aid",
                    "content_id",
                    "video_id",
                    "sec_user_id",
                    "creator_id",
                    "userId",
                    "user_id",
                    "url_token",
                    "creator_url",
                    "user_name",
                    "root_comment_id",
                )
            )
            parts = [str(values[k]) for k in fields if values.get(k) is not None]
            if capability == "replies":
                parts += [
                    str(values[k])
                    for k in ("comment_id", "root_comment_id")
                    if values.get(k) is not None and str(values[k]) not in parts
                ]
            scope = ":".join(parts) or function.__name__
            current_scope.set((capability, scope))
            record(capability, "started", scope=scope)
            try:
                response = await function(*args, **kwargs)
            except Exception as exc:
                reason = getattr(exc, "reason", "request_failed")
                from tools.crawl_status import MESSAGES

                if reason not in MESSAGES:
                    reason = "request_failed"
                record(
                    capability,
                    reason,
                    scope=scope,
                    response={
                        "http_status": getattr(exc, "http_status", None),
                        "retry_after": getattr(exc, "retry_after", 0),
                    },
                )
                raise
            reason = outcome(platform, capability, response)
            record(capability, reason, scope=scope, response=response)
            if (
                reason in ("source_exhausted", "no_results", "request_succeeded")
                and platform == "dy"
            ):
                safe = {
                    k: str(values[k])
                    for k in ("aweme_id", "comment_id", "sec_user_id")
                    if values.get(k)
                }
                if safe:
                    from .scope_sink import save_sample

                    save_sample(platform, capability, safe)
            return response

        return call

    return decorate
