"""Persist facts at the queue seam and project the same explanation for UI and agents."""

from __future__ import annotations

import json
import re
from typing import TypedDict
from .database import dump, now, uid


class AttemptCounts(TypedDict):
    received: int
    inserted: int
    updated: int
    unchanged: int
    dataset_total: int


class StatusDetail(TypedDict):
    lifecycle: str
    wait_reason: str
    result: str
    reason_code: str
    counts: AttemptCounts
    evidence_refs: list[int]
    next_actions: list[dict[str, str | bool]]
    decision_revision: str
    scope_counts: dict[str, int]
    range_verified: bool
    explanation: str
    failure_domain: str
    access: list[dict]
    operation_refs: list[str]


class OperationResult(TypedDict, total=False):
    id: str
    kind: str
    platform: str
    capability: str
    state: str
    job_id: str | None
    attempt: int
    created: str
    updated: str
    revision: str
    reason_code: str
    next_actions: list[str]
    attempted: bool
    verdict: str
    requests: int
    elapsed_ms: int
    cleanup_complete: bool
    commit_pending: bool


def event(
    con,
    job_id,
    attempt,
    message,
    *,
    source="queue",
    kind="state",
    capability="",
    scope="",
    details=None,
    level="info",
):
    cursor = con.execute(
        "INSERT INTO events(job_id,created,level,message) VALUES(?,?,?,?)",
        (job_id, now(), level, message),
    )
    con.execute(
        "INSERT INTO event_details VALUES(?,?,?,?,?,?,?)",
        (
            cursor.lastrowid,
            attempt,
            source,
            capability,
            scope,
            kind,
            dump(details or {}),
        ),
    )
    return cursor.lastrowid


def finish(con, job, state, checkpoint, reason=None):
    current = con.execute(
        "SELECT state,attempt FROM jobs WHERE id=?", (job["id"],)
    ).fetchone()
    if current["attempt"] != job["attempt"]:
        return False
    previous = con.execute(
        "SELECT finished FROM job_attempts WHERE job_id=? AND attempt=?",
        (job["id"], job["attempt"]),
    ).fetchone()
    if (
        previous
        and previous["finished"]
        and current["state"] not in ("running", "stopping")
    ):
        return False
    if current["state"] in ("stopping", "cancelled"):
        state = "cancelled"
    else:
        from .ownership import valid_session

        if not valid_session(con, job["id"], job["attempt"]):
            state, reason = "needs_login", "account_changed"
    con.execute(
        "UPDATE collection_scopes SET outcome=?,reason=?,updated=? WHERE job_id=? AND attempt=? AND outcome='running'",
        (
            "user_stopped" if state == "cancelled" else "abnormal_stop",
            "user_stopped" if state == "cancelled" else "outcome_unconfirmed",
            now(),
            job["id"],
            job["attempt"],
        ),
    )
    con.execute(
        "UPDATE jobs SET state=?,pid=NULL,error=?,checkpoint=?,updated=? WHERE id=? AND attempt=?",
        (state, reason, dump(checkpoint), now(), job["id"], job["attempt"]),
    )
    con.execute(
        "UPDATE job_attempts SET state=?,finished=?,checkpoint=? WHERE job_id=? AND attempt=?",
        (state, now(), dump(checkpoint), job["id"], job["attempt"]),
    )
    event(
        con,
        job["id"],
        job["attempt"],
        f"Task {state}; {checkpoint.get('saved_records', 0)} distinct records saved.",
        details={"state": state, "saved_records": checkpoint.get("saved_records", 0)},
    )
    progress = con.execute(
        "SELECT reason FROM login_progress WHERE job_id=? AND attempt=?",
        (job["id"], job["attempt"]),
    ).fetchone()
    con.execute(
        "UPDATE operations SET state=?,updated=?,result=? WHERE job_id=? AND attempt=? AND state IN ('accepted','running')",
        (
            "completed"
            if state == "completed"
            else "cancelled"
            if state == "cancelled"
            else "failed",
            now(),
            dump(
                {
                    "reason_code": progress["reason"] if progress else state,
                    "next_actions": []
                    if state == "completed"
                    else ["retry", "inspect_events"],
                }
            ),
            job["id"],
            job["attempt"],
        ),
    )
    return True


def session_operation(con, job_id):
    existing = con.execute(
        "SELECT id FROM operations WHERE job_id=? AND state IN ('accepted','running')",
        (job_id,),
    ).fetchone()
    if existing:
        return existing["id"]
    job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    identity, stamp = uid(), now()
    kind = json.loads(job["options"]).get("session_action", "login")
    con.execute(
        "INSERT INTO operations(id,kind,platform,state,job_id,attempt,created,updated) VALUES(?,?,?,'accepted',?,?,?,?)",
        (identity, kind, job["platform"], job_id, job["attempt"], stamp, stamp),
    )
    return identity


def plan_scopes(con, job, attempt):
    options = json.loads(job["options"])
    mode = options["crawler_type"]
    if mode == "login":
        return
    capability, field = {
        "search": ("search", "keywords"),
        "detail": ("detail", "specified_ids"),
        "creator": ("creator", "creator_ids"),
    }[mode]
    for scope in filter(
        None, (v.strip() for v in re.split(r"[,，\r\n]+", options.get(field, "")))
    ):
        from .targets import canonical

        scope = canonical(job["platform"], capability, scope)
        con.execute(
            "INSERT OR IGNORE INTO collection_scopes VALUES(?,?,?,?,?,'not_executed','not_started',?,?,NULL)",
            (job["id"], attempt, capability, scope, "expected", now(), now()),
        )


def scope_fact(con, job_id, attempt, stage, scope, reason, details):
    capability = {"replies": "replies", "sub_comments": "replies"}.get(stage, stage)
    if (
        reason == "no_results"
        and con.execute(
            "SELECT 1 FROM scope_records WHERE job_id=? AND attempt=? AND capability=? AND scope=?",
            (job_id, attempt, capability, scope),
        ).fetchone()
    ):
        reason = "source_exhausted"
    outcome = {
        "started": "running",
        "source_exhausted": "normal_end",
        "limit_reached": "quantity_reached",
        "no_results": "no_results",
        "request_succeeded": "running",
    }.get(reason, "abnormal_stop")
    seq = event(
        con,
        job_id,
        attempt,
        "Collection scope " + reason,
        source="platform",
        kind="scope",
        capability=capability,
        scope=scope,
        details=details,
    )
    con.execute(
        "INSERT INTO collection_scopes VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id,attempt,capability,scope) DO UPDATE SET outcome=excluded.outcome,reason=excluded.reason,updated=excluded.updated,evidence_seq=excluded.evidence_seq",
        (
            job_id,
            attempt,
            capability,
            scope,
            "discovered",
            outcome,
            reason,
            now(),
            now(),
            seq,
        ),
    )
    if reason != "started":
        context = con.execute(
            "SELECT j.platform,COALESCE(s.account_id,'') account_id FROM jobs j LEFT JOIN platform_sessions s ON s.platform=j.platform WHERE j.id=?",
            (job_id,),
        ).fetchone()
        from .availability import NETWORK_REASONS, INTERFACE_REASONS

        availability = (
            "available"
            if reason
            in ("source_exhausted", "limit_reached", "no_results", "request_succeeded")
            else "restricted"
            if reason == "restriction_explicit"
            else "network_error"
            if reason in NETWORK_REASONS
            else "interface_error"
            if reason in INTERFACE_REASONS
            else "unconfirmed"
        )
        con.execute(
            "INSERT INTO access_state(platform,account_id,capability,state,reason,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(platform,account_id,capability) DO UPDATE SET state=excluded.state,reason=excluded.reason,updated=excluded.updated WHERE access_state.cooldown_until IS NULL AND access_state.state<>'manual_action'",
            (
                context["platform"],
                context["account_id"],
                capability,
                availability,
                reason,
                now(),
            ),
        )
    return reason


def discover_followups(con, job, attempt, item, kind):
    options = json.loads(job["options"])
    content_id = next(
        (
            str(item[key])
            for key in ("aweme_id", "note_id", "video_id", "bvid", "content_id")
            if item.get(key)
        ),
        None,
    )
    capability = scope = None
    if options.get("enable_comments") and "comment" not in kind and content_id:
        capability, scope = "comments", content_id
    elif options.get("enable_sub_comments") and "comment" in kind and content_id:
        child_count = next(
            (
                item.get(key)
                for key in (
                    "reply_comment_total",
                    "sub_comment_count",
                    "rcount",
                    "total_number",
                )
                if isinstance(item.get(key), int)
            ),
            0,
        )
        parent = next(
            (
                str(item[key])
                for key in ("comment_id", "cid", "rpid", "id")
                if item.get(key)
            ),
            None,
        )
        if child_count > 0 and parent:
            capability, scope = "replies", content_id + ":" + parent
    if scope:
        con.execute(
            "INSERT OR IGNORE INTO collection_scopes VALUES(?,?,?,?,?,'not_executed','not_started',?,?,NULL)",
            (job["id"], attempt, capability, scope, "discovered", now(), now()),
        )


class Recovery:
    def __init__(self, db):
        self.db = db

    def attempts(self, job_id, after=0, limit=200):
        self.db.one("jobs", job_id)
        rows = self.db.query(
            "SELECT * FROM job_attempts WHERE job_id=? AND attempt>? ORDER BY attempt LIMIT ?",
            (job_id, after, limit + 1),
        )
        return {
            "items": [
                {
                    **r,
                    "checkpoint": json.loads(r["checkpoint"]),
                    "counts": self.counts(
                        job_id,
                        r["attempt"],
                        json.loads(r["checkpoint"]).get("saved_records", 0),
                    ),
                }
                for r in rows[:limit]
            ],
            "next_after": rows[limit - 1]["attempt"] if len(rows) > limit else None,
            "history_complete": not self.db.query(
                "SELECT 1 FROM job_attempts WHERE job_id=? AND origin<>'live' LIMIT 1",
                (job_id,),
            ),
        }

    def operation(self, identity) -> OperationResult:
        row = self.db.one("operations", identity)
        result = json.loads(row.pop("result"))
        return {
            "reason_code": "",
            "next_actions": ["wait"]
            if row["state"] in ("accepted", "running", "cancelling")
            else [],
            **row,
            **result,
            "revision": row["updated"],
        }

    @staticmethod
    def cancel_session_operation(con, identity):
        from .queue import control

        operation = con.execute(
            "SELECT * FROM operations WHERE id=?", (identity,)
        ).fetchone()
        if operation["state"] not in ("accepted", "running"):
            return {"id": identity, "state": operation["state"]}
        job = con.execute(
            "SELECT attempt FROM jobs WHERE id=?", (operation["job_id"],)
        ).fetchone()
        if job["attempt"] != operation["attempt"]:
            raise ValueError("This operation belongs to an earlier attempt")
        control(con, operation["job_id"], "stop")
        return {
            "id": identity,
            "state": con.execute(
                "SELECT state FROM operations WHERE id=?", (identity,)
            ).fetchone()[0],
        }

    def scopes(self, job_id, attempt=None, after=0, limit=200):
        self.db.one("jobs", job_id)
        rows = self.db.query(
            "SELECT rowid AS cursor,* FROM collection_scopes WHERE job_id=? AND rowid>? "
            + ("AND attempt=? " if attempt is not None else "")
            + "ORDER BY rowid LIMIT ?",
            (job_id, after, *([attempt] if attempt is not None else []), limit + 1),
        )
        items = []
        for row in rows[:limit]:
            metrics = self.db.query(
                "SELECT received,inserted,updated,unchanged FROM scope_metrics WHERE job_id=? AND attempt=? AND capability=? AND scope=?",
                (job_id, row["attempt"], row["capability"], row["scope"]),
            )
            items.append(
                {
                    **row,
                    "counts": metrics[0]
                    if metrics
                    else dict(received=0, inserted=0, updated=0, unchanged=0),
                }
            )
        return {
            "items": items,
            "next_after": rows[limit - 1]["cursor"] if len(rows) > limit else None,
        }

    def diagnostic(self, job_id):
        job = self.db.one("jobs", job_id)
        # Structural whitelist: no payload, keyword, URL, cookie, raw log, account or local path.
        scopes = self.db.query(
            "SELECT attempt,capability,scope,origin,outcome,reason,created,updated,evidence_seq FROM collection_scopes WHERE job_id=? ORDER BY attempt,capability,scope",
            (job_id,),
        )
        references = {}
        for scope in scopes:
            raw = scope.pop("scope")
            if raw not in references:
                references[raw] = f"scope-{len(references) + 1}"
            scope["scope_ref"] = references[raw]
        return {
            "format_revision": 1,
            "job_id": job_id,
            "platform": job["platform"],
            "state": job["state"],
            "attempt": job["attempt"],
            "scopes": scopes,
            "attempts": [
                {k: r[k] for k in ("attempt", "state", "started", "finished", "origin")}
                for r in self.db.query(
                    "SELECT attempt,state,started,finished,origin FROM job_attempts WHERE job_id=? ORDER BY attempt",
                    (job_id,),
                )
            ],
        }

    def counts(self, job_id, attempt, dataset_total) -> AttemptCounts:
        rows = self.db.query(
            "SELECT received,inserted,updated,unchanged FROM attempt_metrics WHERE job_id=? AND attempt=?",
            (job_id, attempt),
        )
        return {
            **(
                rows[0]
                if rows
                else dict(received=0, inserted=0, updated=0, unchanged=0)
            ),
            "dataset_total": dataset_total,
        }

    def explain(self, job) -> StatusDetail:
        state = job["state"]
        checkpoint = job["checkpoint"]
        collection = checkpoint.get("collection") or {}
        issues = collection.get("issues") or []
        progress = self.db.query(
            "SELECT reason FROM login_progress WHERE job_id=? AND attempt=?",
            (job["id"], job["attempt"]),
        )
        reason = (
            issues[0]["reason"] if issues else progress[0]["reason"] if progress else ""
        )
        if state == "needs_login" and progress and progress[0]["reason"]:
            reason = progress[0]["reason"]
        if state == "cancelled":
            reason = "user_stopped"
        lifecycle = (
            state
            if state in ("queued", "running", "stopping")
            else "waiting"
            if state in ("needs_login", "waiting_access")
            else "ended"
        )
        total = self.db.query(
            "SELECT count(*) n FROM records WHERE dataset_id=?", (job["dataset_id"],)
        )[0]["n"]
        scopes = self.db.query(
            "SELECT outcome,count(*) n FROM collection_scopes WHERE job_id=? AND attempt=? GROUP BY outcome",
            (job["id"], job["attempt"]),
        )
        evidence = self.db.query(
            "SELECT e.seq FROM events e JOIN event_details d ON e.seq=d.seq WHERE e.job_id=? AND d.attempt=? ORDER BY e.seq DESC LIMIT 20",
            (job["id"], job["attempt"]),
        )
        actions = []
        access = self.db.query(
            "SELECT w.capability,s.state,s.reason,s.cooldown_until,s.checks,s.updated,s.operation_id FROM access_waits w LEFT JOIN access_state s ON s.platform=? AND s.account_id=w.account_id AND s.capability=w.capability WHERE w.job_id=? AND w.enabled=1",
            (job["platform"], job["id"]),
        )
        probes = self.db.query(
            "SELECT o.id,o.state FROM access_checks c JOIN operations o ON o.id=c.operation_id WHERE c.source_job=? AND c.source_attempt=? ORDER BY o.created DESC",
            (job["id"], job["attempt"]),
        )
        blocked = any(
            item["state"] in ("restricted", "suspected_restriction", "manual_action")
            for item in access
        )
        probing = any(
            item["state"] in ("accepted", "running", "cancelling") for item in probes
        )
        if state in ("queued", "running", "needs_login", "waiting_access"):
            actions.append(
                {
                    "id": "stop",
                    "label": "停止自动续接"
                    if state in ("needs_login", "waiting_access")
                    else "停止",
                    "enabled": True,
                }
            )
        if (
            state in ("failed", "partial", "interrupted", "cancelled")
            or state == "waiting_access"
            and not blocked
            and not probing
        ):
            actions.append({"id": "resume", "label": "恢复原任务", "enabled": True})
        if state == "needs_login":
            actions.append(
                {"id": "login", "label": "完成平台登录或验证", "enabled": True}
            )
        from tools.crawl_status import MESSAGES

        explanation = MESSAGES.get(reason, job.get("error") or "")
        if state == "needs_login":
            from .auth import MESSAGES as AUTH_MESSAGES

            explanation = AUTH_MESSAGES.get(reason, explanation)
        if state == "waiting_access" and blocked:
            reason = next(
                item["state"]
                for item in access
                if item["state"]
                in ("restricted", "suspected_restriction", "manual_action")
            )
            explanation = {
                "restricted": "平台明确要求暂缓访问，正在按保存的时间等待复查。",
                "suspected_restriction": "原请求与对照均异常，暂记疑似访问限制；尚不能确认账号风控。",
                "manual_action": "三次自动复查未恢复，请查看操作证据并手动处理。",
            }[reason]
        elif state == "waiting_access" and (job.get("error") or "").startswith(
            "对照读取成功"
        ):
            explanation = job["error"]
        elif state == "cancelled":
            explanation = "用户已停止，登录或定时复查都不会自动启动此任务。"
        failure_domain = (
            "platform"
            if issues
            else "browser"
            if reason in ("startup_failed", "startup_timeout", "browser_closed")
            else "session"
            if progress and progress[0]["reason"]
            else "queue"
        )
        range_result = (
            "not_applicable"
            if job["options"].get("crawler_type") == "login"
            else "user_stopped"
            if state == "cancelled"
            else "not_executed"
            if scopes and all(r["outcome"] == "not_executed" for r in scopes)
            else "abnormal_stop"
            if any(r["outcome"] in ("abnormal_stop", "not_executed") for r in scopes)
            else "in_progress"
            if any(r["outcome"] == "running" for r in scopes)
            else "no_results"
            if collection.get("empty")
            else "quantity_reached"
            if any(r["outcome"] == "quantity_reached" for r in scopes)
            else "normal_end"
            if scopes
            else "unconfirmed"
        )
        return {
            "lifecycle": lifecycle,
            "wait_reason": reason if lifecycle == "waiting" else "",
            "result": range_result,
            "reason_code": reason,
            "counts": self.counts(job["id"], job["attempt"], total),
            "evidence_refs": [r["seq"] for r in evidence],
            "next_actions": actions,
            "decision_revision": f"{job['attempt']}:{job['updated']}:{evidence[0]['seq'] if evidence else 0}:{max((a['updated'] or '' for a in access), default='')}",
            "explanation": explanation,
            "failure_domain": failure_domain,
            "access": access,
            "operation_refs": [p["id"] for p in probes],
            "scope_counts": {r["outcome"]: r["n"] for r in scopes},
            "range_verified": bool(scopes)
            and all(
                r["outcome"] in ("normal_end", "quantity_reached", "no_results")
                for r in scopes
            ),
        }

    def events(self, job_id, after=0, limit=200, attempt=None):
        self.db.one("jobs", job_id)
        rows = self.db.query(
            "SELECT e.*,d.attempt,d.source,d.capability,d.scope,d.kind,d.details FROM events e "
            "LEFT JOIN event_details d ON d.seq=e.seq WHERE e.job_id=? AND e.seq>? "
            + ("AND d.attempt=? " if attempt is not None else "")
            + "ORDER BY e.seq LIMIT ?",
            (job_id, after, *([attempt] if attempt is not None else []), limit + 1),
        )
        items = [
            {**r, "details": json.loads(r["details"]) if r["details"] else {}}
            for r in rows[:limit]
        ]
        return {
            "items": items,
            "next_after": items[-1]["seq"] if len(rows) > limit else None,
        }
