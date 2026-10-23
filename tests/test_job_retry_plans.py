"""Retry plans are visible through the real authenticated job API and queue."""
import time
import sys
import json
from contextlib import contextmanager
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from workbench.app import make_app
from workbench.database import insert_records
from workbench.queue import Queue


@contextmanager
def workers(app, script='', *, probe=None, sessions=None):
    queue=Queue(app.state.db,lambda jid:[sys.executable,'-c',script,jid],
                availability=app.state.availability,probe_command=probe,sessions=sessions)
    queue.start()
    try:yield
    finally:queue.close()


def authenticated(app):
    app.state.db.execute("INSERT OR REPLACE INTO platform_sessions(platform,account_id,generation,check_state) VALUES('dy','fixture-account',1,'authenticated')")


def wait_job(client,jid,predicate,timeout=8):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        result=client.get('/api/v1/jobs/'+jid).json()
        if predicate(result):return result
        time.sleep(.02)
    raise AssertionError(result)


WAIT_FOR_STOP="""
import os,time,pathlib
stop=pathlib.Path(os.environ['MEDIAWORKBENCH_LOGIN_DIR'])/'.stop'
while not stop.exists(): time.sleep(.01)
"""


def complete_early_control(client,job_id):
    view=client.get('/api/v1/jobs/'+job_id).json()
    operation=view['status_detail']['operation_refs'][0]
    assert client.post('/api/v1/operations/'+operation+'/cancel',json={}).status_code==200
    control=client.post('/api/v1/platforms/dy/probes',json={'capability':'search'}).json()
    end=time.monotonic()+5
    while time.monotonic()<end:
        result=client.get('/api/v1/operations/'+control['id']).json()
        if result['state']=='completed':break
        time.sleep(.02)
    assert result['verdict']=='available'


def test_profiles_cannot_satisfy_content_comment_reply_threshold(tmp_path):
    app,c=client_for(tmp_path)
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture','enrich_profiles':True}).json()
    with app.state.db.connect() as con:
        insert_records(con,job['dataset_id'],[{'id':'content','kind':'contents'}]+
                       [{'id':'profile-'+str(i),'kind':'profiles'} for i in range(100)])
        con.commit()
    with workers(app):
        view=wait_job(c,job['id'],lambda j:j['state'] not in ('queued','running'))
    assert view['state']=='waiting_retry'
    assert view['status_detail']['retry']['record_count']==1
    assert view['status_detail']['counts']['dataset_total']==101
    assert '累计去重 1 条' in view['status_detail']['explanation']


@pytest.mark.parametrize('check_state',['logged_out','challenge','unknown'])
def test_due_retry_waits_for_valid_authentication(tmp_path,check_state):
    app,c=client_for(tmp_path)
    authenticated(app)
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
    configure(c,job['id'],next_retry_at=time.time()-1)
    app.state.db.execute('UPDATE platform_sessions SET check_state=?',(check_state,))
    with workers(app):
        time.sleep(.5)
        view=c.get('/api/v1/jobs/'+job['id']).json()
        assert view['state']=='waiting_retry' and view['attempt']==0
        assert view['status_detail']['retry']['blocked_reason']=='authentication_required'
        assert view['status_detail']['next_retry_at'] is None
        app.state.db.execute("UPDATE platform_sessions SET check_state='authenticated'")
        wait_job(c,job['id'],lambda j:j['attempt']==1)


def test_running_pause_keeps_explicit_deadline_after_worker_exit(tmp_path):
    app,c=client_for(tmp_path)
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
    deadline=time.time()+25000
    with workers(app,WAIT_FOR_STOP):
        wait_job(c,job['id'],lambda j:j['state']=='running')
        configure(c,job['id'],next_retry_at=deadline)
        view=wait_job(c,job['id'],lambda j:j['state']=='waiting_retry')
        saved=view['status_detail']['retry']['next_retry_at']
        assert datetime.fromisoformat(saved).timestamp()==pytest.approx(deadline,abs=.001,rel=0)


@pytest.mark.parametrize('early_control',[False,True])
def test_control_success_does_not_bypass_target_replay_limit(tmp_path,monkeypatch,early_control):
    app,c=client_for(tmp_path)
    authenticated(app)
    epoch=[time.time()]
    monkeypatch.setattr('workbench.retrying.time.time',lambda:epoch[0])
    app.state.availability.clock=lambda:epoch[0]
    probe="import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('{\"verdict\":\"available\",\"reason\":\"control_success\",\"attempted\":true,\"requests\":1}')"
    command=lambda identity,result:[sys.executable,'-c',probe,str(result)]
    script="import os,sys;os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1];from tools.crawl_status import record;record('search','empty_response',scope='fixture')"
    with workers(app,script,probe=command):
        job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
        for attempt in (1,2):
            view=wait_job(c,job['id'],lambda j:j['attempt']==attempt and j['state']=='waiting_retry')
            if early_control and attempt==1:
                complete_early_control(c,job['id'])
                assert c.get('/api/v1/jobs/'+job['id']).json()['attempt']==1
            epoch[0]=datetime.fromisoformat(view['status_detail']['next_retry_at']).timestamp()+1
        view=wait_job(c,job['id'],lambda j:j['status_detail']['retry']['blocked_reason']=='manual_action',timeout=5)
        time.sleep(.4)
        assert c.get('/api/v1/jobs/'+job['id']).json()['attempt']==2
        assert view['status_detail']['next_retry_at'] is None


def test_early_control_cannot_resume_a_different_account(tmp_path,monkeypatch):
    app,c=client_for(tmp_path)
    epoch=[time.time()]
    monkeypatch.setattr('workbench.retrying.time.time',lambda:epoch[0])
    app.state.availability.clock=lambda:epoch[0]
    probe="import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('{\"verdict\":\"available\",\"reason\":\"control_success\",\"attempted\":true,\"requests\":1}')"
    command=lambda identity,result:[sys.executable,'-c',probe,str(result)]
    script="import os,sys;os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1];from tools.crawl_status import record;record('search','empty_response',scope='fixture')"
    with workers(app,script,probe=command):
        job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
        view=wait_job(c,job['id'],lambda j:j['state']=='waiting_retry')
        complete_early_control(c,job['id'])
        app.state.db.execute("UPDATE platform_sessions SET account_id='another-account'")
        epoch[0]=datetime.fromisoformat(view['status_detail']['next_retry_at']).timestamp()+1
        time.sleep(.6)
        view=c.get('/api/v1/jobs/'+job['id']).json()
        assert view['attempt']==1 and view['state']=='waiting_retry'
        assert view['status_detail']['retry']['blocked_reason']=='account_changed'


@pytest.mark.parametrize('verdict',['network_error','interface_error','unconfirmed'])
def test_inconclusive_control_saves_next_retry_instead_of_stranding(tmp_path,monkeypatch,verdict):
    app,c=client_for(tmp_path)
    epoch=[time.time()]
    monkeypatch.setattr('workbench.retrying.time.time',lambda:epoch[0])
    app.state.availability.clock=lambda:epoch[0]
    response=tmp_path/'probe-response.json'
    response.write_text(json.dumps({'verdict':verdict,'reason':'timeout','attempted':True,'requests':1}))
    probe=f"import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(pathlib.Path({str(response)!r}).read_bytes())"
    command=lambda identity,result:[sys.executable,'-c',probe,str(result)]
    script="import os,sys;os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1];from tools.crawl_status import record;record('search','empty_response',scope='fixture')"
    with workers(app,script,probe=command):
        job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
        view=wait_job(c,job['id'],lambda j:j['state']=='waiting_retry')
        epoch[0]=datetime.fromisoformat(view['status_detail']['next_retry_at']).timestamp()+1
        view=wait_job(c,job['id'],lambda j:j['status_detail']['retry']['reason']=='access_check_inconclusive',timeout=5)
        next_time=datetime.fromisoformat(view['status_detail']['next_retry_at']).timestamp()
        assert 5400<=next_time-epoch[0]<=7200
        assert view['attempt']==1
        response.write_text('{"verdict":"available","reason":"control_success","attempted":true,"requests":1}')
        epoch[0]=next_time+1
        wait_job(c,job['id'],lambda j:j['attempt']==2)


def test_due_timer_preserves_a_control_already_queued(tmp_path,monkeypatch):
    app,c=client_for(tmp_path)
    epoch=[time.time()]
    probe_epoch=[epoch[0]]
    monkeypatch.setattr('workbench.retrying.time.time',lambda:epoch[0])
    app.state.availability.clock=lambda:probe_epoch[0]
    probe="import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('{\"verdict\":\"available\",\"reason\":\"control_success\",\"attempted\":true,\"requests\":1}')"
    command=lambda identity,result:[sys.executable,'-c',probe,str(result)]
    script="import os,sys;os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1];from tools.crawl_status import record;record('search','empty_response',scope='fixture')"
    with workers(app,script,probe=command):
        job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
        view=wait_job(c,job['id'],lambda j:j['state']=='waiting_retry')
        deadline=datetime.fromisoformat(view['status_detail']['next_retry_at']).timestamp()
        operation=view['status_detail']['operation_refs'][0]
        # The control dispatcher observed the instant before the timer became
        # due. The task dispatcher must not postpone the accepted operation.
        epoch[0]=deadline+1
        time.sleep(.5)
        view=c.get('/api/v1/jobs/'+job['id']).json()
        assert datetime.fromisoformat(view['status_detail']['next_retry_at']).timestamp()==pytest.approx(deadline,abs=.001,rel=0)
        assert c.get('/api/v1/operations/'+operation).json()['state']=='accepted'
        probe_epoch[0]=epoch[0]
        wait_job(c,job['id'],lambda j:j['attempt']==2)


@pytest.mark.parametrize('cooldown_seconds',[0,14400])
def test_shared_control_uses_earliest_due_keyword(tmp_path,monkeypatch,cooldown_seconds):
    app,c=client_for(tmp_path)
    epoch=[time.time()]
    monkeypatch.setattr('workbench.retrying.time.time',lambda:epoch[0])
    app.state.availability.clock=lambda:epoch[0]
    probe="import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('{\"verdict\":\"available\",\"reason\":\"control_success\",\"attempted\":true,\"requests\":1}')"
    command=lambda identity,result:[sys.executable,'-c',probe,str(result)]
    script="import os,sys;os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1];from tools.crawl_status import record;record('search','empty_response',scope='fixture')"
    with workers(app,script,probe=command):
        first=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'first'}).json()
        wait_job(c,first['id'],lambda j:j['state']=='waiting_retry')
        second=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'second'}).json()
        wait_job(c,second['id'],lambda j:j['state']=='waiting_retry')
        configure(c,first['id'],next_retry_at=epoch[0]+7200)
        configure(c,second['id'],next_retry_at=epoch[0]+5400)
        started=epoch[0]
        if cooldown_seconds:
            app.state.db.execute("UPDATE access_state SET state='restricted',cooldown_until=?",(started+cooldown_seconds,))
        epoch[0]+=5401
        if cooldown_seconds:
            # Neither another due task nor the source task's own timer can
            # shorten the longer platform cooldown of an already queued check.
            for due in (started+5401,started+7201):
                epoch[0]=due
                time.sleep(.4)
                for job in (first,second):
                    assert c.get('/api/v1/jobs/'+job['id']).json()['attempt']==1
            epoch[0]=started+cooldown_seconds+1
        wait_job(c,second['id'],lambda j:j['attempt']==2,timeout=5)
        if not cooldown_seconds:
            first_view=c.get('/api/v1/jobs/'+first['id']).json()
            assert first_view['attempt']==1 and first_view['state']=='waiting_retry'


@pytest.mark.parametrize('blocked',['manual_action','authentication_required'])
def test_blocked_waiter_cannot_trigger_a_shared_control(tmp_path,monkeypatch,blocked):
    app,c=client_for(tmp_path)
    epoch=[time.time()]
    monkeypatch.setattr('workbench.retrying.time.time',lambda:epoch[0])
    app.state.availability.clock=lambda:epoch[0]
    probe="import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('{\"verdict\":\"available\",\"reason\":\"control_success\",\"attempted\":true,\"requests\":1}')"
    command=lambda identity,result:[sys.executable,'-c',probe,str(result)]
    script="import os,sys;os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1];from tools.crawl_status import record;record('search','empty_response',scope='fixture')"
    with workers(app,script,probe=command):
        first=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'first'}).json()
        first_view=wait_job(c,first['id'],lambda j:j['state']=='waiting_retry')
        operation=first_view['status_detail']['operation_refs'][0]
        second=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'second'}).json()
        wait_job(c,second['id'],lambda j:j['state']=='waiting_retry')
        configure(c,first['id'],next_retry_at=epoch[0]+7200)
        configure(c,second['id'],next_retry_at=epoch[0]+5400)
        if blocked=='manual_action':
            app.state.db.execute('UPDATE jobs SET error=? WHERE id=?',('对照读取成功，但原目标重试后仍异常，需要人工处理',second['id']))
        else:
            app.state.db.execute("UPDATE platform_sessions SET check_state='unknown'")
        epoch[0]+=5401
        time.sleep(.5)
        assert c.get('/api/v1/jobs/'+second['id']).json()['status_detail']['retry']['blocked_reason']==blocked
        assert c.get('/api/v1/operations/'+operation).json()['state']=='accepted'
        # Restore only the eligibility boundary; the same saved operation runs.
        app.state.db.execute('UPDATE jobs SET error=NULL WHERE id=?',(second['id'],))
        app.state.db.execute("UPDATE platform_sessions SET check_state='authenticated'")
        wait_job(c,second['id'],lambda j:j['attempt']==2)
        assert c.get('/api/v1/jobs/'+first['id']).json()['attempt']==1


@pytest.mark.parametrize('changed',['account','generation'])
def test_stale_retry_check_finishes_before_eligibility_filter(tmp_path,monkeypatch,changed):
    app,c=client_for(tmp_path)
    epoch=[time.time()]
    monkeypatch.setattr('workbench.retrying.time.time',lambda:epoch[0])
    app.state.availability.clock=lambda:epoch[0]
    script="import os,sys;os.environ['MEDIAWORKBENCH_JOB_ID']=sys.argv[1];from tools.crawl_status import record;record('search','empty_response',scope='fixture')"
    with workers(app,script):
        job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
        view=wait_job(c,job['id'],lambda j:j['state']=='waiting_retry')
        operation=view['status_detail']['operation_refs'][0]
        if changed=='account':
            app.state.db.execute("UPDATE platform_sessions SET account_id='another-account'")
        else:
            app.state.db.execute('UPDATE platform_sessions SET generation=generation+1')
        epoch[0]=datetime.fromisoformat(view['status_detail']['next_retry_at']).timestamp()+1
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            result=c.get('/api/v1/operations/'+operation).json()
            if result['state']=='failed':break
            time.sleep(.02)
        assert result['state']=='failed' and result['reason_code']=='session_changed'
        assert c.get('/api/v1/jobs/'+job['id']).json()['attempt']==1


def client_for(root):
    app=make_app(root,run_queue=False)
    authenticated(app)
    client=TestClient(app)
    client.get('/api/v1/session')
    return app,client


def configure(client,jid,**options):
    response=client.post(f'/api/v1/jobs/{jid}/retry-plan',json=options)
    assert response.status_code==200,response.text
    return response.json()


def test_scheduled_wait_is_visible_and_survives_restart(tmp_path):
    app,c=client_for(tmp_path)
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
    deadline=time.time()+6500
    assert c.get('/api/v1/jobs/'+job['id']).json()['status_detail']['retry']['threshold']==100
    configure(c,job['id'],next_retry_at=deadline)
    status=c.get('/api/v1/jobs/'+job['id']).json()
    assert status['state']=='waiting_retry'
    assert status['status_detail']['next_retry_at']
    assert status['status_detail']['retry']['threshold']==100
    assert status['status_detail']['result']!='user_stopped'
    assert status['status_detail']['lifecycle']=='waiting'
    app2,c2=client_for(tmp_path)
    again=c2.get('/api/v1/jobs/'+job['id']).json()
    assert again['status_detail']['next_retry_at']==status['status_detail']['next_retry_at']
    assert app2.state.db.one('jobs',job['id'])['dataset_id']==job['dataset_id']


@pytest.mark.parametrize('count,expected',[(0,'waiting_retry'),(100,'waiting_retry'),(101,'completed')])
def test_threshold_uses_committed_unique_total_including_100(tmp_path,count,expected):
    app,c=client_for(tmp_path)
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
    configure(c,job['id'],schedule=False)
    with app.state.db.connect() as con:
        records=[{'id':str(i),'text':'fixture','kind':'comments' if i%2 else 'contents'} for i in range(count)]
        insert_records(con,job['dataset_id'],records)
        # Repeat/update the same records; requests received are not unique records.
        insert_records(con,job['dataset_id'],records)
        con.commit()
    with workers(app):
        view=wait_job(c,job['id'],lambda j:j['state']==expected)
    assert view['state']==expected
    assert view['status_detail']['counts']['dataset_total']==count
    if count<=100:
        wait=view['status_detail']['retry']['delay_seconds']
        assert 5400<=wait<=7200
        assert view['status_detail']['reason_code']=='insufficient_records'


def test_due_queue_reuses_original_job_and_dataset_and_stop_wins(tmp_path,monkeypatch):
    app,c=client_for(tmp_path)
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
    deadline=time.time()+6000
    configure(c,job['id'],next_retry_at=deadline)
    epoch=[deadline-1]
    monkeypatch.setattr('workbench.retrying.time.time',lambda:epoch[0])
    with workers(app):
        time.sleep(.4)
        assert c.get('/api/v1/jobs/'+job['id']).json()['attempt']==0
        epoch[0]=deadline+1
        claimed=wait_job(c,job['id'],lambda j:j['attempt']==1 and j['state']=='waiting_retry')
        assert claimed['dataset_id']==job['dataset_id']
        assert c.post('/api/v1/jobs/'+job['id']+'/stop',json={}).json()['state']=='cancelled'
        epoch[0]=deadline+9000
        time.sleep(.4)
        assert c.get('/api/v1/jobs/'+job['id']).json()['state']=='cancelled'
    assert len(c.get('/api/v1/jobs').json()['items'])==1


def test_waiting_retry_cannot_be_hidden_and_explicit_resume_clears_timer(tmp_path):
    app,c=client_for(tmp_path)
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
    configure(c,job['id'])
    assert c.post('/api/v1/jobs/clear-history',json={'ids':[job['id']]}).status_code==409
    assert c.post('/api/v1/jobs/'+job['id']+'/resume',json={}).status_code==200
    view=c.get('/api/v1/jobs/'+job['id']).json()
    assert view['state']=='queued'
    assert view['status_detail']['next_retry_at'] is None


def test_probe_lease_arguments_do_not_conflict_with_dispatch_method(tmp_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:browser=pw.chromium.executable_path
    app,c=client_for(tmp_path)
    app.state.sessions.store.bind('dy',browser)
    probe="import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('{\"verdict\":\"available\",\"reason\":\"control_success\",\"attempted\":true,\"requests\":1}')"
    command=lambda identity,result:[sys.executable,'-c',probe,str(result)]
    with workers(app,probe=command,sessions=app.state.sessions):
        result=c.post('/api/v1/platforms/dy/probes',json={'capability':'search'}).json()
        end=time.monotonic()+15
        while time.monotonic()<end:
            operation=c.get('/api/v1/operations/'+result['id']).json()
            if operation['state'] not in ('accepted','running'):break
            time.sleep(.05)
        assert operation['state']=='completed',operation
        assert operation['verdict']=='available'
        assert operation['cleanup_complete']
    assert c.get('/api/v1/datasets').json()['items']==[]


def test_saved_retry_does_not_bypass_platform_block_or_changed_account(tmp_path):
    app,c=client_for(tmp_path);db=app.state.db
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
    db.execute("UPDATE platform_sessions SET account_id='account-a'")
    deadline=time.time()-1
    configure(c,job['id'],next_retry_at=deadline)
    db.execute("INSERT INTO access_state(platform,account_id,capability,state,reason,updated) VALUES('dy','account-a','search','restricted','fixture','fixture')")
    with workers(app):
        time.sleep(.4)
        assert c.get('/api/v1/jobs/'+job['id']).json()['attempt']==0
        db.execute("UPDATE access_state SET state='available'")
        db.execute("UPDATE platform_sessions SET account_id='account-b'")
        time.sleep(.4)
        assert c.get('/api/v1/jobs/'+job['id']).json()['attempt']==0
        db.execute("UPDATE platform_sessions SET account_id='account-a'")
        wait_job(c,job['id'],lambda j:j['attempt']==1)


def test_repeated_retry_does_not_starve_an_unattempted_keyword(tmp_path):
    app,c=client_for(tmp_path);db=app.state.db
    first=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'first'}).json()
    with workers(app,WAIT_FOR_STOP):
        wait_job(c,first['id'],lambda j:j['state']=='running')
        second=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'second'}).json()
        configure(c,first['id'],next_retry_at=time.time()-1)
        wait_job(c,second['id'],lambda j:j['state']=='running')
        assert c.get('/api/v1/jobs/'+first['id']).json()['attempt']==1
        c.post('/api/v1/jobs/'+first['id']+'/stop',json={})
        c.post('/api/v1/jobs/'+second['id']+'/stop',json={})


@pytest.mark.parametrize('user_stop',[False,True])
def test_planned_pause_is_distinct_from_a_later_user_stop(tmp_path,user_stop):
    app,c=client_for(tmp_path);db=app.state.db
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
    with workers(app,WAIT_FOR_STOP):
        wait_job(c,job['id'],lambda j:j['state']=='running')
        configure(c,job['id'])
        if user_stop:c.post('/api/v1/jobs/'+job['id']+'/stop',json={})
        view=wait_job(c,job['id'],lambda j:j['state'] in ('cancelled','waiting_retry'))
        assert view['state']==('cancelled' if user_stop else 'waiting_retry')
        assert ('用户已停止' in view['status_detail']['explanation'])==user_stop


def test_retry_plan_request_replay_does_not_redraw_deadline(tmp_path):
    app,c=client_for(tmp_path)
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
    headers={'Idempotency-Key':'same-retry-request'}
    a=c.post('/api/v1/jobs/'+job['id']+'/retry-plan',json={},headers=headers)
    b=c.post('/api/v1/jobs/'+job['id']+'/retry-plan',json={},headers=headers)
    assert a.status_code==b.status_code==200
    assert a.json()==b.json()


def test_real_worker_exit_schedules_the_original_search(tmp_path):
    import sys
    app,c=client_for(tmp_path)
    queue=Queue(app.state.db,lambda jid:[sys.executable,'-c',"print('fixture empty search')",jid],availability=app.state.availability)
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
    queue.start()
    try:
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            view=c.get('/api/v1/jobs/'+job['id']).json()
            if view['state']=='waiting_retry':break
            time.sleep(.02)
        assert view['state']=='waiting_retry'
        assert view['dataset_id']==job['dataset_id'] and view['attempt']==1
        assert view['status_detail']['reason_code']=='insufficient_records'
        assert view['status_detail']['next_retry_at']
    finally:queue.close()


def test_schema5_migration_backs_up_and_preserves_records(tmp_path):
    import sqlite3
    from workbench.database import Database
    app,c=client_for(tmp_path);db=app.state.db
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
    with db.connect() as con:
        insert_records(con,job['dataset_id'],[{'id':'preserved','text':'fixture'}])
        con.execute('DROP TABLE job_retry_plans')
        con.execute('PRAGMA user_version=5')
        con.commit()
    updated=Database(tmp_path)
    assert updated.query('PRAGMA user_version')[0]['user_version']==6
    assert updated.query('SELECT count(*) n FROM records')[0]['n']==1
    assert updated.one('jobs',job['id'])['dataset_id']==job['dataset_id']
    backups=list((tmp_path/'backups').glob('*.sqlite3'))
    assert backups
    with sqlite3.connect(backups[-1]) as saved:
        assert saved.execute('PRAGMA user_version').fetchone()[0]==5
        assert saved.execute('SELECT count(*) FROM records').fetchone()[0]==1


def test_explicit_opt_out_keeps_a_single_small_search_terminal(tmp_path):
    app,c=client_for(tmp_path);db=app.state.db
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture','retry_threshold':None}).json()
    with workers(app):
        status=wait_job(c,job['id'],lambda j:j['state']=='completed')
    assert status['state']=='completed' and status['status_detail']['retry'] is None
