"""Persist retry intent separately from user stops and per-attempt outcomes."""
from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone

from .database import Conflict, Missing, now


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch is not None else None


def choose(plan):
    return random.SystemRandom().randint(plan['min_seconds'], plan['max_seconds'])


def account(con, platform):
    row=con.execute('SELECT account_id FROM platform_sessions WHERE platform=?',(platform,)).fetchone()
    return row['account_id'] if row else None


def configure(con, job_id, body):
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
    if running and schedule:deadline=None
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


def finish_state(con, job, state):
    """Run after ownership/stop fencing, in the same transaction as finalization."""
    from .recovery import event
    plan=con.execute('SELECT * FROM job_retry_plans WHERE job_id=? AND enabled=1',(job['id'],)).fetchone()
    if not plan or state in ('needs_login','cancelled'):
        return state
    total=con.execute('SELECT count(*) FROM records WHERE dataset_id=?',(job['dataset_id'],)).fetchone()[0]
    low=total<=plan['threshold']
    incomplete=state in ('partial','failed','interrupted','waiting_access','retry_requested')
    if not low and not incomplete:
        con.execute('UPDATE job_retry_plans SET next_retry_at=NULL,reason=?,updated=? WHERE job_id=?',('',now(),job['id']))
        return state
    delay=plan['delay_seconds'] if state=='retry_requested' and plan['delay_seconds'] else choose(plan)
    deadline=time.time()+delay
    reason='insufficient_records' if low else 'collection_incomplete'
    con.execute('UPDATE job_retry_plans SET next_retry_at=?,delay_seconds=?,reason=?,account_id=COALESCE(account_id,?),updated=? WHERE job_id=?',
                (deadline,delay,reason,account(con,job['platform']),now(),job['id']))
    event(con,job['id'],job['attempt'],'Retry scheduled',source='queue',kind='retry_scheduled',
          details={'reason':reason,'dataset_total':total,'threshold':plan['threshold'],
                   'next_retry_at':iso(deadline),'delay_seconds':delay,'attempt_outcome':state})
    return 'waiting_retry'


def release_due(con):
    """Claim a saved timer without clearing authentication or capability blocks."""
    from .recovery import event
    epoch=time.time()
    rows=con.execute("SELECT j.*,p.account_id retry_account FROM jobs j JOIN job_retry_plans p ON p.job_id=j.id WHERE j.state='waiting_retry' AND p.enabled=1 AND p.next_retry_at<=? ORDER BY p.next_retry_at,j.created,j.id",(epoch,)).fetchall()
    for job in rows:
        current_account=account(con,job['platform'])
        if job['retry_account'] and job['retry_account']!=current_account:continue
        if (job['error'] or '').startswith('对照读取成功，但原目标重试后仍异常'):continue
        blocked=con.execute("SELECT 1 FROM access_state WHERE platform=? AND account_id=? AND state IN ('restricted','suspected_restriction','manual_action')",(job['platform'],current_account or '')).fetchone()
        if job['platform']=='dy' and blocked:continue
        changed=con.execute("UPDATE jobs SET state='queued',error=NULL,updated=? WHERE id=? AND state='waiting_retry'",(now(),job['id'])).rowcount
        if changed:
            con.execute('UPDATE job_retry_plans SET next_retry_at=NULL,updated=? WHERE job_id=?',(now(),job['id']))
            event(con,job['id'],job['attempt'],'Saved retry is due; original task queued',source='queue',kind='retry_due')


def view(db, job):
    rows=db.query('SELECT * FROM job_retry_plans WHERE job_id=?',(job['id'],))
    if not rows:return None
    result=dict(rows[0]);result.pop('account_id',None)
    session=db.query('SELECT account_id FROM platform_sessions WHERE platform=?',(job['platform'],))
    current=session[0]['account_id'] if session else None
    result['blocked_reason']='account_changed' if rows[0]['account_id'] and rows[0]['account_id']!=current else ''
    if result['blocked_reason']:
        result['next_retry_at']=None
    else:
        blocks=db.query("SELECT state,cooldown_until FROM access_state WHERE platform=? AND account_id=? AND state IN ('restricted','suspected_restriction','manual_action')",(job['platform'],current or '')) if job['platform']=='dy' else []
        if any(b['state']=='manual_action' for b in blocks) or (job.get('error') or '').startswith('对照读取成功，但原目标重试后仍异常'):
            result['blocked_reason']='manual_action';result['next_retry_at']=None
        elif blocks:
            result['blocked_reason']='access_recheck'
            if result['next_retry_at'] is not None:
                result['next_retry_at']=max([result['next_retry_at']]+[b['cooldown_until'] for b in blocks if b['cooldown_until'] is not None])
    result['next_retry_at']=iso(result['next_retry_at'])
    return result
