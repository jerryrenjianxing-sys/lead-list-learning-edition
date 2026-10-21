"""Durable capability checks, cooldowns and explicitly gated replay.

The queue owns execution. This module owns decisions and persistence, never
credentials, browser processes, collection datasets or model inference.
"""

from __future__ import annotations

import json
import time
from typing import Literal, TypedDict

from .database import Conflict, digest, dump, now, uid

CAPABILITIES = ("search", "detail", "creator", "comments", "replies", "profile")
NETWORK_REASONS = {"network_error", "request_failed", "timeout"}
INTERFACE_REASONS = {
    "response_unconfirmed",
    "non_json_response",
    "interface_changed",
    "pagination_stalled",
}
AMBIGUOUS_REASONS = {
    "empty_response",
    "http_error",
    "request_rejected",
    "empty_unconfirmed",
    "platform_error",
}


class ProbeResult(TypedDict, total=False):
    verdict: Literal[
        "available",
        "unconfirmed",
        "restricted",
        "network_error",
        "interface_error",
        "content_unavailable",
    ]
    reason: str
    attempted: bool
    requests: int
    retry_after: int
    elapsed_ms: int
    cleanup_complete: bool


class Availability:
    def __init__(self, db, *, clock=time.time):
        self.db = db
        self.clock = clock

    @staticmethod
    def _session(con, platform):
        row = con.execute(
            "SELECT * FROM platform_sessions WHERE platform=?", (platform,)
        ).fetchone()
        return (
            dict(row)
            if row
            else {
                "generation": 0,
                "account_id": None,
                "browser": "",
                "profile": "",
                "check_state": "not_checked",
            }
        )

    def request(
        self,
        con,
        platform,
        capability,
        *,
        source_job=None,
        source_attempt=None,
        automatic=False,
        original_reason="",
    ):
        if capability not in CAPABILITIES:
            raise ValueError("Unknown collection capability")
        session = self._session(con, platform)
        environment = digest(
            {k: session[k] for k in ("generation", "account_id", "browser", "profile")}
            | {"platform": platform, "capability": capability}
        )
        existing = con.execute(
            "SELECT o.id,o.state FROM access_checks c JOIN operations o ON o.id=c.operation_id WHERE c.environment=? AND o.state IN ('accepted','running')",
            (environment,),
        ).fetchone()
        if existing:
            return {**dict(existing), "reused": True}
        identity, stamp = uid(), now()
        account = session["account_id"] or ""
        sample = con.execute(
            "SELECT parameters FROM access_samples WHERE platform=? AND account_id=? AND capability=?",
            (platform, account, capability),
        ).fetchone()
        control = (
            {"keyword": "美食"}
            if capability == "search"
            else json.loads(sample[0])
            if sample
            else {}
        )
        previous = con.execute(
            "SELECT * FROM access_state WHERE platform=? AND account_id=? AND capability=?",
            (platform, account, capability),
        ).fetchone()
        reason = (
            "probe_not_enabled"
            if platform != "dy"
            else "control_missing"
            if not control
            else "cooldown_active"
            if previous
            and previous["cooldown_until"]
            and previous["cooldown_until"] > self.clock()
            else ""
        )
        result = {
            "attempted": False,
            "verdict": "unconfirmed",
            "reason_code": reason,
            "next_actions": [
                "wait" if reason == "cooldown_active" else "inspect_status"
            ],
        }
        con.execute(
            "INSERT INTO operations(id,kind,platform,capability,state,created,updated,result) VALUES(?,?,?,?,?,?,?,?)",
            (
                identity,
                "probe",
                platform,
                capability,
                "completed" if reason else "accepted",
                stamp,
                stamp,
                dump(result if reason else {}),
            ),
        )
        con.execute(
            "INSERT INTO access_checks VALUES(?,?,?,?,?,?,?,?,?)",
            (
                identity,
                environment,
                session["generation"],
                session["account_id"],
                source_job,
                source_attempt,
                int(automatic),
                dump(control),
                original_reason,
            ),
        )
        return {
            "id": identity,
            "state": "completed" if reason else "accepted",
            "reused": False,
        }

    def recover(self):
        with self.db.lock, self.db.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "UPDATE operations SET state='failed',updated=?,result=? WHERE kind='probe' AND state='running'",
                (
                    now(),
                    dump(
                        {
                            "attempted": True,
                            "verdict": "unconfirmed",
                            "reason_code": "host_interrupted",
                            "next_actions": ["retry"],
                            "cleanup_complete": False,
                        }
                    ),
                ),
            )
            con.commit()

    def claim(self):
        with self.db.lock, self.db.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if (
                not self.db.accept_writes
                or con.execute(
                    "SELECT 1 FROM jobs WHERE state IN ('running','stopping') OR (state='queued' AND json_extract(options,'$.crawler_type')='login')"
                ).fetchone()
            ):
                return None
            row = con.execute(
                "SELECT o.*,c.environment,c.generation,c.account_id,c.control,c.original_reason,c.automatic,c.source_job,c.source_attempt FROM operations o JOIN access_checks c ON c.operation_id=o.id WHERE o.state='accepted' ORDER BY o.created,o.id LIMIT 1"
            ).fetchone()
            if not row:
                return None
            session = self._session(con, row["platform"])
            if (
                session["account_id"] != row["account_id"]
                or session["generation"] != row["generation"]
            ):
                con.execute(
                    "UPDATE operations SET state='failed',updated=?,result=? WHERE id=?",
                    (
                        now(),
                        dump(
                            {
                                "attempted": False,
                                "reason_code": "session_changed",
                                "next_actions": ["retry"],
                            }
                        ),
                        row["id"],
                    ),
                )
                con.commit()
                return None
            con.execute(
                "UPDATE operations SET state='running',updated=? WHERE id=?",
                (now(), row["id"]),
            )
            con.commit()
            return dict(row)

    def cancel(self, con, identity):
        con.execute(
            "UPDATE operations SET state=CASE WHEN state='running' THEN 'cancelling' ELSE 'cancelled' END,updated=?,result=? WHERE id=? AND state IN ('accepted','running') AND kind='probe'",
            (
                now(),
                dump(
                    {
                        "attempted": False,
                        "reason_code": "user_stopped",
                        "next_actions": ["retry"],
                    }
                ),
                identity,
            ),
        )
        return {
            "id": identity,
            "state": con.execute(
                "SELECT state FROM operations WHERE id=?", (identity,)
            ).fetchone()[0],
        }

    def complete(self, operation, result: ProbeResult):
        """Commit only this execution's verdict, with no inference from a bare 403."""
        with self.db.lock, self.db.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            current = con.execute(
                "SELECT state FROM operations WHERE id=?", (operation["id"],)
            ).fetchone()
            if current[0] == "cancelling":
                clean = {
                    k: result[k]
                    for k in ("attempted", "requests", "elapsed_ms", "cleanup_complete")
                    if k in result
                }
                clean.update(reason_code="user_stopped", next_actions=["retry"])
                con.execute(
                    "UPDATE operations SET state='cancelled',updated=?,result=? WHERE id=?",
                    (now(), dump(clean), operation["id"]),
                )
                con.commit()
                return True
            if current[0] != "running":
                return False
            session = self._session(con, operation["platform"])
            same_session = (
                session["generation"] == operation["generation"]
                and session["account_id"] == operation["account_id"]
            )
            verdict = (
                result.get("verdict", "unconfirmed") if same_session else "unconfirmed"
            )
            reason = (
                result.get("reason", "outcome_unconfirmed")
                if same_session
                else "session_changed"
            )
            # Whitelist process output; no body, exception, local path or account is returned.
            clean = {
                k: result[k]
                for k in ("attempted", "requests", "elapsed_ms", "cleanup_complete")
                if k in result
            }
            clean.update(
                verdict=verdict,
                reason_code=reason,
                next_actions=["continue"]
                if verdict == "available"
                else ["inspect_status", "retry_later"],
            )
            con.execute(
                "UPDATE operations SET state='completed',updated=?,result=? WHERE id=?",
                (now(), dump(clean), operation["id"]),
            )
            if same_session and operation["platform"] == "dy":
                self._decision(
                    con, operation, verdict, reason, result.get("retry_after", 0)
                )
            con.commit()
            return True

    def _decision(self, con, operation, verdict, reason, retry_after):
        platform, account, capability = (
            operation["platform"],
            operation["account_id"] or "",
            operation["capability"],
        )
        previous = con.execute(
            "SELECT * FROM access_state WHERE platform=? AND account_id=? AND capability=?",
            (platform, account, capability),
        ).fetchone()
        checks = (previous["checks"] if previous else 0) + int(
            bool(operation["automatic"])
        )
        restricted = verdict == "restricted"
        suspected = (
            verdict == "unconfirmed"
            and operation["original_reason"] in AMBIGUOUS_REASONS
            and reason in AMBIGUOUS_REASONS | {"empty_control"}
        )
        deadline = None
        recheck_failed = (
            operation["automatic"]
            and verdict != "available"
            and previous
            and previous["state"] in ("restricted", "suspected_restriction")
        )
        if restricted or suspected or recheck_failed:
            state = (
                previous["state"]
                if recheck_failed
                else "restricted"
                if restricted
                else "suspected_restriction"
            )
            if operation["automatic"] and checks >= 3:
                state = "manual_action"
            else:
                # The initial anomaly schedules 60 minutes; subsequent failed checks 2h and 4h.
                deadline = self.clock() + max(
                    (3600, 7200, 14400)[min(checks, 2)], max(0, int(retry_after))
                )
        elif verdict == "available":
            state, checks = "available", 0
        else:
            state = verdict
        con.execute(
            "INSERT INTO access_state VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(platform,account_id,capability) DO UPDATE SET state=excluded.state,reason=excluded.reason,cooldown_until=excluded.cooldown_until,checks=excluded.checks,updated=excluded.updated,operation_id=excluded.operation_id",
            (
                platform,
                account,
                capability,
                state,
                reason,
                deadline,
                checks,
                now(),
                operation["id"],
            ),
        )
        if state == "available":
            self._resume(con, platform, account)

    def pause(self, con, job, capability, reason, retry_after=0):
        session = self._session(con, job["platform"])
        con.execute(
            "INSERT INTO access_waits VALUES(?,?,?,1,?) ON CONFLICT(job_id,capability) DO UPDATE SET enabled=1",
            (job["id"], capability, session["account_id"] or "", now()),
        )
        if job["platform"] == "dy" and reason == "restriction_explicit":
            con.execute(
                "INSERT INTO access_state VALUES(?,?,?,'restricted',?,?,0,?,NULL) ON CONFLICT(platform,account_id,capability) DO UPDATE SET state='restricted',reason=excluded.reason,cooldown_until=MAX(COALESCE(access_state.cooldown_until,0),excluded.cooldown_until),updated=excluded.updated",
                (
                    job["platform"],
                    session["account_id"] or "",
                    capability,
                    reason,
                    self.clock() + max(3600, retry_after),
                    now(),
                ),
            )
        elif job["platform"] == "dy" and reason in AMBIGUOUS_REASONS:
            self.request(
                con,
                job["platform"],
                capability,
                source_job=job["id"],
                source_attempt=job["attempt"],
                original_reason=reason,
            )

    def schedule_due(self):
        with self.db.lock, self.db.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            due = con.execute(
                "SELECT * FROM access_state WHERE platform='dy' AND cooldown_until IS NOT NULL AND cooldown_until<=? AND checks<3 AND state IN ('restricted','suspected_restriction')",
                (self.clock(),),
            ).fetchall()
            for state in due:
                session = self._session(con, state["platform"])
                if (session["account_id"] or "") != state["account_id"] or session[
                    "check_state"
                ] != "authenticated":
                    continue
                waiting = con.execute(
                    "SELECT 1 FROM access_waits w JOIN jobs j ON j.id=w.job_id WHERE w.account_id=? AND w.capability=? AND w.enabled=1 AND j.state='waiting_access'",
                    (state["account_id"], state["capability"]),
                ).fetchone()
                if waiting:
                    self.request(
                        con,
                        state["platform"],
                        state["capability"],
                        automatic=True,
                        original_reason="request_rejected",
                    )
            con.commit()

    def _resume(self, con, platform, account):
        if (
            not account
            or self._session(con, platform)["check_state"] != "authenticated"
        ):
            return
        jobs = con.execute(
            "SELECT DISTINCT j.id FROM jobs j JOIN access_waits w ON j.id=w.job_id WHERE j.platform=? AND j.state='waiting_access' AND w.enabled=1 AND w.account_id=?",
            (platform, account),
        ).fetchall()
        for job in jobs:
            blocked = con.execute(
                "SELECT 1 FROM access_waits w LEFT JOIN access_state s ON s.platform=? AND s.account_id=w.account_id AND s.capability=w.capability WHERE w.job_id=? AND w.enabled=1 AND COALESCE(s.state,'unknown')<>'available'",
                (platform, job["id"]),
            ).fetchone()
            if not blocked:
                con.execute(
                    "UPDATE jobs SET state='queued',updated=?,error=NULL WHERE id=? AND state='waiting_access'",
                    (now(), job["id"]),
                )
                con.execute(
                    "UPDATE access_waits SET enabled=0 WHERE job_id=?", (job["id"],)
                )

    def view(self, platform):
        with self.db.connect() as con:
            session = self._session(con, platform)
            rows = con.execute(
                "SELECT capability,state,reason,cooldown_until,checks,updated,operation_id FROM access_state WHERE platform=? AND account_id=?",
                (platform, session["account_id"] or ""),
            ).fetchall()
        return {
            "items": [dict(r) for r in rows],
            "automatic_checks_enabled": platform == "dy",
        }
