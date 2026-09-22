import json
import importlib
import importlib.util

import pytest


FIELDS = {
    "schemaVersion",
    "platform",
    "type",
    "id",
    "contentId",
    "parentId",
    "accountId",
    "sourceUrl",
    "capturedAt",
    "payload",
}


def _read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _sink(*args, **kwargs):
    assert importlib.util.find_spec("tools.lead_sink") is not None, "shared lead sink module is missing"
    return importlib.import_module("tools.lead_sink").LeadSink(*args, **kwargs)


def test_sink_appends_sanitized_schema_and_dedupes_after_restart(tmp_path):
    sink = _sink(tmp_path, "xhs", max_comments=3)
    assert sink.append(
        "content",
        "note-1",
        content_id="note-1",
        source_url="https://www.xiaohongshu.com/explore/note-1?xsec_token=secret",
        payload={"title": "采购样品", "xsec_token": "secret", "nested": {"cookie": "bad"}},
    )
    sink.checkpoint("note-1")

    raw = _read(tmp_path / "raw.jsonl")
    events = _read(tmp_path / "events.jsonl")
    assert set(raw[0]) == FIELDS
    assert raw[0]["schemaVersion"] == 2
    assert raw[0]["sourceUrl"] == "https://www.xiaohongshu.com/explore/note-1"
    assert "secret" not in json.dumps(raw + events, ensure_ascii=False)
    assert set(events[0]) == FIELDS
    assert events[0]["type"] == "checkpoint"

    resumed = _sink(tmp_path, "xhs", max_comments=3)
    assert resumed.is_completed("note-1")
    assert not resumed.append("content", "note-1", content_id="note-1")
    assert len(_read(tmp_path / "raw.jsonl")) == 1


@pytest.mark.parametrize(
    ("platform", "content", "expected"),
    [
        (
            "xhs",
            {
                "title": "一次性雨衣",
                "desc": "支持批发定制",
                "time": 1700000000,
                "interact_info": {"comment_count": "12"},
                "user": {
                "user_id": "xhs-author",
                "nickname": "雨衣工厂",
                    "redId": "raincoat-factory",
                    "avatar": "https://media.example/avatar?pkey=secret",
                },
                "tag_list": [{"type": "topic", "name": "雨衣"}],
                "image_list": [{"url": "https://media.example/image?pkey=secret"}],
                "manifest": "manifest-secret",
            },
            ("一次性雨衣", "12", "xhs-author", "雨衣工厂", 1700000000, ["雨衣"]),
        ),
        (
            "dy",
            {
                "desc": "EVA雨衣采购",
                "create_time": 1700000001,
                "statistics": {"comment_count": 23},
                "author": {
                    "sec_uid": "dy-author",
                    "nickname": "劳保采购",
                    "unique_id": "buyer-01",
                    "avatar_thumb": {"url_list": ["https://media.example/avatar?token=secret"]},
                },
                "text_extra": [{"hashtag_name": "雨衣采购"}],
                "video": {"play_addr": {"url_list": ["https://media.example/play?sign=secret"]}},
                "manifest": "manifest-secret",
            },
            ("EVA雨衣采购", 23, "dy-author", "劳保采购", 1700000001, ["雨衣采购"]),
        ),
        (
            "ks",
            {
                "author": {
                    "id": "ks-author",
                    "name": "批发采购商",
                    "kwai_id": "kwai-factory",
                    "headerUrl": "https://media.example/avatar?pkey=secret",
                },
                "photo": {
                    "id": "ks-video",
                    "caption": "一次性雨衣批发",
                    "commentCount": 34,
                    "timestamp": 1700000002,
                    "manifest": "manifest-secret",
                    "videoResource": {"h264": "https://media.example/play?pkey=secret"},
                    "photoUrl": "https://media.example/photo?pkey=secret",
                },
                "tags": [{"name": "雨衣批发"}],
            },
            ("一次性雨衣批发", 34, "ks-author", "批发采购商", 1700000002, ["雨衣批发"]),
        ),
    ],
)
def test_record_content_persists_only_public_summary_fields(platform, content, expected, tmp_path):
    sink = _sink(tmp_path, platform)

    assert sink.record_content(
        "content-1",
        content,
        source_keyword="雨衣采购",
        source_url="https://example.test/content-1?token=route-secret",
    )

    record = _read(tmp_path / "raw.jsonl")[0]
    payload = record["payload"]
    title, comment_count, author_id, nickname, publish_time, tags = expected
    assert payload == {
        "sourceKeyword": "雨衣采购",
        "title": title,
        "display_title": title,
        "description": content.get("desc") or title,
        "comment_count": comment_count,
        "display_comment_count": comment_count,
        "author": {
            "id": author_id,
            "nickname": nickname,
            "publicId": {
                "xhs": "raincoat-factory",
                "dy": "buyer-01",
                "ks": "kwai-factory",
            }[platform],
        },
        "publish_time": publish_time,
        "tags": tags,
    }
    persisted = (tmp_path / "raw.jsonl").read_text(encoding="utf-8")
    assert all(
        forbidden not in persisted
        for forbidden in ("manifest", "videoResource", "photoUrl", "media.example", "secret")
    )


@pytest.mark.parametrize(
    ("platform", "content", "internal_id"),
    [
        ("xhs", {"user": {"user_id": "internal-xhs"}}, "internal-xhs"),
        (
            "dy",
            {
                "author": {
                    "sec_uid": "internal-dy",
                    "unique_id": "0",
                    "short_id": "",
                    "short_user_id": "0",
                }
            },
            "internal-dy",
        ),
        ("ks", {"author": {"id": "internal-ks", "user_id": "also-internal"}}, "internal-ks"),
    ],
)
def test_record_content_never_uses_internal_id_as_public_id(platform, content, internal_id, tmp_path):
    sink = _sink(tmp_path, platform)

    assert sink.record_content(
        "content-1",
        content,
        source_keyword="雨衣采购",
        source_url="https://example.test/content-1",
    )

    author = _read(tmp_path / "raw.jsonl")[0]["payload"]["author"]
    assert author["id"] == internal_id
    assert "publicId" not in author


@pytest.mark.parametrize(
    ("platform", "content", "record_comments", "comments"),
    [
        (
            "xhs",
            {"user": {"user_id": "author-id"}},
            lambda sink, items: sink.record_xhs_comments("content-1", items),
            [
                {"id": "c1", "content": "采购", "user_info": {"user_id": "commenter-id"}},
                {"id": "r1", "content": "需要报价", "target_comment": {"id": "c1"}, "user_info": {"user_id": "replier-id"}},
                {"id": "c2", "content": "再次采购", "user_info": {"user_id": "commenter-id"}},
            ],
        ),
        (
            "dy",
            {"author": {"sec_uid": "author-id"}},
            lambda sink, items: sink.record_dy_comments("content-1", items),
            [
                {"cid": "c1", "text": "采购", "user": {"sec_uid": "commenter-id"}},
                {"cid": "r1", "text": "需要报价", "reply_id": "c1", "user": {"sec_uid": "replier-id"}},
                {"cid": "c2", "text": "再次采购", "user": {"sec_uid": "commenter-id"}},
            ],
        ),
        (
            "ks",
            {"author": {"id": "author-id"}},
            lambda sink, items: sink.record_ks_comments("content-1", items),
            [
                {"comment_id": "c1", "content": "采购", "author_id": "commenter-id"},
                {"comment_id": "r1", "content": "需要报价", "parentCommentId": "c1", "author_id": "replier-id"},
                {"comment_id": "c2", "content": "再次采购", "author_id": "commenter-id"},
            ],
        ),
    ],
)
def test_content_author_and_commenters_share_one_deduplicated_profile_queue(
    platform, content, record_comments, comments, tmp_path
):
    sink = _sink(tmp_path, platform, max_accounts=0)

    assert sink.record_content(
        "content-1", content, source_keyword="采购", source_url="https://example.test/content-1"
    )
    assert record_comments(sink, comments) == 3
    assert {item["user_id"] for item in sink.profile_references()} == {
        "author-id",
        "commenter-id",
        "replier-id",
    }


def test_xhs_comment_capture_keeps_all_raw_records_and_valid_refs(tmp_path):
    sink = _sink(tmp_path, "xhs", max_accounts=2, max_comments=2)
    comments = [
        {
            "id": "c1",
            "note_id": "n1",
            "content": "采购100套，怎么报价",
            "user_info": {"user_id": "u1", "nickname": "甲", "xsec_token": "t1"},
            "xsec_token": "route-1",
        },
        {
            "id": "r1",
            "note_id": "n1",
            "content": "MOQ多少",
            "target_comment": {"id": "c1"},
            "user_info": {"user_id": "u2", "nickname": "乙", "xsec_token": "t2"},
        },
        {
            "id": "c2",
            "note_id": "n1",
            "content": "第三条",
            "user_info": {"user_id": "u3", "xsec_token": "t3"},
        },
    ]

    assert sink.record_xhs_comments("n1", comments) == 3
    assert sink.remaining_comments == 0
    assert [record["type"] for record in _read(tmp_path / "raw.jsonl")] == ["comment", "reply", "comment"]
    assert [ref["user_id"] for ref in sink.profile_references()] == ["u1", "u2"]
    persisted = (tmp_path / "raw.jsonl").read_text(encoding="utf-8")
    assert all(secret not in persisted for secret in ("t1", "t2", "route-1", "xsec_token"))


def test_profile_records_do_not_add_scores_or_trigger_deep_analysis(tmp_path):
    sink = _sink(tmp_path, "xhs", max_accounts=3, deep_profile_limit=2, max_comments=10)
    sink.record_xhs_comments(
        "n1",
        [
            {"id": "c1", "content": "采购定制包装，MOQ多少", "user_info": {"user_id": "buyer"}},
            {"id": "c2", "content": "好看", "user_info": {"user_id": "viewer"}},
            {"id": "c3", "content": "批发报价", "user_info": {"user_id": "contacted"}},
        ],
    )
    sink.record_profile("buyer", {"basicInfo": {"desc": "品牌采购经理"}})
    sink.record_profile("viewer", {"basicInfo": {"desc": "生活记录"}})
    sink.record_profile("contacted", {"basicInfo": {"desc": "批发商，电话 13800138000"}})

    assert {ref["user_id"] for ref in sink.profile_references()} == {"buyer", "viewer", "contacted"}
    assert all("score" not in ref for ref in sink.profile_references())
    assert sink.deep_profile_references() == []


def test_duplicate_comment_replay_rebuilds_memory_refs_after_restart(tmp_path):
    comment = {
        "id": "c1",
        "content": "采购报价",
        "user_info": {"user_id": "buyer", "xsec_token": "route-secret"},
    }
    first = _sink(tmp_path, "xhs", max_comments=1)
    assert first.record_xhs_comments("n1", [comment]) == 1

    resumed = _sink(tmp_path, "xhs", max_comments=1)
    assert resumed.remaining_comments == 0
    assert [ref["user_id"] for ref in resumed.profile_references()] == ["buyer"]
    assert resumed.record_xhs_comments("n1", [comment]) == 0
    assert [ref["user_id"] for ref in resumed.profile_references()] == ["buyer"]
    assert resumed.profile_references()[0]["xsec_token"] == "route-secret"
    assert len(_read(tmp_path / "raw.jsonl")) == 1
    assert "route-secret" not in (tmp_path / "raw.jsonl").read_text(encoding="utf-8")


def test_restart_rebuilds_content_accounts_without_profile_analysis(tmp_path):
    sink = _sink(tmp_path, "xhs", max_comments=10)
    sink.record_xhs_comments(
        "n1",
        [
            {
                "id": "c1",
                "content": "采购报价",
                "user_info": {"user_id": "buyer", "xsec_token": "route-secret"},
            },
            {
                "id": "r1",
                "content": "需要样品",
                "root_comment_id": "c1",
                "user_info": {"user_id": "reply-buyer"},
            },
        ],
    )
    sink.record_profile("buyer", {"basicInfo": {"desc": "品牌采购经理"}})

    resumed = _sink(tmp_path, "xhs", max_comments=10)

    assert resumed.content_comment_count("n1") == 2
    assert {ref["user_id"] for ref in resumed.content_profile_references("n1")} == {
        "buyer",
        "reply-buyer",
    }
    assert resumed.deep_profile_references() == []
    assert "route-secret" not in (tmp_path / "raw.jsonl").read_text(encoding="utf-8")


def test_xhs_reply_parent_accepts_all_known_payload_shapes(tmp_path):
    sink = _sink(tmp_path, "xhs", max_comments=3)

    sink.record_xhs_comments(
        "n1",
        [
            {"id": "r1", "target_comment": {"id": "p1"}},
            {"id": "r2", "parent_comment_id": "p2"},
            {"id": "r3", "root_comment_id": "p3"},
        ],
    )

    records = _read(tmp_path / "raw.jsonl")
    assert [(record["type"], record["parentId"]) for record in records] == [
        ("reply", "p1"),
        ("reply", "p2"),
        ("reply", "p3"),
    ]


def test_recursive_privacy_cleaning_removes_response_blobs_and_sensitive_url_queries(tmp_path):
    sink = _sink(tmp_path, "xhs")

    sink.append(
        "content",
        "n1",
        payload={
            "captcha": "captcha-secret",
            "verification": "verify-secret",
            "raw_response": "raw-secret",
            "nested": {
                "response": "response-secret",
                "body": "body-secret",
                "publicUrl": (
                    "https://example.test/path?safe=1&xsec_token=route-secret"
                    "&session=sess-secret&cookie=cookie-secret"
                ),
            },
        },
    )

    persisted = (tmp_path / "raw.jsonl").read_text(encoding="utf-8")
    assert "safe=1" in persisted
    assert all(
        secret not in persisted
        for secret in (
            "captcha-secret",
            "verify-secret",
            "raw-secret",
            "response-secret",
            "body-secret",
            "route-secret",
            "sess-secret",
            "cookie-secret",
        )
    )


def test_restart_discards_only_damaged_jsonl_tail_before_next_append(tmp_path):
    sink = _sink(tmp_path, "xhs")
    sink.append("content", "n1")
    with (tmp_path / "raw.jsonl").open("a", encoding="utf-8") as stream:
        stream.write('{"broken":')

    resumed = _sink(tmp_path, "xhs")
    resumed.append("content", "n2")

    assert [record["id"] for record in _read(tmp_path / "raw.jsonl")] == ["n1", "n2"]


def test_sink_keeps_explicit_profile_reference_limit(tmp_path):
    sink = _sink(
        tmp_path,
        "xhs",
        max_accounts=999,
        max_comments=999,
    )

    assert sink.max_accounts == 999


def test_douyin_comments_replies_and_profiles_use_normalized_public_fields(tmp_path):
    sink = _sink(tmp_path, "dy", max_comments=3)

    assert sink.record_dy_comments(
        "a1",
        [
            {
                "cid": "c1",
                "text": "采购 10 箱，怎么报价",
                "create_time": 1,
                "digg_count": 2,
                "reply_id": "0",
                "user": {
                    "sec_uid": "buyer-sec",
                    "nickname": "采购",
                    "session": "secret",
                },
            },
            {
                "cid": "r1",
                "text": "MOQ 多少",
                "reply_id": "c1",
                "user": {"sec_uid": "reply-sec", "nickname": "外贸"},
            },
        ],
    ) == 2
    sink.record_profile("buyer-sec", {"user": {"signature": "采购经理"}})
    records = _read(tmp_path / "raw.jsonl")
    assert [(item["type"], item["id"], item["parentId"]) for item in records[:2]] == [
        ("comment", "c1", ""),
        ("reply", "r1", "c1"),
    ]
    assert records[0]["accountId"] == "buyer-sec"
    assert records[0]["sourceUrl"] == "https://www.douyin.com/video/a1"
    assert records[2]["sourceUrl"] == "https://www.douyin.com/user/buyer-sec"
    persisted = (tmp_path / "raw.jsonl").read_text(encoding="utf-8")
    assert all(secret not in persisted for secret in ("secret", "route-secret", "session"))


def test_kuaishou_comments_keep_real_parent_and_public_profile_urls(tmp_path):
    sink = _sink(tmp_path, "ks", max_comments=4)

    assert sink.record_ks_comments(
        "v1",
        [
            {
                "comment_id": 11,
                "content": "报价",
                "author_id": "buyer-ks",
                "author_name": "采购",
            },
            {
                "commentId": "12",
                "content": "要样品",
                "authorId": "reply-ks",
                "rootCommentId": 11,
                "parentCommentId": 11,
            },
        ],
    ) == 2
    sink.record_profile("buyer-ks", {"name": "劳保采购"})
    records = _read(tmp_path / "raw.jsonl")
    assert [(item["type"], item["id"], item["parentId"]) for item in records[:2]] == [
        ("comment", "11", ""),
        ("reply", "12", "11"),
    ]
    assert records[0]["sourceUrl"] == "https://www.kuaishou.com/short-video/v1"
    assert records[2]["sourceUrl"] == "https://www.kuaishou.com/profile/buyer-ks"
    persisted = (tmp_path / "raw.jsonl").read_text(encoding="utf-8")
    assert "secret" not in persisted


def test_platform_composite_ids_do_not_cross_dedupe_in_shared_run(tmp_path):
    xhs = _sink(tmp_path, "xhs")
    dy = _sink(tmp_path, "dy")
    ks = _sink(tmp_path, "ks")

    assert xhs.append("content", "same", content_id="same")
    assert dy.append("content", "same", content_id="same")
    assert ks.append("content", "same", content_id="same")
    assert len(_read(tmp_path / "raw.jsonl")) == 3


def test_each_platform_keeps_all_raw_comments(tmp_path):
    dy = _sink(tmp_path, "dy", max_comments=2)
    ks = _sink(tmp_path, "ks", max_comments=2)

    assert dy.record_dy_comments(
        "a1",
        [
            {"cid": "d1", "user": {"sec_uid": "du1"}},
            {"cid": "d2", "user": {"sec_uid": "du2"}},
            {"cid": "d3", "user": {"sec_uid": "du3"}},
        ],
    ) == 3
    assert ks.record_ks_comments(
        "v1",
        [
            {"comment_id": "k1", "author_id": "ku1"},
            {"comment_id": "k2", "author_id": "ku2"},
            {"comment_id": "k3", "author_id": "ku3"},
        ],
    ) == 3

    comments = [
        record
        for record in _read(tmp_path / "raw.jsonl")
        if record["type"] in {"comment", "reply"}
    ]
    assert [record["platform"] for record in comments].count("dy") == 3
    assert [record["platform"] for record in comments].count("ks") == 3


def test_three_platform_comments_mark_content_author_after_restart(tmp_path):
    cases = {
        "xhs": lambda sink: sink.record_xhs_comments(
            "content-1",
            [
                {"id": "author", "user_info": {"user_id": "content-author"}},
                {"id": "buyer", "user_info": {"user_id": "buyer"}},
            ],
        ),
        "dy": lambda sink: sink.record_dy_comments(
            "content-1",
            [
                {"cid": "author", "user": {"sec_uid": "content-author"}},
                {"cid": "buyer", "user": {"sec_uid": "buyer"}},
            ],
        ),
        "ks": lambda sink: sink.record_ks_comments(
            "content-1",
            [
                {"comment_id": "author", "author_id": "content-author"},
                {"comment_id": "buyer", "author_id": "buyer"},
            ],
        ),
    }

    for platform, capture in cases.items():
        run_dir = tmp_path / platform
        sink = _sink(run_dir, platform)
        sink.append(
            "content",
            "content-1",
            content_id="content-1",
            account_id="content-author",
        )

        capture(_sink(run_dir, platform))

        comments = [
            record
            for record in _read(run_dir / "raw.jsonl")
            if record["type"] in {"comment", "reply"}
        ]
        assert [record["payload"]["isAuthorReply"] for record in comments] == [
            True,
            False,
        ]


@pytest.mark.parametrize(
    ("platform", "record_comments", "author_reply"),
    [
        ("xhs", lambda sink, comments: sink.record_xhs_comments("content-1", comments), {"id": "reply", "content": "求购", "user_info": {"user_id": "author", "nickname": "外贸"}}),
        ("dy", lambda sink, comments: sink.record_dy_comments("content-1", comments), {"cid": "reply", "text": "求购", "user": {"sec_uid": "author", "nickname": "外贸"}}),
        ("ks", lambda sink, comments: sink.record_ks_comments("content-1", comments), {"comment_id": "reply", "content": "求购", "author_id": "author", "author_name": "外贸"}),
    ],
)
def test_profile_references_exclude_content_author_replies(platform, record_comments, author_reply, tmp_path):
    sink = _sink(tmp_path, platform)
    sink.append("content", "content-1", content_id="content-1", account_id="author")

    assert record_comments(sink, [author_reply]) == 1
    assert sink.profile_references() == []


def test_profile_reference_cap_does_not_limit_raw_comments(tmp_path):
    sink = _sink(tmp_path, "xhs", max_accounts=150)
    comments = [
        {"id": str(index), "content": "好看", "user_info": {"user_id": str(index)}}
        for index in range(151)
    ]

    assert sink.record_xhs_comments("content-1", comments) == 151
    assert len(sink.profile_references()) == 150
    assert len(_read(tmp_path / "raw.jsonl")) == 151


def test_zero_profile_reference_limit_collects_every_account(tmp_path):
    sink = _sink(tmp_path, "xhs", max_accounts=0)
    comments = [
        {"id": str(index), "content": "需要报价", "user_info": {"user_id": str(index)}}
        for index in range(151)
    ]

    assert sink.record_xhs_comments("content-1", comments) == 151
    assert len(sink.profile_references()) == 151


def test_comment_payloads_keep_only_existing_public_ids(tmp_path):
    _sink(tmp_path / "xhs", "xhs").record_xhs_comments(
        "note-1", [{"id": "x", "content": "x", "user_info": {"user_id": "internal", "redId": "red-id"}}]
    )
    _sink(tmp_path / "dy", "dy").record_dy_comments(
        "video-1",
        [
            {"cid": "unique", "text": "x", "user": {"sec_uid": "internal", "unique_id": "unique-id", "short_id": "123"}},
            {"cid": "short", "text": "x", "user": {"sec_uid": "internal-2", "short_id": "456"}},
            {"cid": "invalid", "text": "x", "user": {"sec_uid": "internal-3", "short_id": "0"}},
        ],
    )
    _sink(tmp_path / "ks", "ks").record_ks_comments(
        "video-1",
        [
            {"comment_id": "public", "content": "x", "author_id": "internal", "author": {"kwai_id": "kwai-id"}},
            {"comment_id": "internal", "content": "x", "author_id": "internal-only"},
        ],
    )

    assert [_read(tmp_path / "xhs" / "raw.jsonl")[0]["payload"]["publicId"]] == ["red-id"]
    assert [record["payload"].get("publicId") for record in _read(tmp_path / "dy" / "raw.jsonl")] == ["unique-id", "456", None]
    assert [record["payload"].get("publicId") for record in _read(tmp_path / "ks" / "raw.jsonl")] == ["kwai-id", None]


def test_normal_comment_text_creates_profile_reference_but_non_text_does_not(tmp_path):
    sink = _sink(tmp_path, "xhs")
    sink.append("content", "note-1", content_id="note-1", account_id="author")

    sink.record_xhs_comments(
        "note-1",
        [
            {"id": "normal", "content": "好看", "user_info": {"user_id": "normal"}},
            {"id": "blank", "content": "  ", "user_info": {"user_id": "blank"}},
            {"id": "emoji", "content": "[赞][比心]", "user_info": {"user_id": "emoji"}},
            {"id": "punctuation", "content": "!!!", "user_info": {"user_id": "punctuation"}},
            {"id": "keycap-number", "content": "1️⃣", "user_info": {"user_id": "keycap-number"}},
            {"id": "keycap-hash", "content": "#️⃣", "user_info": {"user_id": "keycap-hash"}},
            {"id": "keycap-star", "content": "*️⃣", "user_info": {"user_id": "keycap-star"}},
            {"id": "number", "content": "100", "user_info": {"user_id": "number"}},
            {"id": "author", "content": "谢谢", "user_info": {"user_id": "author"}},
        ],
    )

    assert [ref["user_id"] for ref in sink.profile_references()] == ["normal", "number"]


def test_raw_comments_are_not_limited_by_legacy_comment_budget(tmp_path):
    sink = _sink(tmp_path, "dy", max_comments=1)

    assert sink.record_dy_comments(
        "video-1",
        [
            {"cid": "first", "text": "第一条", "user": {"sec_uid": "first"}},
            {"cid": "second", "text": "第二条", "user": {"sec_uid": "second"}},
        ],
    ) == 2
    assert [record["id"] for record in _read(tmp_path / "raw.jsonl")] == ["first", "second"]


def test_pause_does_not_interrupt_crawl_when_sidecar_write_fails(tmp_path, monkeypatch):
    sink = _sink(tmp_path, "dy")

    def fail_write(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(sink, "_event", fail_write)

    sink.pause("profile-user", {"status": "failed"})


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("account blocked"), "risk_control"),
        (RuntimeError("login session expired"), "login_expired"),
        (
            RuntimeError("Target page, context or browser has been closed"),
            "browser_closed",
        ),
        (RuntimeError("All connection attempts failed"), "network_error"),
    ],
)
def test_profile_resume_marker_classifies_safe_recovery_reason(
    tmp_path, error, expected
):
    sink = _sink(tmp_path, "ks")

    assert sink.mark_profile_resume(error) == expected
    assert json.loads(
        (tmp_path / "profile_resume_required.json").read_text(encoding="utf-8")
    ) == {"platform": "ks", "reason": expected}
