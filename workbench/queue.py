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
from tools import crawl_status
from . import recovery

TERMINAL = {"completed", "partial", "failed", "cancelled", "interrupted", "needs_login", "waiting_access", "waiting_retry"}
CLAIM_ORDER = "(json_extract(options,'$.crawler_type')='login') DESC,COALESCE((SELECT MAX(a.started) FROM job_attempts a WHERE a.job_id=jobs.id),created),created,id"
PLATFORMS = {
    "xhs": "小红书",
    "dy": "抖音",
    "ks": "快手",
    "bili": "哔哩哔哩",
    "wb": "微博",
    "tieba": "贴吧",
    "zhihu": "知乎",
}


def job_view(row, db=None):
    result = dict(row)
    result["options"] = json.loads(result["options"])
    result["options"].pop("enable_media", None)
    result["options"]["has_cookie"] = bool(result["options"].pop("cookies", ""))
    result["checkpoint"] = json.loads(result["checkpoint"])
    if db is not None:
        result['status_detail'] = recovery.Recovery(db).explain(result)
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
                "operation_id": recovery.session_operation(con, existing['id']),
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
    result = {"id": identity, "state": "queued", "dataset_id": dataset_id}
    if options.get('crawler_type')=='search' and options.get('retry_threshold') is not None:
        from .retrying import configure
        configure(con,identity,{'threshold':options['retry_threshold'],'schedule':False})
    if options['crawler_type'] == 'login':
        result['operation_id'] = recovery.session_operation(con, identity)
    return result


def control(con, job_id, action):
    row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise Missing(job_id)
    if action == "stop":
        con.execute('UPDATE job_retry_plans SET enabled=0,next_retry_at=NULL,updated=? WHERE job_id=?',(now(),job_id))
        con.execute("UPDATE auth_pauses SET enabled=0 WHERE job_id=?", (job_id,))
        con.execute("UPDATE access_waits SET enabled=0 WHERE job_id=?",(job_id,))
        con.execute("UPDATE operations SET state=CASE WHEN state='running' THEN 'cancelling' ELSE 'cancelled' END,updated=?,result=? WHERE id IN (SELECT operation_id FROM access_checks WHERE source_job=?) AND state IN ('accepted','running')",
                    (now(),dump({'reason_code':'source_task_stopped','next_actions':['inspect_status'],'attempted':False}),job_id))
        state = (
            "stopping"
            if row["state"] in ("running", "stopping")
            else ("completed" if row["state"] == "completed" else "cancelled")
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
        con.execute('UPDATE job_retry_plans SET enabled=1,next_retry_at=NULL,updated=? WHERE job_id=?',(now(),job_id))
        con.execute("UPDATE auth_pauses SET enabled=0 WHERE job_id=?", (job_id,))
        # Explicit resume does not bypass an unresolved capability block.
        from .availability import Availability
        account = Availability._session(con,row['platform'])['account_id'] or ''
        blocked = con.execute("SELECT 1 FROM access_waits w JOIN access_state s ON s.platform=? AND s.account_id=w.account_id AND s.capability=w.capability WHERE w.job_id=? AND w.account_id=? AND s.state IN ('restricted','suspected_restriction','manual_action')",(row['platform'],job_id,account)).fetchone()
        if blocked and row['platform']=='dy':
            raise Conflict('对应平台能力尚未恢复，请查看状态并等待复查；重新登录不会清除冷却。')
        con.execute('UPDATE access_waits SET enabled=0 WHERE job_id=?',(job_id,))
        con.execute('DELETE FROM access_replays WHERE job_id=?',(job_id,))
        con.execute('UPDATE job_retry_plans SET account_id=? WHERE job_id=?',(account or None,job_id))
        options = json.loads(row["options"])
        options.pop("enable_media", None)
        con.execute("UPDATE jobs SET options=? WHERE id=?", (dump(options), job_id))
        con.execute("DELETE FROM job_history_archive WHERE job_id=?", (job_id,))
    con.execute(
        "UPDATE jobs SET state=?,updated=?,error=NULL WHERE id=?",
        (state, now(), job_id),
    )
    if state == 'cancelled':
        con.execute("UPDATE operations SET state='cancelled',updated=?,result=? WHERE job_id=? AND state IN ('accepted','running')",
                    (now(), dump({'reason_code':'user_stopped','next_actions':['retry']}),job_id))
    if state == "cancelled" and row["state"] == "queued":
        con.execute(
            "UPDATE login_progress SET phase='cancelled',reason='cancelled',updated=? WHERE job_id=? AND attempt=? AND phase='queued'",
            (now(), job_id, row["attempt"]),
        )
    recovery.event(con,job_id,row['attempt'],'User requested '+action,source='user',kind='control',details={'action':action,'state':state})
    return {"id": job_id, "state": state}


def clear_history(con, ids=None, *, restore=False):
    if restore:
        rows = con.execute("SELECT job_id FROM job_history_archive").fetchall()
        selected = [r[0] for r in rows if ids is None or r[0] in ids]
        con.executemany(
            "DELETE FROM job_history_archive WHERE job_id=?", [(id,) for id in selected]
        )
    else:
        resumable={r[0] for r in con.execute("SELECT j.id FROM jobs j WHERE (j.state IN ('needs_login','waiting_access') AND (EXISTS(SELECT 1 FROM auth_pauses p WHERE p.job_id=j.id AND p.enabled=1) OR EXISTS(SELECT 1 FROM access_waits w WHERE w.job_id=j.id AND w.enabled=1))) OR (j.state='waiting_retry' AND EXISTS(SELECT 1 FROM job_retry_plans p WHERE p.job_id=j.id AND p.enabled=1))")}
        if ids is not None and resumable.intersection(ids):
            raise Conflict('任务仍在等待自动续接，请先停止自动续接再清理记录。')
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
            if r["state"] in TERMINAL and r['id'] not in resumable and (ids is None or r["id"] in ids)
        ]
        con.executemany(
            "INSERT INTO job_history_archive VALUES(?,?)",
            [(id, now()) for id in selected],
        )
    return {"count": len(selected), "ids": selected, "data_preserved": True}


def kill_tree(pid, created=None, timeout=3):
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
        _, alive = psutil.wait_procs(children + [process], timeout=timeout)
        for child in alive:
            try:
                child.kill()
            except psutil.Error:
                pass
    except psutil.Error:
        pass


class Queue:
    def __init__(self, db, worker_command=None, sessions=None, *, availability=None, probe_command=None):
        self.db = db
        self.stop_event = threading.Event()
        self.thread = None
        self.process = None
        self.sessions = sessions
        from .availability import Availability
        self.availability = availability or Availability(db)
        self.probe_command = probe_command
        self.worker_command = worker_command or (
            lambda job_id: [sys.executable, "-m", "workbench.worker", job_id]
        )

    def start(self):
        self.availability.recover()
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
            with self.db.lock,self.db.connect() as con:
                con.execute('BEGIN IMMEDIATE')
                recovery.finish(con,job,'interrupted',json.loads(job['checkpoint']),
                                'Previous host stopped. Saved records are available; resume explicitly.')
                con.commit()
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
            if not self.db.accept_writes:
                return None
            from .retrying import release_due
            release_due(con)
            if con.execute("SELECT 1 FROM operations WHERE kind='probe' AND state IN ('running','cancelling') LIMIT 1").fetchone():
                return None
            row = con.execute(
                "SELECT * FROM jobs WHERE state='queued' ORDER BY "+CLAIM_ORDER+" LIMIT 1"
            ).fetchone()
            while row and json.loads(row['options'])['crawler_type']!='login':
                account=self.availability._session(con,row['platform'])['account_id'] or ''
                blocks=con.execute("SELECT capability,reason FROM access_state WHERE platform=? AND account_id=? AND state IN ('restricted','suspected_restriction','manual_action')",(row['platform'],account)).fetchall()
                if not blocks or row['platform']!='dy':break
                for block in blocks:
                    self.availability.pause(con,dict(row)|{'options':json.loads(row['options'])},block['capability'],'existing_block')
                con.execute("UPDATE jobs SET state='waiting_access',updated=? WHERE id=?",(now(),row['id']))
                row=con.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY "+CLAIM_ORDER+" LIMIT 1").fetchone()
            if row:
                options = json.loads(row["options"])
                options.pop("enable_media", None)
                con.execute(
                    "UPDATE jobs SET state='running',attempt=attempt+1,options=?,updated=? WHERE id=?",
                    (dump(options), now(), row["id"]),
                )
                con.execute("INSERT INTO job_attempts(job_id,attempt,state,started) VALUES(?,?,'running',?)",
                            (row['id'], row['attempt'] + 1, now()))
                recovery.plan_scopes(con,row,row['attempt']+1)
                con.execute("UPDATE operations SET state='running',attempt=?,updated=? WHERE job_id=? AND state='accepted'",
                            (row['attempt']+1,now(),row['id']))
            con.commit()
            if row:
                result = job_view(row)
                result["attempt"] += 1
                auth_update(self.db, row["id"], result["attempt"], "starting_browser")
                return result
            return None

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.availability.schedule_due()
                operation = self.availability.claim()
            except Exception:
                import logging
                logging.getLogger(__name__).error('Scheduler storage unavailable; will retry')
                self.stop_event.wait(1)
                continue
            if operation:
                from .probe_runner import run_probe
                try:
                    result = run_probe(self.db,operation,self.stop_event,self.sessions,self.probe_command)
                except Exception:
                    result={'attempted':False,'verdict':'unconfirmed','reason':'worker_failed','cleanup_complete':False}
                # Retain the finished result and retry only its transaction. A
                # temporary storage fault must not repeat platform requests or
                # leave an unclaimable running operation until host restart.
                while not self.stop_event.is_set():
                    try:
                        self.availability.complete(operation,result)
                        break
                    except Exception:
                        try:
                            self.db.execute("UPDATE operations SET updated=?,result=? WHERE id=? AND state='running'",(now(),dump({'reason_code':'storage_error','next_actions':['wait','retry_later'],'commit_pending':True}),operation['id']))
                        except Exception:
                            pass
                        self.stop_event.wait(.5)
                if self.stop_event.is_set():
                    import logging
                    logging.getLogger(__name__).error('Probe result commit interrupted; restart recovery retains its operation')
                continue
            try:
                job = self._claim()
            except Exception:
                self.stop_event.wait(1)
                continue
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
                with self.db.lock, self.db.connect() as con:
                    con.execute('BEGIN IMMEDIATE')
                    recovery.finish(con, job, state, json.loads(current['checkpoint']), str(exc))
                    con.commit()
                self.db.event(job["id"], str(exc), "error", attempt=job['attempt'], source='browser')
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
            "MEDIAWORKBENCH_LOGIN_DIR": str(directory),
        }
        if job.get("_session"):
            env.update(
                MEDIAWORKBENCH_SESSION=dump(job["_session"]),
                MEDIAWORKBENCH_SERVICE_URL=self.sessions.url,
                MEDIAWORKBENCH_WORKER_TOKEN=self.sessions.token,
            )
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        attempt_dir=directory/'attempts'/str(job['attempt'])
        attempt_dir.mkdir(parents=True,exist_ok=True)
        with (attempt_dir / "worker.log").open("a", encoding="utf-8") as log:
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
            unconfirmed = []

            def read_output():
                for line in process.stdout:
                    from .redaction import sanitize
                    line = sanitize(line)
                    if not line:
                        continue
                    log.write(line + "\n")
                    log.flush()
                    level = (
                        "error"
                        if re.search(r"\bERROR\b|Traceback \(most recent|^[\w.]+(?:Error|Exception|Conflict):", line)
                        else "info"
                    )
                    if level == "error":
                        errors.append(line)
                    if "[DouYinCrawler.search]" in line:
                        if re.search(r"page:\s*\d+ is empty", line):
                            unconfirmed.append("搜索返回空列表，尚未确认结果；可查看详情后重试。")
                        elif "Repeated page for keyword:" in line:
                            unconfirmed.append("搜索分页重复，已停止翻页以避免循环；采集尚未确认完整。")
                    source='local_app' if 'InternalAuthorizationError:' in line else 'storage' if re.search(r'\bsqlite3\.(?:Operational|Integrity|Database)Error|\bPermissionError:',line) else 'browser' if 'TargetClosedError' in line else 'worker'
                    self.db.event(job["id"], line, level, attempt=job['attempt'],source=source)

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
        collection = crawl_status.read(directory, job["id"], job["attempt"])
        if cancelled:
            state = "cancelled"
        elif self.stop_event.is_set():
            state = "interrupted"
        elif code != 0:
            state = "partial" if count else "failed"
        elif collection and collection["issues"] and job["options"]["crawler_type"] != "login":
            state = "partial" if count else "interrupted"
            reason = collection["message"]
        elif not collection and unconfirmed and job["options"]["crawler_type"] != "login":
            state, reason = ("partial" if count else "interrupted"), unconfirmed[-1]
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
                            job["id"], job["platform"], progress[0]["reason"],attempt=job['attempt']
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
        if collection:
            checkpoint["collection"] = collection
        with self.db.lock, self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current = con.execute('SELECT state,attempt FROM jobs WHERE id=?',(job['id'],)).fetchone()
            if (current['state']=='running' and current['attempt']==job['attempt'] and state not in ('cancelled','needs_login')
                    and collection and job['platform']=='dy' and job['options']['crawler_type']!='login'):
                from .availability import AMBIGUOUS_REASONS
                for issue in collection['issues']:
                    if issue['stage'] in ('search','detail','creator','comments','replies','profile') and issue['reason'] in AMBIGUOUS_REASONS | {'restriction_explicit'}:
                        self.availability.pause(con,job,issue['stage'],issue['reason'],issue.get('response',{}).get('retry_after',0))
                        state='waiting_access'
            recovery.finish(con, job, state, checkpoint, reason)
            con.commit()


def collect_record(item, kind, platform, source_id=None, source_url=""):
    job_id = os.environ.get("MEDIAWORKBENCH_JOB_ID")
    if not job_id:
        return
    from .database import worker_database
    db = worker_database()
    with db.lock, db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        job = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        attempt = os.environ.get('MEDIAWORKBENCH_ATTEMPT', '')
        lease = json.loads(os.environ.get('MEDIAWORKBENCH_SESSION', '{}'))
        from .ownership import worker_job
        try:
            job=worker_job(con,job_id,attempt,lease,platform)
        except Conflict:
            if job:
                recovery.event(con, job_id, int(attempt) if attempt.isdigit() else None,
                               'Late or stopped worker write rejected', source='storage', kind='stale_write')
                con.commit()
            raise Conflict('This task attempt no longer owns record writes')
        saved = insert_records(
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
        con.execute('INSERT INTO attempt_metrics VALUES(?,?,1,?,?,?) ON CONFLICT(job_id,attempt) DO UPDATE SET received=received+1,inserted=inserted+excluded.inserted,updated=updated+excluded.updated,unchanged=unchanged+excluded.unchanged',
                    (job_id,int(attempt),saved['inserted'],saved['updated'],saved['unchanged']))
        recovery.event(con,job_id,int(attempt),'Record committed',source='storage',kind='record_committed',
                       capability='comments' if 'comment' in kind else 'detail',details=saved)
        record_id = saved['record_ids'][0]
        recovery.discover_followups(con,job,int(attempt),item,kind)
        from .platform_observation import current_scope
        active_scope=current_scope.get()
        attributed=set([active_scope] if active_scope else [])
        # Gathered requests run in child contexts. Match their stable source ID
        # at commit time so the parent's ContextVar cannot erase detail counts.
        content_id=next((str(item[k]) for k in ('aweme_id','note_id','video_id','bvid','content_id') if item.get(k)),None)
        if content_id:
            capability='comments' if 'comment' in kind else 'detail'
            if con.execute('SELECT 1 FROM collection_scopes WHERE job_id=? AND attempt=? AND capability=? AND scope=?',(job_id,int(attempt),capability,content_id)).fetchone():
                attributed.add((capability,content_id))
        for capability,scope in attributed:
            con.execute('INSERT INTO scope_records VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING',(job_id,int(attempt),capability,scope,record_id))
            con.execute('INSERT INTO scope_metrics VALUES(?,?,?,?,1,?,?,?) ON CONFLICT(job_id,attempt,capability,scope) DO UPDATE SET received=received+1,inserted=inserted+excluded.inserted,updated=updated+excluded.updated,unchanged=unchanged+excluded.unchanged',
                        (job_id,int(attempt),capability,scope,saved['inserted'],saved['updated'],saved['unchanged']))
            options=json.loads(job['options'])
            limit=options['max_comments_count'] if capability in ('comments','replies') else options['max_notes_count'] if capability in ('search','creator') else 0
            count=con.execute('SELECT count(*) FROM scope_records WHERE job_id=? AND attempt=? AND capability=? AND scope=?',(job_id,int(attempt),capability,scope)).fetchone()[0]
            if limit and count>=limit:
                recovery.scope_fact(con,job_id,int(attempt),capability,scope,'limit_reached',{'committed_records':count})
        revision = con.execute('SELECT revision FROM records WHERE id=?',(record_id,)).fetchone()[0]
        con.execute("INSERT INTO raw_commits VALUES(?,?,?,?,'pending',?) ON CONFLICT DO NOTHING",
                    (job_id,int(attempt),record_id,revision,now()))
        con.commit()
        return {'record_id':record_id,'revision':revision,'job_id':job_id,'attempt':int(attempt)}
