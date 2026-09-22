"""One persisted queue shared by desktop and agents; workers survive UI disconnects."""

from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import threading
import time
import psutil
from .database import Conflict, Missing, dump, uid, now, insert_records
from .paths import ROOT
from .auth import active_login, update as auth_update, finish_progress

TERMINAL = {"completed", "partial", "failed", "cancelled", "interrupted", "needs_login"}
PLATFORMS = {
    "xhs": "小红书",
    "dy": "抖音",
    "ks": "快手",
    "bili": "哔哩哔哩",
    "wb": "微博",
    "tieba": "贴吧",
    "zhihu": "知乎",
}


def job_view(row):
    result = dict(row)
    result["options"] = json.loads(result["options"])
    result["options"].pop("enable_media", None)
    result["options"]["has_cookie"] = bool(result["options"].pop("cookies", ""))
    result["checkpoint"] = json.loads(result["checkpoint"])
    return result


def create_job(con, options):
    options = dict(options)
    options.pop("enable_media", None)
    if options["crawler_type"] == "login":
        from .models import CrawlRequest

        options = CrawlRequest(**options).model_dump()
        existing = active_login(con, options["platform"])
        if existing:
            return {
                "id": existing["id"],
                "state": existing["state"],
                "dataset_id": existing["dataset_id"],
                "reused": True,
            }
    if options.get("cookies"):
        from .credentials import seal

        options["cookies"] = seal(options["cookies"])
    identity, dataset_id, stamp = uid(), uid(), now()
    if options["crawler_type"] == "login":
        dataset_id = None
    else:
        con.execute(
            "INSERT INTO datasets VALUES(?,?,?,?)",
            (
                dataset_id,
                f"{PLATFORMS[options['platform']]} · {options.get('keywords') or options['crawler_type']}",
                stamp,
                dump({"job_id": identity}),
            ),
        )
    con.execute(
        "INSERT INTO jobs(id,platform,state,options,dataset_id,created,updated) VALUES(?,?,?,?,?,?,?)",
        (
            identity,
            options["platform"],
            "queued",
            dump(options),
            dataset_id,
            stamp,
            stamp,
        ),
    )
    if options["crawler_type"] == "login":
        con.execute(
            "INSERT INTO login_progress VALUES(?,0,'queued',?,?,'',0)",
            (identity, stamp, stamp),
        )
    return {"id": identity, "state": "queued", "dataset_id": dataset_id}


def control(con, job_id, action):
    row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise Missing(job_id)
    if action == "stop":
        con.execute("UPDATE auth_pauses SET enabled=0 WHERE job_id=?", (job_id,))
        state = (
            "stopping"
            if row["state"] in ("running", "stopping")
            else ("cancelled" if row["state"] == "queued" else row["state"])
        )
    else:
        if json.loads(row["options"])["crawler_type"] == "login":
            existing = active_login(con, row["platform"], job_id)
            if existing:
                return {
                    "id": existing["id"],
                    "state": existing["state"],
                    "reused": True,
                }
        if row["state"] not in TERMINAL - {"completed"}:
            raise Conflict(
                "Only an interrupted, stopped, partial, or failed task can resume"
            )
        state = "queued"
        con.execute("UPDATE auth_pauses SET enabled=0 WHERE job_id=?", (job_id,))
        options = json.loads(row["options"])
        options.pop("enable_media", None)
        con.execute("UPDATE jobs SET options=? WHERE id=?", (dump(options), job_id))
        con.execute("DELETE FROM job_history_archive WHERE job_id=?", (job_id,))
    con.execute(
        "UPDATE jobs SET state=?,updated=?,error=NULL WHERE id=?",
        (state, now(), job_id),
    )
    if state == "cancelled" and row["state"] == "queued":
        con.execute(
            "UPDATE login_progress SET phase='cancelled',reason='cancelled',updated=? WHERE job_id=? AND attempt=? AND phase='queued'",
            (now(), job_id, row["attempt"]),
        )
    return {"id": job_id, "state": state}


def clear_history(con, ids=None, *, restore=False):
    if restore:
        rows = con.execute("SELECT job_id FROM job_history_archive").fetchall()
        selected = [r[0] for r in rows if ids is None or r[0] in ids]
        con.executemany(
            "DELETE FROM job_history_archive WHERE job_id=?", [(id,) for id in selected]
        )
    else:
        rows = con.execute(
            "SELECT id,state FROM jobs WHERE id NOT IN (SELECT job_id FROM job_history_archive)"
        ).fetchall()
        if ids is not None:
            # Reject an explicit active selection atomically; bulk cleanup skips active jobs.
            requested = con.execute("SELECT id,state FROM jobs").fetchall()
            if set(ids) - {r["id"] for r in requested}:
                raise Missing(next(iter(set(ids) - {r["id"] for r in requested})))
            if any(r["id"] in ids and r["state"] not in TERMINAL for r in requested):
                raise Conflict("正在运行或排队的任务不能清理，请先停止任务")
        selected = [
            r["id"]
            for r in rows
            if r["state"] in TERMINAL and (ids is None or r["id"] in ids)
        ]
        con.executemany(
            "INSERT INTO job_history_archive VALUES(?,?)",
            [(id, now()) for id in selected],
        )
    return {"count": len(selected), "ids": selected, "data_preserved": True}


def kill_tree(pid, created=None):
    try:
        process = psutil.Process(pid)
        if created is not None and abs(process.create_time() - created) > 0.1:
            return
        children = process.children(recursive=True)
        for child in reversed(children):
            try:
                child.terminate()
            except psutil.Error:
                pass
        process.terminate()
        _, alive = psutil.wait_procs(children + [process], timeout=3)
        for child in alive:
            try:
                child.kill()
            except psutil.Error:
                pass
    except psutil.Error:
        pass


class Queue:
    def __init__(self, db, worker_command=None, sessions=None):
        self.db = db
        self.stop_event = threading.Event()
        self.thread = None
        self.process = None
        self.sessions = sessions
        self.worker_command = worker_command or (
            lambda job_id: [sys.executable, "-m", "workbench.worker", job_id]
        )

    def start(self):
        # A worker from a crashed host must be reaped before the next browser session.
        for job in self.db.query(
            "SELECT * FROM jobs WHERE state IN ('running','stopping')"
        ):
            if job["pid"]:
                try:
                    process = psutil.Process(job["pid"])
                    if "workbench.worker" in process.cmdline():
                        kill_tree(job["pid"], job["process_created"])
                except psutil.Error:
                    pass
            self.db.execute(
                "UPDATE jobs SET state='interrupted',pid=NULL,error=?,updated=? WHERE id=?",
                (
                    "Previous host stopped. Saved records are available; resume explicitly.",
                    now(),
                    job["id"],
                ),
            )
            finish_progress(self.db, job["id"], "interrupted")
        self.thread = threading.Thread(
            target=self._loop, name="workbench-queue", daemon=True
        )
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=25)
        if self.process and self.process.poll() is None:
            kill_tree(self.process.pid)
        if self.sessions:
            self.sessions.close()

    def _claim(self):
        with self.db.lock, self.db.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM jobs WHERE state='queued' ORDER BY (json_extract(options,'$.crawler_type')='login') DESC,created,id LIMIT 1"
            ).fetchone()
            if row:
                options = json.loads(row["options"])
                options.pop("enable_media", None)
                con.execute(
                    "UPDATE jobs SET state='running',attempt=attempt+1,options=?,updated=? WHERE id=?",
                    (dump(options), now(), row["id"]),
                )
            con.commit()
            if row:
                result = job_view(row)
                result["attempt"] += 1
                auth_update(self.db, row["id"], result["attempt"], "starting_browser")
                return result
            return None

    def _loop(self):
        while not self.stop_event.is_set():
            job = self._claim()
            if not job:
                if self.sessions and self.sessions.thread:
                    try:
                        self.sessions.call("idle")
                    except Exception:
                        # A failed idle close must not kill the durable queue.
                        import logging

                        logging.getLogger(__name__).exception(
                            "Browser idle cleanup failed"
                        )
                self.stop_event.wait(0.3)
                continue
            try:
                self._run(job)
            except Exception as exc:
                current = self.db.one("jobs", job["id"])
                state = (
                    "cancelled"
                    if current["state"] == "stopping"
                    else "interrupted"
                    if self.stop_event.is_set()
                    else "failed"
                )
                self.db.execute(
                    "UPDATE jobs SET state=?,error=?,pid=NULL,updated=? WHERE id=?",
                    (state, str(exc), now(), job["id"]),
                )
                self.db.event(job["id"], str(exc), "error")
                finish_progress(
                    self.db,
                    job["id"],
                    state,
                    "startup_failed" if state == "failed" else state,
                )

    def _run(self, job):
        lease = None
        try:
            if self.sessions:
                lease = self.sessions.call("acquire", job=job)
                job["_session"] = lease
            return self._execute(job)
        finally:
            if lease:
                try:
                    self.sessions.call("release", lease=lease["lease"])
                except Exception:
                    self.sessions.call("shutdown")

    def _execute(self, job):
        directory = self.db.root / "runs" / job["id"]
        directory.mkdir(parents=True, exist_ok=True)
        stop_file = directory / ".stop"
        stop_file.unlink(missing_ok=True)
        (directory / ".login_qrcode.png").unlink(missing_ok=True)
        env = {
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(ROOT),
            "MEDIAWORKBENCH_DATA_DIR": str(self.db.root),
            "MEDIAWORKBENCH_ATTEMPT": str(job["attempt"]),
        }
        if job.get("_session"):
            env.update(
                MEDIAWORKBENCH_SESSION=dump(job["_session"]),
                MEDIAWORKBENCH_SERVICE_URL=self.sessions.url,
                MEDIAWORKBENCH_WORKER_TOKEN=self.sessions.token,
            )
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with (directory / "worker.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                self.worker_command(job["id"]),
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
            self.process = process
            self.db.execute(
                "UPDATE jobs SET pid=?,process_created=? WHERE id=?",
                (process.pid, psutil.Process(process.pid).create_time(), job["id"]),
            )
            errors = []

            def read_output():
                for line in process.stdout:
                    line = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
                    line = re.sub(
                        r"(?i)(cookie|authorization)(\s*[:=]\s*)[^\r\n]+",
                        r"\1\2[redacted]",
                        line,
                    )
                    if not line:
                        continue
                    log.write(line + "\n")
                    log.flush()
                    level = (
                        "error"
                        if re.search(r"\bERROR\b|Traceback \(most recent", line)
                        else "info"
                    )
                    if level == "error":
                        errors.append(line)
                    self.db.event(job["id"], line, level)

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            stopped_at = None
            cancelled = False
            startup_deadline = time.monotonic() + 120
            while process.poll() is None:
                state = self.db.one("jobs", job["id"])["state"]
                if state == "stopping" or self.stop_event.is_set():
                    cancelled = state == "stopping"
                    if stopped_at is None:
                        stop_file.touch()
                        stopped_at = time.monotonic()
                    elif time.monotonic() - stopped_at > 18:
                        kill_tree(process.pid)
                if stopped_at is None and time.monotonic() > startup_deadline:
                    progress = self.db.query(
                        "SELECT phase FROM login_progress WHERE job_id=? AND attempt=?",
                        (job["id"], job["attempt"]),
                    )
                    if progress and progress[0]["phase"] == "starting_browser":
                        auth_update(
                            self.db,
                            job["id"],
                            job["attempt"],
                            "failed",
                            state="unknown",
                            reason="startup_timeout",
                        )
                        kill_tree(process.pid)
                time.sleep(0.2)
            reader.join(timeout=4)
            code = process.wait()
        self.process = None
        count = self.db.query(
            "SELECT COUNT(*) n FROM records WHERE dataset_id=?", (job["dataset_id"],)
        )[0]["n"]
        reason = (
            errors[-1]
            if errors
            else (f"Worker exited with code {code}" if code else None)
        )
        if cancelled:
            state = "cancelled"
        elif self.stop_event.is_set():
            state = "interrupted"
        elif code != 0:
            state = "partial" if count else "failed"
        elif errors and job["options"]["crawler_type"] != "login":
            state = "partial" if count else "failed"
        else:
            state = "completed"
        progress = self.db.query(
            "SELECT phase,reason FROM login_progress WHERE job_id=? AND attempt=?",
            (job["id"], job["attempt"]),
        )
        if job["options"]["crawler_type"] == "login" and state == "completed":
            if not progress or progress[0]["phase"] != "authenticated":
                state, reason = "failed", "Login verification did not complete"
            else:
                reason = None
        if progress and progress[0]["phase"] == "failed":
            from .auth import MESSAGES

            reason = MESSAGES.get(progress[0]["reason"], reason)
            if state not in ("cancelled", "interrupted"):
                if progress[0]["reason"] in (
                    "logged_out",
                    "challenge",
                    "scan_timeout",
                    "account_changed",
                ):
                    state = "needs_login"
                    from .session_store import SessionStore

                    if job["options"]["crawler_type"] != "login":
                        SessionStore(self.db).pause(
                            job["id"], job["platform"], progress[0]["reason"]
                        )
                elif progress[0]["reason"] in ("network_error", "unconfirmed"):
                    state = "interrupted"
        finish_progress(
            self.db,
            job["id"],
            state,
            "startup_failed"
            if progress
            and progress[0]["phase"] == "starting_browser"
            and state not in ("cancelled", "interrupted")
            else "",
        )
        if (directory / "xhs_resume_required.json").exists() or (
            directory / "profile_resume_required.json"
        ).exists():
            if state not in ("cancelled", "interrupted"):
                state = "needs_login"
        checkpoint = {
            "saved_records": count,
            "resume_strategy": "replay_with_dedup",
            "raw_directory": str(directory),
            "exit_code": code,
        }
        self.db.execute(
            "UPDATE jobs SET state=?,pid=NULL,error=?,checkpoint=?,updated=? WHERE id=?",
            (state, reason, dump(checkpoint), now(), job["id"]),
        )
        self.db.event(
            job["id"],
            f"Task {state}; {count} distinct records saved.",
            "error" if state == "failed" else "info",
        )


_collector_databases = {}


def collect_record(item, kind, platform, source_id=None, source_url=""):
    job_id = os.environ.get("MEDIAWORKBENCH_JOB_ID")
    if not job_id:
        return
    from .database import Database
    from .paths import data_root

    root = data_root()
    if str(root) not in _collector_databases:
        _collector_databases[str(root)] = Database(root)
    db = _collector_databases[str(root)]
    job = db.one("jobs", job_id)
    with db.lock, db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        insert_records(
            con,
            job["dataset_id"],
            [
                {
                    "payload": item,
                    "platform": platform,
                    "kind": kind,
                    "source_id": source_id,
                    "source_url": source_url,
                }
            ],
            platform=platform,
            kind=kind,
        )
        con.commit()
