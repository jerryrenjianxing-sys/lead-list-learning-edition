"""Durable task state and atomic, replayable write receipts. No model runtime."""

from __future__ import annotations
import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(dump(value).encode("utf-8")).hexdigest()


class Conflict(ValueError):
    pass


class Missing(KeyError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts(key TEXT PRIMARY KEY, scope TEXT NOT NULL, hash TEXT NOT NULL, response TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY, name TEXT NOT NULL, created TEXT NOT NULL, metadata TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id), ordinal INTEGER NOT NULL, source_key TEXT NOT NULL, platform TEXT, kind TEXT NOT NULL, source_url TEXT, payload TEXT NOT NULL, revision TEXT NOT NULL, observed TEXT NOT NULL, UNIQUE(dataset_id,source_key));
CREATE INDEX IF NOT EXISTS records_dataset ON records(dataset_id,ordinal);
CREATE TABLE IF NOT EXISTS raw_versions(record_id TEXT NOT NULL REFERENCES records(id), revision TEXT NOT NULL, payload TEXT NOT NULL, observed TEXT NOT NULL, PRIMARY KEY(record_id,revision));
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, platform TEXT NOT NULL, state TEXT NOT NULL, options TEXT NOT NULL, dataset_id TEXT REFERENCES datasets(id), attempt INTEGER NOT NULL DEFAULT 0, pid INTEGER, process_created REAL, created TEXT NOT NULL, updated TEXT NOT NULL, error TEXT, checkpoint TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id), created TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analyses(id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id), name TEXT NOT NULL, goal TEXT NOT NULL, schema_json TEXT, state TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, metadata TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analysis_inputs(analysis_id TEXT NOT NULL REFERENCES analyses(id), record_id TEXT NOT NULL, ordinal INTEGER NOT NULL, snapshot TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', note TEXT, PRIMARY KEY(analysis_id,record_id));
CREATE TABLE IF NOT EXISTS results(id TEXT NOT NULL, analysis_id TEXT NOT NULL REFERENCES analyses(id), payload TEXT NOT NULL, evidence TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(analysis_id,id));
CREATE TABLE IF NOT EXISTS batches(analysis_id TEXT NOT NULL REFERENCES analyses(id), batch_id TEXT NOT NULL, hash TEXT NOT NULL, response TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(analysis_id,batch_id));
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, analysis_id TEXT REFERENCES analyses(id), name TEXT NOT NULL, mime TEXT NOT NULL, relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS platform_auth(platform TEXT PRIMARY KEY, state TEXT NOT NULL,
    verified_at TEXT, checked_at TEXT NOT NULL, job_id TEXT NOT NULL, attempt INTEGER NOT NULL,
    reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_progress(job_id TEXT NOT NULL REFERENCES jobs(id), attempt INTEGER NOT NULL,
    phase TEXT NOT NULL, started TEXT NOT NULL, updated TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    restored INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(job_id,attempt));
CREATE TABLE IF NOT EXISTS job_history_archive(job_id TEXT PRIMARY KEY REFERENCES jobs(id), archived_at TEXT NOT NULL);
"""

SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS platform_sessions(
    platform TEXT PRIMARY KEY, profile TEXT NOT NULL DEFAULT '', browser TEXT NOT NULL DEFAULT '',
    generation INTEGER NOT NULL DEFAULT 0, account_id TEXT, account_name TEXT,
    saved_at TEXT, save_state TEXT NOT NULL DEFAULT 'not_saved',
    check_state TEXT NOT NULL DEFAULT 'not_checked', checked_at TEXT,
    snapshot_valid INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS auth_pauses(
    job_id TEXT PRIMARY KEY REFERENCES jobs(id), platform TEXT NOT NULL,
    account_id TEXT, generation INTEGER NOT NULL, resumed_generation INTEGER,
    reason TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
"""


class Database:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "workbench.sqlite3"
        self.lock = threading.RLock()
        self.accept_writes = True
        with self.connect() as con:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version > 4:
                raise RuntimeError(
                    "Database belongs to a newer application. Open that version; existing data was not changed."
                )
            if version in (1, 2, 3):
                self.backup()
            con.executescript(
                "BEGIN IMMEDIATE;\n"
                + SCHEMA
                + AUTH_SCHEMA
                + SESSION_SCHEMA
                + "\nPRAGMA user_version=4;\nCOMMIT;"
            )

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        try:
            yield con
        finally:
            con.close()

    def write(self, key, scope, body, operation):
        key = key or uid()
        fingerprint = digest(body)
        with self.lock, self.connect() as con:
            if not self.accept_writes:
                raise Conflict(
                    "Application is preparing an update; reconnect after restart"
                )
            con.execute("BEGIN IMMEDIATE")
            try:
                old = con.execute(
                    "SELECT * FROM receipts WHERE key=?", (key,)
                ).fetchone()
                if old:
                    if old["scope"] != scope or old["hash"] != fingerprint:
                        raise Conflict(
                            "Idempotency key already belongs to a different request"
                        )
                    result = json.loads(old["response"])
                else:
                    result = operation(con)
                    con.execute(
                        "INSERT INTO receipts VALUES(?,?,?,?,?)",
                        (key, scope, fingerprint, dump(result), now()),
                    )
                con.commit()
                return result
            except BaseException:
                con.rollback()
                raise

    def query(self, sql, args=()):
        with self.connect() as con:
            return [dict(r) for r in con.execute(sql, args)]

    def one(self, table, identity):
        if table not in {"jobs", "datasets", "analyses", "artifacts"}:
            raise ValueError(table)
        rows = self.query(f"SELECT * FROM {table} WHERE id=?", (identity,))
        if not rows:
            raise Missing(identity)
        return rows[0]

    def execute(self, sql, args=()):
        with self.lock, self.connect() as con:
            con.execute(sql, args)
            con.commit()

    def event(self, job_id, message, level="info"):
        self.execute(
            "INSERT INTO events(job_id,created,level,message) VALUES(?,?,?,?)",
            (job_id, now(), level, message),
        )

    def backup(self):
        directory = self.root / "backups"
        directory.mkdir(exist_ok=True)
        target = directory / f"workbench-{uid()}.sqlite3"
        with (
            self.lock,
            self.connect() as source,
            sqlite3.connect(target) as destination,
        ):
            source.backup(destination)
        return target


def insert_records(con, dataset_id, records, *, platform="import", kind="record"):
    """Preserve raw versions, update normalized current rows, and keep record IDs stable."""
    if not con.execute("SELECT 1 FROM datasets WHERE id=?", (dataset_id,)).fetchone():
        raise Missing(dataset_id)
    ordinal = con.execute(
        "SELECT COALESCE(MAX(ordinal),0) FROM records WHERE dataset_id=?", (dataset_id,)
    ).fetchone()[0]
    inserted = updated = unchanged = 0
    ids = []
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("Every record must be a JSON object")
        payload = item.get("payload", item)
        if not isinstance(payload, dict):
            raise ValueError("Record payload must be a JSON object")
        item_platform = str(item.get("platform", platform))
        item_kind = str(item.get("kind", kind))
        native_id = item.get("source_id") or next(
            (
                payload.get(k)
                for k in (
                    "comment_id",
                    "rpid",
                    "cid",
                    "note_id",
                    "aweme_id",
                    "video_id",
                    "id",
                    "user_id",
                )
                if payload.get(k) is not None
            ),
            None,
        )
        revision = digest(payload)
        source_key = dump(
            [
                item_platform,
                item_kind,
                str(native_id) if native_id is not None else revision,
            ]
        )
        record_id = hashlib.sha256((dataset_id + source_key).encode()).hexdigest()[:32]
        source_url = item.get("source_url") or next(
            (
                payload.get(k)
                for k in ("note_url", "video_url", "content_url", "url")
                if isinstance(payload.get(k), str)
            ),
            "",
        )
        timestamp = now()
        previous = con.execute(
            "SELECT revision FROM records WHERE id=?", (record_id,)
        ).fetchone()
        if previous:
            if previous[0] == revision:
                unchanged += 1
            else:
                updated += 1
                con.execute(
                    "UPDATE records SET payload=?,revision=?,observed=?,source_url=? WHERE id=?",
                    (dump(payload), revision, timestamp, source_url, record_id),
                )
        else:
            inserted += 1
            ordinal += 1
            con.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    record_id,
                    dataset_id,
                    ordinal,
                    source_key,
                    item_platform,
                    item_kind,
                    str(source_url),
                    dump(payload),
                    revision,
                    timestamp,
                ),
            )
        con.execute(
            "INSERT OR IGNORE INTO raw_versions VALUES(?,?,?,?)",
            (record_id, revision, dump(payload), timestamp),
        )
        ids.append(record_id)
    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "record_ids": ids,
    }


def record_view(row):
    return {
        "id": row["id"],
        "ordinal": row["ordinal"],
        "platform": row["platform"],
        "kind": row["kind"],
        "source_url": row["source_url"],
        "revision": row["revision"],
        "payload": json.loads(row["payload"]),
    }
