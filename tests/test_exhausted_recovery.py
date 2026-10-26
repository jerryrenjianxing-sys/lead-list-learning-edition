"""Exhausted cycles observed through authenticated HTTP and real workers."""
import time
import sys
import pytest
from concurrent.futures import ThreadPoolExecutor

from tests.test_job_retry_plans import client_for, configure, wait_job, workers


def legacy_exhausted(app, client, *, deadline=None):
    job=client.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture','retry_threshold':999}).json()
    configure(client,job['id'],threshold=999,next_retry_at=time.time()-1)
    app.state.db.execute(
        "INSERT INTO access_state VALUES('dy','fixture-account','search','suspected_restriction','empty_response',?,3,'fixture',NULL)",
        (deadline if deadline is not None else time.time()-1,))
    app.state.db.execute("INSERT INTO access_waits VALUES(?,'search','fixture-account',1,'fixture')",(job['id'],))
    return job


def test_legacy_exhausted_deadline_becomes_manual_review_after_restart(tmp_path):
    app,client=client_for(tmp_path)
    job=legacy_exhausted(app,client)
    with workers(app):
        view=wait_job(client,job['id'],lambda j:j['status_detail']['retry']['blocked_reason']=='manual_action',timeout=2)
        assert view['attempt']==0
        assert view['status_detail']['next_retry_at'] is None
        capability=client.get('/api/v1/platforms/dy/availability').json()['items'][0]
        assert capability['state']=='manual_action'
        assert capability['checks']==3
        assert capability['cooldown_until'] is None
    first=client.get('/api/v1/jobs/'+job['id']+'/events').json()['items']
    with workers(app):
        second=client.get('/api/v1/jobs/'+job['id']+'/events').json()['items']
    assert first==second
    assert any(e['kind']=='access_budget_exhausted' for e in first)


def wait_operation(client, identity):
    end=time.monotonic()+5
    while time.monotonic()<end:
        result=client.get('/api/v1/operations/'+identity).json()
        if result['state'] not in ('accepted','running','cancelling'):
            return result
        time.sleep(.02)
    raise AssertionError(result)


def test_explicit_restart_opens_one_new_bounded_cycle(tmp_path,monkeypatch):
    epoch=[time.time()]
    monkeypatch.setattr('workbench.retrying.time.time',lambda:epoch[0])
    app,client=client_for(tmp_path)
    app.state.availability.clock=lambda:epoch[0]
    job=legacy_exhausted(app,client)
    script="import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('{\"attempted\":true,\"requests\":1,\"verdict\":\"unconfirmed\",\"reason\":\"empty_response\",\"cleanup_complete\":true}')"
    command=lambda identity,result:[sys.executable,'-c',script,str(result)]
    with workers(app,probe=command):
        wait_job(client,job['id'],lambda j:j['status_detail']['retry']['blocked_reason']=='manual_action')
        body={'capability':'search','restart_cycle':True}
        headers={'Idempotency-Key':'one-deliberate-cycle'}
        first=client.post('/api/v1/platforms/dy/probes',json=body,headers=headers)
        assert first.status_code==200,first.text
        assert client.post('/api/v1/platforms/dy/probes',json=body,headers=headers).json()==first.json()
        duplicate=client.post('/api/v1/platforms/dy/probes',json=body).json()
        assert duplicate['id']==first.json()['id']
        wait_operation(client,first.json()['id'])
        cap=client.get('/api/v1/platforms/dy/availability').json()['items'][0]
        assert cap['checks']==0 and cap['cooldown_until']==epoch[0]+3600
        for count,delay in ((1,7200),(2,14400),(3,None)):
            epoch[0]=cap['cooldown_until']+1
            end=time.monotonic()+5
            while time.monotonic()<end:
                cap=client.get('/api/v1/platforms/dy/availability').json()['items'][0]
                if cap['checks']==count:break
                time.sleep(.02)
            assert cap['checks']==count
            assert cap['cooldown_until']==(epoch[0]+delay if delay else None)
        assert cap['state']=='manual_action'
        assert client.get('/api/v1/jobs/'+job['id']).json()['attempt']==0


@pytest.mark.parametrize('verdict,reason',[('unconfirmed','empty_response'),('network_error','timeout'),('interface_error','interface_changed')])
def test_ordinary_failed_probe_keeps_exhausted_budget(tmp_path,verdict,reason):
    app,client=client_for(tmp_path)
    job=legacy_exhausted(app,client)
    script=f"import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('{{\"attempted\":true,\"requests\":1,\"verdict\":\"{verdict}\",\"reason\":\"{reason}\"}}')"
    with workers(app,probe=lambda identity,result:[sys.executable,'-c',script,str(result)]):
        op=client.post('/api/v1/platforms/dy/probes',json={'capability':'search'}).json()
        wait_operation(client,op['id'])
        view=client.get('/api/v1/jobs/'+job['id']).json()
        assert view['status_detail']['retry']['blocked_reason']=='manual_action'
        assert view['status_detail']['next_retry_at'] is None
        cap=client.get('/api/v1/platforms/dy/availability').json()['items'][0]
        assert cap['checks']==3 and cap['cooldown_until'] is None


def test_restart_does_not_shorten_valid_cooldown_or_reset_unauthenticated_account(tmp_path):
    app,client=client_for(tmp_path)
    legacy_exhausted(app,client)
    deadline=time.time()+20000
    app.state.db.execute("UPDATE access_state SET checks=2,state='restricted',cooldown_until=?",(deadline,))
    body={'capability':'search','restart_cycle':True}
    blocked=client.post('/api/v1/platforms/dy/probes',json=body).json()
    assert client.get('/api/v1/operations/'+blocked['id']).json()['reason_code']=='cooldown_active'
    cap=client.get('/api/v1/platforms/dy/availability').json()['items'][0]
    assert cap['checks']==2 and cap['cooldown_until']==deadline
    app.state.db.execute("UPDATE platform_sessions SET check_state='logged_out'")
    assert client.post('/api/v1/platforms/dy/probes',json=body).status_code==409


def test_exhausted_cycle_retains_server_minimum_wait(tmp_path):
    app,client=client_for(tmp_path)
    legacy_exhausted(app,client)
    script="import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('{\"attempted\":true,\"requests\":1,\"verdict\":\"restricted\",\"reason\":\"restriction_explicit\",\"retry_after\":20000}')"
    with workers(app,probe=lambda identity,result:[sys.executable,'-c',script,str(result)]):
        op=client.post('/api/v1/platforms/dy/probes',json={'capability':'search'}).json()
        wait_operation(client,op['id'])
        restart=client.post('/api/v1/platforms/dy/probes',json={'capability':'search','restart_cycle':True}).json()
        result=wait_operation(client,restart['id'])
        assert result['reason_code']=='cooldown_active'
        cap=client.get('/api/v1/platforms/dy/availability').json()['items'][0]
        assert cap['checks']==3 and cap['state']=='manual_action'


def test_successful_restart_never_revives_a_stopped_task(tmp_path):
    app,client=client_for(tmp_path)
    job=legacy_exhausted(app,client)
    client.post('/api/v1/jobs/'+job['id']+'/stop',json={})
    script="import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('{\"attempted\":true,\"requests\":1,\"verdict\":\"available\",\"reason\":\"control_success\"}')"
    with workers(app,probe=lambda identity,result:[sys.executable,'-c',script,str(result)]):
        op=client.post('/api/v1/platforms/dy/probes',json={'capability':'search','restart_cycle':True}).json()
        assert wait_operation(client,op['id'])['verdict']=='available'
        view=client.get('/api/v1/jobs/'+job['id']).json()
        assert view['state']=='cancelled' and view['attempt']==0
        assert not view['status_detail']['retry']['enabled']


@pytest.mark.parametrize('count,state',[(999,'waiting_retry'),(1000,'completed')])
def test_campaign_minimum_is_inclusive_at_one_thousand(tmp_path,count,state):
    app,client=client_for(tmp_path)
    job=client.post('/api/v1/jobs',json={'platform':'dy','keywords':'fixture','retry_threshold':999}).json()
    client.post('/api/v1/datasets/'+job['dataset_id']+'/records',json={'records':[{'id':str(i),'kind':'comments','text':'fixture'} for i in range(count)]})
    with workers(app):
        view=wait_job(client,job['id'],lambda j:j['attempt']==1 and j['state'] not in ('queued','running'))
        assert view['state']==state
        assert view['status_detail']['retry']['record_count']==count


def test_concurrent_restart_requests_share_one_check(tmp_path):
    app,client=client_for(tmp_path)
    legacy_exhausted(app,client)
    def restart(_):
        return client.post('/api/v1/platforms/dy/probes',json={'capability':'search','restart_cycle':True}).json()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(restart,range(4)))
    assert len({r['id'] for r in results})==1
    assert sum(not r['reused'] for r in results)==1
    cap=client.get('/api/v1/platforms/dy/availability').json()['items'][0]
    assert cap['checks']==0


def test_legacy_probe_receipt_replays_after_upgrade(tmp_path):
    app,client=client_for(tmp_path)
    body={'capability':'search'}
    legacy=app.state.db.write('old-probe-request','POST /api/v1/platforms/dy/probes',body,
        lambda con:{'id':'historical-operation','state':'completed','reused':False})
    response=client.post('/api/v1/platforms/dy/probes',json=body,headers={'Idempotency-Key':'old-probe-request'})
    assert response.status_code==200,response.text
    assert response.json()==legacy


def test_interrupted_restart_keeps_its_count_and_operation_history(tmp_path):
    app,client=client_for(tmp_path)
    job=legacy_exhausted(app,client)
    op=client.post('/api/v1/platforms/dy/probes',json={'capability':'search','restart_cycle':True}).json()
    app.state.db.execute("UPDATE operations SET state='running' WHERE id=?",(op['id'],))
    reopened,other=client_for(tmp_path)
    with workers(reopened):
        result=wait_operation(other,op['id'])
        assert result['reason_code']=='host_interrupted'
        cap=other.get('/api/v1/platforms/dy/availability').json()['items'][0]
        assert cap['checks']==0 and cap['state']=='manual_action'
        events=other.get('/api/v1/jobs/'+job['id']+'/events').json()['items']
        assert sum(e['kind']=='access_cycle_restarted' for e in events)==1


def test_exhausted_automatic_check_finishes_without_platform_requests(tmp_path):
    app,client=client_for(tmp_path)
    legacy_exhausted(app,client)
    op=client.post('/api/v1/platforms/dy/probes',json={'capability':'search'}).json()
    app.state.db.execute('UPDATE access_checks SET automatic=1 WHERE operation_id=?',(op['id'],))
    with workers(app):
        result=wait_operation(client,op['id'])
        assert result['state']=='failed' and result['reason_code']=='retry_budget_exhausted'
        assert result['attempted'] is False


def test_a_different_capability_success_does_not_release_exhausted_replies(tmp_path):
    app,client=client_for(tmp_path)
    job=legacy_exhausted(app,client)
    app.state.db.execute("UPDATE access_state SET capability='replies'")
    app.state.db.execute("UPDATE access_waits SET capability='replies'")
    script="import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('{\"attempted\":true,\"requests\":1,\"verdict\":\"available\",\"reason\":\"control_success\"}')"
    with workers(app,probe=lambda identity,result:[sys.executable,'-c',script,str(result)]):
        op=client.post('/api/v1/platforms/dy/probes',json={'capability':'search'}).json()
        assert wait_operation(client,op['id'])['verdict']=='available'
        view=client.get('/api/v1/jobs/'+job['id']).json()
        assert view['attempt']==0 and view['status_detail']['next_retry_at'] is None
        assert view['status_detail']['retry']['blocked_reason']=='manual_action'
