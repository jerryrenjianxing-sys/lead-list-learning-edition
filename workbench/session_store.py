"""Credential snapshots stay local and encrypted; metadata alone is exposed to clients."""

from __future__ import annotations
import json
import os
from pathlib import Path
from .credentials import seal, unseal
from .database import now

PLATFORMS = ("xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu")


class SessionStore:
    def __init__(self, db):
        self.db = db
        self.directory = db.root / "sessions"

    def get(self, platform):
        if platform not in PLATFORMS:
            raise ValueError("Unknown platform")
        rows = self.db.query(
            "SELECT * FROM platform_sessions WHERE platform=?", (platform,)
        )
        if not rows:
            self.db.execute(
                "INSERT OR IGNORE INTO platform_sessions(platform) VALUES(?)",
                (platform,),
            )
            rows = self.db.query(
                "SELECT * FROM platform_sessions WHERE platform=?", (platform,)
            )
        return rows[0]

    def profile(self, platform):
        row = self.get(platform)
        base = (self.db.root / "browser_data").resolve()
        # Migrate in place; prefer the profile used by the desktop's CDP path.
        candidates = [
            base / f"cdp_{platform}_user_data_dir",
            base / f"{platform}_user_data_dir",
        ]
        path = (
            Path(row["profile"])
            if row["profile"]
            else next((p for p in candidates if p.is_dir()), candidates[0])
        )
        if path.resolve() != path.absolute():
            raise ValueError("Linked browser profiles cannot be managed automatically")
        path = path.resolve()
        if path.parent != base or path.name not in {p.name for p in candidates}:
            raise ValueError(
                "Browser profile must belong to this application's data directory"
            )
        return path

    def bind(self, platform, browser):
        profile = self.profile(platform)
        self.db.execute(
            "UPDATE platform_sessions SET profile=?,browser=? WHERE platform=?",
            (str(profile), str(Path(browser).resolve()), platform),
        )
        return self.get(platform)

    def path(self, platform):
        self.get(platform)
        return self.directory / (platform + ".dpapi")

    def check(self, platform, state):
        self.get(platform)
        self.db.execute(
            "UPDATE platform_sessions SET check_state=?,checked_at=?,snapshot_valid=CASE WHEN ?='logged_out' THEN 0 ELSE snapshot_valid END WHERE platform=?",
            (state, now(), state, platform),
        )

    def save(self, platform, state, identity=None, *, new_auth=False):
        row = self.get(platform)
        identity = identity or {}
        # Account IDs are metadata, never derived from credential values.
        account_id = str(identity.get("id") or "") or None
        name = str(identity.get("name") or "")[:120] or None
        if name:
            import re

            name = re.sub(r"(?<!\d)(1\d{2})\d{4}(\d{4})(?!\d)", r"\1****\2", name)
        changed = bool(
            account_id and row["account_id"] and account_id != row["account_id"]
        )
        generation = row["generation"] + int(
            new_auth or changed or not row["generation"]
        )
        payload = {
            "version": 1,
            "platform": platform,
            "browser": row["browser"],
            "account_id": account_id,
            "generation": generation,
            "storage": state,
        }
        target = self.path(platform)
        previous = target.with_suffix(".previous.dpapi")
        old = self._matching_snapshot(row)
        replaced = False
        try:
            protected = seal(json.dumps(payload, ensure_ascii=False))
            self.directory.mkdir(parents=True, exist_ok=True)
            if old:
                # A crash between file replacement and SQLite commit must leave
                # the previous generation recoverable with its original binding.
                self._atomic_text(previous, old[0])
            self._atomic_text(target, protected)
            replaced = True
            self.db.execute(
                "UPDATE platform_sessions SET generation=?,account_id=?,account_name=?,saved_at=?,save_state='saved',snapshot_valid=1,last_error='',check_state='authenticated',checked_at=? WHERE platform=?",
                (generation, account_id, name, now(), now(), platform),
            )
        except Exception:
            if replaced:
                try:
                    if old:
                        self._atomic_text(target, old[0])
                    else:
                        target.unlink(missing_ok=True)
                except OSError:
                    pass  # load() can still use the matching previous generation.
            self.db.execute(
                "UPDATE platform_sessions SET save_state='failed',last_error='登录有效，但保存未完成，请重试保存。' WHERE platform=?",
                (platform,),
            )
            return False
        return True

    @staticmethod
    def _atomic_text(target, text):
        temp = target.with_suffix(".tmp")
        with temp.open("w", encoding="ascii") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        temp.replace(target)

    def _matching_snapshot(self, row):
        if not row["snapshot_valid"]:
            return None
        target = self.path(row["platform"])
        for candidate in (target, target.with_suffix(".previous.dpapi")):
            try:
                protected = candidate.read_text(encoding="ascii")
                data = json.loads(unseal(protected))
                if (
                    data.get("version") == 1
                    and data["platform"] == row["platform"]
                    and data["browser"] == row["browser"]
                    and data["generation"] == row["generation"]
                    and data.get("account_id") == row["account_id"]
                ):
                    return protected, data["storage"]
            except (OSError, ValueError, KeyError, RuntimeError):
                continue
        return None

    def load(self, platform):
        row = self.get(platform)
        if not row["snapshot_valid"]:
            return None
        match = self._matching_snapshot(row)
        if match:
            return match[1]
        self.db.execute(
            "UPDATE platform_sessions SET save_state='failed',last_error='无法恢复本机登录备份，请重新登录。' WHERE platform=?",
            (platform,),
        )
        return None

    def view(self, platform):
        row = self.get(platform)
        auth = self.db.query(
            "SELECT state,verified_at FROM platform_auth WHERE platform=?", (platform,)
        )
        verified = bool(auth and auth[0]["verified_at"])
        usable = (
            verified
            and row["check_state"] != "logged_out"
            and (not auth or auth[0]["state"] != "logged_out")
        )
        return {
            "account": {
                "name": row["account_name"] or ("已保存账号" if verified else None)
            },
            "session_status": row["save_state"],
            "saved_at": row["saved_at"],
            "last_check": {"state": row["check_state"], "at": row["checked_at"]},
            "session_error": row["last_error"],
            "can_select": usable,
            "needs_user_action": row["check_state"] in ("logged_out", "challenge"),
            "account_confirmation_jobs": [
                r["job_id"]
                for r in self.db.query(
                    "SELECT p.job_id FROM auth_pauses p JOIN jobs j ON j.id=p.job_id WHERE p.platform=? AND p.enabled=1 AND j.state='needs_login' AND (p.account_id IS NULL OR ? IS NULL OR p.account_id<>?)",
                    (platform, row["account_id"], row["account_id"]),
                )
            ],
        }

    def pause(self, job_id, platform, reason):
        row = self.get(platform)
        self.db.execute(
            "INSERT INTO auth_pauses(job_id,platform,account_id,generation,reason) VALUES(?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET reason=excluded.reason,generation=excluded.generation,enabled=1",
            (job_id, platform, row["account_id"], row["generation"], reason),
        )

    def resume_matching(self, platform):
        row = self.get(platform)
        if (
            row["save_state"] != "saved"
            or not row["account_id"]
            or row["check_state"] != "authenticated"
        ):
            return []
        with self.db.lock, self.db.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            jobs = con.execute(
                "SELECT p.job_id FROM auth_pauses p JOIN jobs j ON j.id=p.job_id WHERE p.platform=? AND p.account_id=? AND p.enabled=1 AND j.state='needs_login' AND p.generation<? AND COALESCE(p.resumed_generation,-1)<?",
                (platform, row["account_id"], row["generation"], row["generation"]),
            ).fetchall()
            for job in jobs:
                con.execute(
                    "UPDATE jobs SET state='queued',error=NULL,updated=? WHERE id=?",
                    (now(), job[0]),
                )
                con.execute(
                    "UPDATE auth_pauses SET resumed_generation=?,enabled=0 WHERE job_id=?",
                    (row["generation"], job[0]),
                )
                con.execute("DELETE FROM job_history_archive WHERE job_id=?", (job[0],))
            con.commit()
        return [j[0] for j in jobs]
