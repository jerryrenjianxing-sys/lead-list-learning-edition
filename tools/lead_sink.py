"""Small append-only lead output used by platform crawlers."""

from __future__ import annotations

import json
import os
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_PRIVATE_KEY_PARTS = (
    "cookie",
    "session",
    "token",
    "xsec",
    "captcha",
    "verify",
    "verification",
    "raw_response",
    "response",
    "body",
    "response_body",
)
_EMOJI_MARKER_RE = re.compile(r"\[[^\]]*\]")
_KEYCAP_EMOJI_RE = re.compile(r"[0-9#*]\ufe0f?\u20e3")
_MEANINGFUL_TEXT_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")
_LOGGER = logging.getLogger(__name__)


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _clean(item)
            for key, item in value.items()
            if not any(part in str(key).lower() for part in _PRIVATE_KEY_PARTS)
        }
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, str):
        parsed = urlsplit(value)
        if parsed.scheme and parsed.netloc and parsed.query:
            query = [
                (key, item)
                for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                if not any(part in key.lower() for part in _PRIVATE_KEY_PARTS)
            ]
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    return value


def _public_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _has_meaningful_text(value: Any) -> bool:
    text = _EMOJI_MARKER_RE.sub("", str(value or ""))
    return bool(_MEANINGFUL_TEXT_RE.search(_KEYCAP_EMOJI_RE.sub("", text)))


def _profile_resume_reason(error: BaseException) -> str:
    text = f"{type(error).__name__}: {error}".lower()
    if "account blocked" in text or "captcha" in text or "请通过验证" in text:
        return "risk_control"
    if "login" in text or "session" in text or "cookie" in text:
        return "login_expired"
    if ("browser" in text and "closed" in text) or "target page" in text:
        return "browser_closed"
    return "network_error"


def _public_id(*values: Any) -> str:
    return next(
        (text for value in values if (text := str(value or "").strip()) and text != "0"),
        "",
    )


def _first(*values: Any) -> Any:
    return next((value for value in values if value is not None and value != ""), "")


def _tag_names(values: Any) -> list[str]:
    if not isinstance(values, list):
        values = [values] if values else []
    result = []
    for value in values:
        name = (
            _first(value.get("name"), value.get("hashtag_name"), value.get("tag_name"))
            if isinstance(value, dict)
            else value
        )
        if name:
            result.append(str(name))
    return result


class LeadSink:
    def __init__(
        self,
        save_data_path: str | Path,
        platform: str,
        *,
        max_accounts: int = 0,
        deep_profile_limit: int = 30,
        max_comments: int = 300,
    ) -> None:
        self.path = Path(save_data_path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.raw_path = self.path / "raw.jsonl"
        self.events_path = self.path / "events.jsonl"
        self.platform = platform
        self.max_accounts = max_accounts
        self.deep_profile_limit = min(deep_profile_limit, 30)
        self.max_comments = min(max_comments, 300)
        self._seen: set[tuple[str, str, str]] = set()
        self._completed: set[str] = set()
        self._comment_count = 0
        self._content_comment_counts: dict[str, int] = {}
        self._content_accounts: dict[str, set[str]] = {}
        self._contents: set[str] = set()
        self._content_authors: dict[str, str] = {}
        self._content_keywords: dict[str, str] = {}
        self._profiles: dict[str, dict[str, Any]] = {}
        self._repair_tail(self.raw_path)
        self._repair_tail(self.events_path)
        self._load(self.raw_path)
        self._load(self.events_path)

    @staticmethod
    def _repair_tail(path: Path) -> None:
        if not path.exists():
            return
        with path.open("r+b") as stream:
            data = stream.read()
            if not data or data.endswith(b"\n"):
                return
            tail_start = data.rfind(b"\n") + 1
            try:
                json.loads(data[tail_start:].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                stream.truncate(tail_start)
                return
            stream.seek(0, 2)
            stream.write(b"\n")

    def _load(self, path: Path) -> None:
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            platform = str(record.get("platform", ""))
            record_type = str(record.get("type", ""))
            record_id = str(record.get("id", ""))
            self._seen.add((platform, record_type, record_id))
            if platform == self.platform and record_type == "content":
                content_id = str(record.get("contentId") or record_id)
                self._contents.add(content_id)
                account_id = str(record.get("accountId", ""))
                if account_id:
                    self._content_authors[content_id] = account_id
                    self._register_profile(content_id, account_id)
                keyword = str((record.get("payload") or {}).get("sourceKeyword", ""))
                if keyword:
                    self._content_keywords[content_id] = keyword
            if platform == self.platform and record_type in ("comment", "reply"):
                self._comment_count += 1
                content_id = str(record.get("contentId", ""))
                self._content_comment_counts[content_id] = self._content_comment_counts.get(content_id, 0) + 1
                account_id = str(record.get("accountId", ""))
                self._consider_comment(content_id, account_id, record.get("payload", {}))
            if platform == self.platform and record_type == "profile":
                account_id = str(record.get("accountId") or record_id)
                if account_id in self._profiles:
                    self._profiles[account_id].update(profile=record.get("payload", {}), profiled=True)
            if platform == self.platform and record_type == "checkpoint":
                self._completed.add(str(record.get("contentId") or record_id))

    @property
    def remaining_comments(self) -> int:
        return max(0, self.max_comments - self._comment_count)

    def is_completed(self, content_id: str) -> bool:
        return content_id in self._completed

    def content_comment_count(self, content_id: str) -> int:
        return self._content_comment_counts.get(content_id, 0)

    @property
    def content_count(self) -> int:
        return len(self._contents)

    def keyword_content_count(self, keyword: str) -> int:
        return sum(value == keyword for value in self._content_keywords.values())

    def pending_content_ids(self, keyword: str) -> set[str]:
        return {
            content_id
            for content_id, source_keyword in self._content_keywords.items()
            if source_keyword == keyword and content_id not in self._completed
        }

    def has_content(self, content_id: str) -> bool:
        return content_id in self._contents

    def _record(
        self,
        record_type: str,
        record_id: str,
        *,
        content_id: str = "",
        parent_id: str = "",
        account_id: str = "",
        source_url: str = "",
        payload: Any = None,
    ) -> dict[str, Any]:
        return {
            "schemaVersion": 2,
            "platform": self.platform,
            "type": record_type,
            "id": str(record_id),
            "contentId": str(content_id),
            "parentId": str(parent_id),
            "accountId": str(account_id),
            "sourceUrl": _public_url(source_url),
            "capturedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "payload": _clean(payload or {}),
        }

    def append(self, record_type: str, record_id: str, **kwargs: Any) -> bool:
        key = (self.platform, record_type, str(record_id))
        if key in self._seen:
            return False
        record = self._record(record_type, record_id, **kwargs)
        with self.raw_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        if record_type == "profile" and os.getenv("MEDIAWORKBENCH_JOB_ID"):
            from workbench.queue import collect_record
            collect_record(record["payload"], "profiles", self.platform, str(record_id), record["sourceUrl"])
        self._seen.add(key)
        if record_type == "content":
            content_id = str(record.get("contentId") or record_id)
            self._contents.add(content_id)
            account_id = str(record.get("accountId", ""))
            if account_id:
                self._content_authors[content_id] = account_id
            keyword = str((record.get("payload") or {}).get("sourceKeyword", ""))
            if keyword:
                self._content_keywords[content_id] = keyword
        if record_type in ("comment", "reply"):
            self._comment_count += 1
            content_id = str(record.get("contentId", ""))
            self._content_comment_counts[content_id] = self._content_comment_counts.get(content_id, 0) + 1
        return True

    def record_content(
        self,
        content_id: str,
        content: dict[str, Any],
        *,
        source_keyword: str,
        source_url: str,
    ) -> bool:
        if self.platform == "xhs":
            author = content.get("user") or {}
            stats = content.get("interact_info") or {}
            title = _first(content.get("title"), content.get("display_title"), content.get("desc"))
            comment_count = _first(
                stats.get("comment_count"),
                content.get("comment_count"),
                content.get("display_comment_count"),
                0,
            )
            account_id = str(author.get("user_id") or "")
            nickname = str(author.get("nickname") or "")
            public_id = _public_id(author.get("red_id"), author.get("redId"))
            publish_time = _first(content.get("time"), content.get("create_time"))
            tags = _tag_names(content.get("tag_list") or content.get("tags"))
        elif self.platform == "dy":
            author = content.get("author") or {}
            stats = content.get("statistics") or {}
            title = _first(content.get("title"), content.get("desc"))
            comment_count = _first(stats.get("comment_count"), content.get("comment_count"), 0)
            account_id = str(author.get("sec_uid") or author.get("sec_user_id") or "")
            nickname = str(author.get("nickname") or "")
            public_id = _public_id(
                author.get("unique_id"), author.get("short_id"), author.get("short_user_id")
            )
            publish_time = _first(content.get("create_time"), content.get("publish_time"))
            tags = _tag_names(content.get("text_extra") or content.get("tags"))
        elif self.platform == "ks":
            author = content.get("author") or {}
            photo = content.get("photo") or {}
            title = _first(photo.get("caption"), content.get("title"), content.get("desc"))
            comment_count = _first(photo.get("commentCount"), content.get("comment_count"), 0)
            account_id = str(author.get("id") or author.get("user_id") or "")
            nickname = str(author.get("name") or author.get("user_name") or "")
            public_id = _public_id(
                author.get("kwai_id"), author.get("kuaishou_id"),
                content.get("kwai_id"), content.get("kuaishou_id"),
            )
            publish_time = _first(photo.get("timestamp"), content.get("create_time"))
            tags = _tag_names(content.get("tags") or photo.get("tags"))
        else:
            raise ValueError(f"Unsupported lead platform: {self.platform}")

        title = str(title)
        author_payload = {
            "id": account_id,
            "nickname": nickname,
        }
        if public_id:
            author_payload["publicId"] = public_id
        payload = {
            "sourceKeyword": source_keyword,
            "title": title,
            "display_title": title,
            "description": str(_first(content.get("desc"), title)),
            "comment_count": comment_count,
            "display_comment_count": comment_count,
            "author": author_payload,
            "publish_time": publish_time,
            "tags": tags,
        }
        recorded = self.append(
            "content",
            content_id,
            content_id=content_id,
            account_id=account_id,
            source_url=source_url,
            payload=payload,
        )
        if not recorded:
            return False
        if self.platform == "xhs":
            self._register_profile(
                content_id,
                account_id,
                xsec_token=author.get("xsec_token") or content.get("xsec_token") or "",
                xsec_source=author.get("xsec_source") or content.get("xsec_source") or "pc_note",
            )
        elif self.platform == "dy":
            self._register_profile(content_id, account_id, sec_uid=account_id)
        else:
            self._register_profile(content_id, account_id, author_id=account_id)
        return True

    def _is_author_reply(self, content_id: str, account_id: str) -> bool:
        return bool(account_id and account_id == self._content_authors.get(content_id))

    def _consider_comment(
        self,
        content_id: str,
        account_id: str,
        payload: dict[str, Any],
        **profile_fields: Any,
    ) -> None:
        if not account_id or payload.get("isAuthorReply") or not _has_meaningful_text(payload.get("content")):
            return
        self._register_profile(content_id, account_id, **profile_fields)

    def _register_profile(
        self, content_id: str, account_id: str, **profile_fields: Any
    ) -> None:
        if not account_id:
            return
        if self.max_accounts and account_id not in self._profiles and len(self._profiles) >= self.max_accounts:
            return
        ref = self._profiles.setdefault(account_id, {"user_id": account_id})
        ref.update(profile_fields)
        self._content_accounts.setdefault(content_id, set()).add(account_id)

    def _event(self, event_type: str, event_id: str, **kwargs: Any) -> bool:
        key = (self.platform, event_type, str(event_id))
        if key in self._seen:
            return False
        record = self._record(event_type, event_id, **kwargs)
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._seen.add(key)
        return True

    def checkpoint(self, content_id: str) -> None:
        if self._event("checkpoint", content_id, content_id=content_id, payload={"status": "completed"}):
            self._completed.add(content_id)

    def pause(self, event_id: str, payload: dict[str, Any]) -> None:
        try:
            self._event("pause", event_id, payload=payload)
        except Exception as exc:
            _LOGGER.warning("Failed to record sidecar pause event %s: %s", event_id, exc)

    def mark_profile_resume(self, error: BaseException) -> str:
        reason = _profile_resume_reason(error)
        (self.path / "profile_resume_required.json").write_text(
            json.dumps({"platform": self.platform, "reason": reason}),
            encoding="utf-8",
        )
        return reason

    def record_xhs_comments(self, content_id: str, comments: list[dict[str, Any]]) -> int:
        written = 0
        for comment in comments:
            comment_id = str(comment.get("id", ""))
            if not comment_id:
                continue
            user = comment.get("user_info") or {}
            account_id = str(user.get("user_id", ""))
            target = comment.get("target_comment") or {}
            parent_id = str(
                target.get("id")
                or target.get("comment_id")
                or comment.get("parent_comment_id")
                or comment.get("root_comment_id")
                or comment.get("target_comment_id")
                or ""
            )
            record_type = "reply" if parent_id else "comment"
            public_id = _public_id(user.get("red_id"), user.get("redId"))
            payload = {
                "content": comment.get("content", ""),
                "nickname": user.get("nickname", ""),
                "createTime": comment.get("create_time"),
                "likeCount": comment.get("like_count", 0),
                "isAuthorReply": self._is_author_reply(content_id, account_id),
            }
            if public_id:
                payload["publicId"] = public_id
            appended = self.append(
                record_type,
                comment_id,
                content_id=content_id,
                parent_id=parent_id,
                account_id=account_id,
                source_url=f"https://www.xiaohongshu.com/explore/{content_id}",
                payload=payload,
            )
            if appended:
                written += 1
            self._consider_comment(
                content_id,
                account_id,
                _clean({
                    "content": comment.get("content", ""),
                    "nickname": user.get("nickname", ""),
                    "isAuthorReply": self._is_author_reply(content_id, account_id),
                }),
                xsec_token=user.get("xsec_token") or comment.get("xsec_token") or "",
                xsec_source=user.get("xsec_source") or comment.get("xsec_source") or "pc_comment",
            )
        return written

    def record_dy_comments(self, content_id: str, comments: list[dict[str, Any]]) -> int:
        written = 0
        for comment in comments:
            comment_id = str(comment.get("cid") or comment.get("comment_id") or "")
            if not comment_id:
                continue
            user = comment.get("user") or {}
            account_id = str(user.get("sec_uid") or user.get("sec_user_id") or "")
            parent_id = str(
                comment.get("reply_id")
                or comment.get("reply_comment_id")
                or comment.get("root_comment_id")
                or ""
            )
            if parent_id == "0":
                parent_id = ""
            record_type = "reply" if parent_id else "comment"
            public_id = _public_id(user.get("unique_id"), user.get("short_id"))
            payload = {
                "content": comment.get("text", ""),
                "nickname": user.get("nickname", ""),
                "createTime": comment.get("create_time"),
                "likeCount": comment.get("digg_count", 0),
                "isAuthorReply": self._is_author_reply(content_id, account_id),
            }
            if public_id:
                payload["publicId"] = public_id
            if self.append(
                record_type,
                comment_id,
                content_id=content_id,
                parent_id=parent_id,
                account_id=account_id,
                source_url=f"https://www.douyin.com/video/{content_id}",
                payload=payload,
            ):
                written += 1
            self._consider_comment(
                content_id,
                account_id,
                _clean({
                    "content": comment.get("text", ""),
                    "nickname": user.get("nickname", ""),
                    "isAuthorReply": self._is_author_reply(content_id, account_id),
                }),
                sec_uid=account_id,
            )
        return written

    def record_ks_comments(self, content_id: str, comments: list[dict[str, Any]]) -> int:
        written = 0
        for comment in comments:
            comment_id = str(comment.get("comment_id") or comment.get("commentId") or "")
            if not comment_id:
                continue
            author = comment.get("author") or {}
            account_id = str(
                comment.get("author_id")
                or comment.get("authorId")
                or comment.get("user_id")
                or comment.get("userId")
                or author.get("id")
                or ""
            )
            parent_id = str(
                comment.get("parent_comment_id")
                or comment.get("parentCommentId")
                or comment.get("root_comment_id")
                or comment.get("rootCommentId")
                or ""
            )
            if parent_id in ("0", comment_id):
                parent_id = ""
            record_type = "reply" if parent_id else "comment"
            public_id = _public_id(
                comment.get("kwai_id"), comment.get("kuaishou_id"),
                author.get("kwai_id"), author.get("kuaishou_id"),
            )
            payload = {
                "content": comment.get("content", ""),
                "nickname": comment.get("author_name") or comment.get("authorName") or author.get("name", ""),
                "createTime": comment.get("timestamp"),
                "likeCount": comment.get("likeCount") or comment.get("like_count", 0),
                "isAuthorReply": self._is_author_reply(content_id, account_id),
            }
            if public_id:
                payload["publicId"] = public_id
            if self.append(
                record_type,
                comment_id,
                content_id=content_id,
                parent_id=parent_id,
                account_id=account_id,
                source_url=f"https://www.kuaishou.com/short-video/{content_id}",
                payload=payload,
            ):
                written += 1
            self._consider_comment(
                content_id,
                account_id,
                _clean({
                    "content": comment.get("content", ""),
                    "nickname": comment.get("author_name") or comment.get("authorName") or author.get("name", ""),
                    "isAuthorReply": self._is_author_reply(content_id, account_id),
                }),
                author_id=account_id,
            )
        return written

    def profile_references(self) -> list[dict[str, Any]]:
        return list(self._profiles.values())[: self.max_accounts or None]

    def content_profile_references(self, content_id: str) -> list[dict[str, Any]]:
        return [
            self._profiles[account_id]
            for account_id in self._content_accounts.get(content_id, set())
            if account_id in self._profiles
        ]

    def record_profile(self, account_id: str, profile: dict[str, Any]) -> None:
        if account_id in self._profiles:
            self._profiles[account_id].update(profile=profile, profiled=True)
        self.append(
            "profile",
            account_id,
            account_id=account_id,
            source_url={
                "dy": f"https://www.douyin.com/user/{account_id}",
                "ks": f"https://www.kuaishou.com/profile/{account_id}",
            }.get(self.platform, f"https://www.xiaohongshu.com/user/profile/{account_id}"),
            payload=profile,
        )

    def deep_profile_references(self) -> list[dict[str, Any]]:
        return []

    def record_posts(self, account_id: str, posts: list[dict[str, Any]]) -> None:
        return None
