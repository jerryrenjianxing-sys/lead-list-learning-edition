"""Retry plans are visible through the real authenticated job API and queue."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from workbench.app import make_app
from workbench.database import dump, insert_records
from workbench.queue import Queue
from workbench import recovery


def client_for(root):
    app=make_app(root,run_queue=False)
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
    db=app.state.db
    with db.connect() as con:
        con.execute("UPDATE jobs SET state='running',attempt=1 WHERE id=?",(job['id'],))
        con.execute("INSERT INTO job_attempts(job_id,attempt,state,started) VALUES(?,1,'running','fixture')",(job['id'],))
        records=[{'id':str(i),'text':'fixture'} for i in range(count)]
        insert_records(con,job['dataset_id'],records)
        # Repeat/update the same records; requests received are not unique records.
        insert_records(con,job['dataset_id'],records)
        current=dict(con.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone())
        current['options']=json.loads(current['options'])
        recovery.finish(con,current,'completed',{'saved_records':9999,'collection':{'empty':count==0}})
        con.commit()
    view=c.get('/api/v1/jobs/'+job['id']).json()
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
    from workbench import retrying
    queue=Queue(app.state.db,availability=app.state.availability)
    monkeypatch.setattr(retrying.time,'time',lambda:deadline-1)
    assert queue._claim() is None
    monkeypatch.setattr(retrying.time,'time',lambda:deadline+1)
    claimed=queue._claim()
    assert claimed['id']==job['id'] and claimed['dataset_id']==job['dataset_id']
    assert claimed['attempt']==1
    # A real user stop cancels a future timer and stays cancelled after its due time.
    app.state.db.execute("UPDATE jobs SET state='interrupted' WHERE id=?",(job['id'],))
    configure(c,job['id'],next_retry_at=deadline+6500)
    assert c.post('/api/v1/jobs/'+job['id']+'/stop',json={}).json()['state']=='cancelled'
    monkeypatch.setattr(retrying.time,'time',lambda:deadline+9000)
    assert queue._claim() is None
    assert c.get('/api/v1/jobs/'+job['id']).json()['state']=='cancelled'


def test_waiting_retry_cannot_be_hidden_and_explicit_resume_clears_timer(tmp_path):
    app,c=client_for(tmp_path)
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
    configure(c,job['id'])
    assert c.post('/api/v1/jobs/clear-history',json={'ids':[job['id']]}).status_code==409
    assert c.post('/api/v1/jobs/'+job['id']+'/resume',json={}).status_code==200
    view=c.get('/api/v1/jobs/'+job['id']).json()
    assert view['state']=='queued'
    assert view['status_detail']['next_retry_at'] is None


def test_probe_lease_arguments_do_not_conflict_with_dispatch_method(monkeypatch):
    import asyncio
    from workbench.sessions import SessionManager
    manager=object.__new__(SessionManager)
    manager._start=lambda:None
    manager.lock=asyncio.Lock();manager.loop=None
    async def acquire_probe(*,operation):return {'lease_for':operation['id']}
    manager._acquire_probe=acquire_probe
    class ImmediateFuture:
        def __init__(self,coro):self.coro=coro
        def result(self,timeout):return asyncio.run(self.coro)
    monkeypatch.setattr(asyncio,'run_coroutine_threadsafe',lambda coro,loop:ImmediateFuture(coro))
    assert manager.call('acquire_probe',operation={'id':'fixture'},timeout=60)=={'lease_for':'fixture'}


def test_saved_retry_does_not_bypass_platform_block_or_changed_account(tmp_path,monkeypatch):
    app,c=client_for(tmp_path);db=app.state.db
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
    db.execute("INSERT INTO platform_sessions(platform,account_id) VALUES('dy','account-a')")
    deadline=time.time()+6000
    configure(c,job['id'],next_retry_at=deadline)
    from workbench import retrying
    monkeypatch.setattr(retrying.time,'time',lambda:deadline+1)
    queue=Queue(db,availability=app.state.availability)
    db.execute("INSERT INTO access_state(platform,account_id,capability,state,reason,updated) VALUES('dy','account-a','search','restricted','fixture','fixture')")
    assert queue._claim() is None
    db.execute("UPDATE access_state SET state='available'")
    db.execute("UPDATE platform_sessions SET account_id='account-b'")
    assert queue._claim() is None
    db.execute("UPDATE platform_sessions SET account_id='account-a'")
    assert queue._claim()['id']==job['id']


def test_repeated_retry_does_not_starve_an_unattempted_keyword(tmp_path):
    app,c=client_for(tmp_path);db=app.state.db
    first=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'first'}).json()
    second=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'second'}).json()
    queue=Queue(db,availability=app.state.availability)
    assert queue._claim()['id']==first['id']
    db.execute("UPDATE jobs SET state='queued' WHERE id=?",(first['id'],))
    assert queue._claim()['id']==second['id']


@pytest.mark.parametrize('user_stop',[False,True])
def test_planned_pause_is_distinct_from_a_later_user_stop(tmp_path,user_stop):
    app,c=client_for(tmp_path);db=app.state.db
    job=c.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture'}).json()
    claimed=Queue(db,availability=app.state.availability)._claim()
    configure(c,job['id'])
    assert c.get('/api/v1/jobs/'+job['id']).json()['state']=='stopping'
    if user_stop:c.post('/api/v1/jobs/'+job['id']+'/stop',json={})
    with db.connect() as con:
        recovery.finish(con,claimed,'completed',{'saved_records':0})
        con.commit()
    view=c.get('/api/v1/jobs/'+job['id']).json()
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
    claimed=Queue(db,availability=app.state.availability)._claim()
    with db.connect() as con:
        recovery.finish(con,claimed,'completed',{'saved_records':0})
        con.commit()
    status=c.get('/api/v1/jobs/'+job['id']).json()
    assert status['state']=='completed' and status['status_detail']['retry'] is None
