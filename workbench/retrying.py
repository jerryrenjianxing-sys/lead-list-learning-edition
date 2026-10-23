"""Persist retry intent separately from user stops and per-attempt outcomes."""
from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from sqlite3 import Connection
from typing import Literal, Mapping, TypedDict

from .database import Conflict, Database, Missing, now


BlockedReason = Literal['', 'account_changed', 'authentication_required', 'manual_action', 'access_recheck']


class RetryPolicy(TypedDict, total=False):
    enabled: bool
    threshold: int
    min_seconds: int
    max_seconds: int
    next_retry_at: float | None
    schedule: bool


class RetrySaved(TypedDict):
    id: str
    state: str
    next_retry_at: str | None


class RetryView(TypedDict):
    job_id: str
    enabled: int
    threshold: int
    min_seconds: int
    max_seconds: int
    next_retry_at: str | None
    delay_seconds: int | None
    reason: str
    updated: str
    record_count: int
    blocked_reason: BlockedReason


class RetryGate(TypedDict):
    blocked_reason: BlockedReason
    next_retry_at: float | None


def iso(epoch: float | None) -> str | None:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch is not None else None


def choose(plan: Mapping) -> int:
    return random.SystemRandom().randint(plan['min_seconds'], plan['max_seconds'])


def account(con: Connection, platform: str) -> str | None:
    row=con.execute('SELECT account_id FROM platform_sessions WHERE platform=?',(platform,)).fetchone()
    return row['account_id'] if row else None


def record_count(con: Connection, dataset_id: str) -> int:
    """Only research content and discussion count toward the run-through threshold."""
    return con.execute("SELECT count(*) FROM records WHERE dataset_id=? AND kind IN ('contents','comments','replies')", (dataset_id,)).fetchone()[0]


def retry_gate(con: Connection, job: Mapping, plan: Mapping) -> RetryGate:
    """Use the same authentication/access decision for scheduling and display."""
    session = con.execute('SELECT account_id,check_state FROM platform_sessions WHERE platform=?', (job['platform'],)).fetchone()
    current_account = session['account_id'] if session else None
    reason: BlockedReason = ''
    deadline = plan['next_retry_at']
    if plan['account_id'] and plan['account_id'] != current_account:
        reason = 'account_changed'
    elif not session or not current_account or session['check_state'] != 'authenticated':
        reason = 'authentication_required'
    elif (job['error'] or '').startswith('对照读取成功，但原目标重试后仍异常'):
        reason = 'manual_action'
    elif job['platform'] == 'dy':
        blocks = con.execute("SELECT state,cooldown_until FROM access_state WHERE platform=? AND account_id=? AND state IN ('restricted','suspected_restriction','manual_action')", (job['platform'], current_account)).fetchall()
        if any(block['state'] == 'manual_action' for block in blocks):
            reason = 'manual_action'
        elif blocks:
            reason = 'access_recheck'
            if deadline is not None:
                deadline = max([deadline] + [b['cooldown_until'] for b in blocks if b['cooldown_until'] is not None])
    return {'blocked_reason': reason, 'next_retry_at': None if reason in ('account_changed', 'authentication_required', 'manual_action') else deadline}


def configure(con: Connection, job_id: str, body: RetryPolicy) -> RetrySaved:
    from .recovery import event
    job=con.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not job:raise Missing(job_id)
    if json.loads(job['options'])['crawler_type']!='search':
        raise Conflict('Automatic low-volume retry is available for search tasks only')
    enabled=bool(body.get('enabled',True));stamp=now()
    previous=con.execute('SELECT * FROM job_retry_plans WHERE job_id=?',(job_id,)).fetchone()
    plan={'threshold':body.get('threshold',100),'min_seconds':body.get('min_seconds',5400),
          'max_seconds':body.get('max_seconds',7200)}
    schedule=enabled and body.get('schedule',True)
    explicit_deadline=body.get('next_retry_at')
    delay=choose(plan) if schedule and explicit_deadline is None else None
    deadline=(explicit_deadline or time.time()+delay) if schedule else None
    reason='scheduled_retry' if schedule else ''
    if enabled and not schedule and previous and job['state']=='waiting_retry':
        deadline=previous['next_retry_at'];delay=previous['delay_seconds'];reason=previous['reason']
    running=job['state'] in ('running','stopping')
    # An executing worker must release its lease before its waiting period starts.
    if running and schedule and explicit_deadline is None:deadline=None
    con.execute('INSERT INTO job_retry_plans VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET enabled=excluded.enabled,threshold=excluded.threshold,min_seconds=excluded.min_seconds,max_seconds=excluded.max_seconds,next_retry_at=excluded.next_retry_at,delay_seconds=excluded.delay_seconds,reason=excluded.reason,account_id=excluded.account_id,updated=excluded.updated',
                (job_id,int(enabled),plan['threshold'],plan['min_seconds'],plan['max_seconds'],deadline,delay,
                 reason,account(con,job['platform']),stamp))
    if schedule:
        state='stopping' if running else 'waiting_retry'
        con.execute('UPDATE jobs SET state=?,updated=?,error=NULL WHERE id=?',(state,stamp,job_id))
        con.execute('DELETE FROM job_history_archive WHERE job_id=?',(job_id,))
    elif not enabled and job['state']=='waiting_retry':
        con.execute("UPDATE jobs SET state='cancelled',updated=? WHERE id=?",(stamp,job_id))
    event(con,job_id,job['attempt'],'Retry plan saved',source='queue',kind='retry_plan',
          details={'enabled':enabled,**plan,'next_retry_at':iso(deadline),'delay_seconds':delay})
    actual=con.execute('SELECT state FROM jobs WHERE id=?',(job_id,)).fetchone()['state']
    return {'id':job_id,'state':actual,
            'next_retry_at':iso(deadline)}


def finish_state(con: Connection, job: Mapping, state: str) -> str:
    """Run after ownership/stop fencing, in the same transaction as finalization."""
    from .recovery import event
    plan=con.execute('SELECT * FROM job_retry_plans WHERE job_id=? AND enabled=1',(job['id'],)).fetchone()
    if not plan or state in ('needs_login','cancelled'):
        return state
    total=record_count(con,job['dataset_id'])
    low=total<=plan['threshold']
    incomplete=state in ('partial','failed','interrupted','waiting_access','retry_requested')
    if not low and not incomplete:
        con.execute('UPDATE job_retry_plans SET next_retry_at=NULL,reason=?,updated=? WHERE job_id=?',('',now(),job['id']))
        return state
    if state == 'retry_requested' and plan['next_retry_at'] is not None:
        deadline, delay = plan['next_retry_at'], plan['delay_seconds']
    else:
        delay=plan['delay_seconds'] if state=='retry_requested' and plan['delay_seconds'] else choose(plan)
        deadline=time.time()+delay
    reason='insufficient_records' if low else 'collection_incomplete'
    con.execute('UPDATE job_retry_plans SET next_retry_at=?,delay_seconds=?,reason=?,account_id=COALESCE(account_id,?),updated=? WHERE job_id=?',
                (deadline,delay,reason,account(con,job['platform']),now(),job['id']))
    event(con,job['id'],job['attempt'],'Retry scheduled',source='queue',kind='retry_scheduled',
          details={'reason':reason,'dataset_total':total,'threshold':plan['threshold'],
                   'next_retry_at':iso(deadline),'delay_seconds':delay,'attempt_outcome':state})
    return 'waiting_retry'


def release_due(con: Connection) -> None:
    """Claim a saved timer without clearing authentication or capability blocks."""
    from .recovery import event
    epoch=time.time()
    rows=con.execute("SELECT j.* FROM jobs j JOIN job_retry_plans p ON p.job_id=j.id WHERE j.state='waiting_retry' AND p.enabled=1 AND p.next_retry_at<=? ORDER BY p.next_retry_at,j.created,j.id",(epoch,)).fetchall()
    for job in rows:
        plan=con.execute('SELECT * FROM job_retry_plans WHERE job_id=?',(job['id'],)).fetchone()
        if retry_gate(con,job,plan)['blocked_reason']:continue
        # Access recovery owns the once-only replay receipt, including when a
        # successful control preceded this timer. Never queue around that seam.
        if con.execute('SELECT 1 FROM access_waits WHERE job_id=? AND enabled=1',(job['id'],)).fetchone():continue
        changed=con.execute("UPDATE jobs SET state='queued',error=NULL,updated=? WHERE id=? AND state='waiting_retry'",(now(),job['id'])).rowcount
        if changed:
            con.execute('UPDATE job_retry_plans SET next_retry_at=NULL,updated=? WHERE job_id=?',(now(),job['id']))
            event(con,job['id'],job['attempt'],'Saved retry is due; original task queued',source='queue',kind='retry_due')


def view(db: Database, job: Mapping) -> RetryView | None:
    with db.connect() as con:
        plan=con.execute('SELECT * FROM job_retry_plans WHERE job_id=?',(job['id'],)).fetchone()
        if not plan:return None
        gate=retry_gate(con,job,plan) if job['state']=='waiting_retry' and plan['enabled'] else {'blocked_reason':'','next_retry_at':plan['next_retry_at']}
        return {'job_id':plan['job_id'],'enabled':plan['enabled'],'threshold':plan['threshold'],
                'min_seconds':plan['min_seconds'],'max_seconds':plan['max_seconds'],
                'next_retry_at':iso(gate['next_retry_at']),'delay_seconds':plan['delay_seconds'],
                'reason':plan['reason'],'updated':plan['updated'],
                'record_count':record_count(con,job['dataset_id']),'blocked_reason':gate['blocked_reason']}
