import json
import sys

from fastapi.testclient import TestClient

from workbench.app import SKILL, make_app


def test_handshake_requires_real_authorization_and_has_no_side_effects(tmp_path):
    app = make_app(tmp_path, run_queue=False)
    with TestClient(app) as client:
        assert client.get('/api/v1/agent/handshake').status_code == 401
        assert client.get('/api/v1/agent/handshake', headers={'Authorization': 'Bearer wrong'}).status_code == 401
        client.get('/api/v1/session')
        first = client.get('/api/v1/agent/handshake').json()
        assert first['status'] == 'ok' and first['api_version'] == 1
        assert first['summary']['analysis'] == 'user_defined'
        assert first['instance_id'] == app.state.agent_access.instance_id
        assert first['workspace_id'] == app.state.agent_access.workspace_id
        assert client.get('/api/v1/agent/handshake').json() == first
        payload = json.dumps(first)
        assert app.state.token not in payload and str(tmp_path) not in payload
        assert 'python' not in first and len(payload) < 2500
        for resource in ('jobs', 'datasets', 'analyses', 'artifacts'):
            assert client.get('/api/v1/' + resource).json()['items'] == []
        assert client.get('/api/v1/diagnostics/self-tests/latest').json() is None


def test_restarted_host_keeps_workspace_but_changes_instance(tmp_path):
    def read(root):
        app = make_app(root, run_queue=False)
        with TestClient(app) as client:
            client.get('/api/v1/session')
            return client.get('/api/v1/agent/handshake').json()
    first, restarted, other = read(tmp_path / 'one'), read(tmp_path / 'one'), read(tmp_path / 'two')
    assert first['workspace_id'] == restarted['workspace_id']
    assert first['instance_id'] != restarted['instance_id']
    assert first['workspace_id'] != other['workspace_id']
    assert first['capability_revision'] == restarted['capability_revision'] == other['capability_revision']


def test_home_bootstrap_binds_the_actual_instance_and_installed_client(tmp_path, monkeypatch):
    instance = tmp_path / "中文 app's instance.json"
    monkeypatch.setenv('MEDIAWORKBENCH_INSTANCE', str(instance))
    app = make_app(tmp_path / 'data', run_queue=False)
    with TestClient(app) as client:
        response = client.get('/api/v1/skill?format=connect')
        assert response.status_code == 200
        text = response.text
        assert str(SKILL / 'SKILL.md') in text and sys.executable in text
        assert str(instance).replace("'", "''") in text
        assert '--instance' in text and ' connect' in text
        assert app.state.token not in text
        assert '备用' in text and len(text) < 2000
