"""Authenticated list projections remain complete and coherent while inputs change."""

from contextlib import contextmanager
import sqlite3

from fastapi.testclient import TestClient

from workbench.app import make_app
from workbench.database import Database
from tests.test_workbench import post, prepare


def test_analysis_list_matches_details_for_fifty_mixed_and_empty_snapshots(tmp_path):
    with TestClient(make_app(tmp_path, run_queue=False)) as client:
        client.get('/api/v1/session')
        assert client.get('/api/v1/analyses').json() == {'items': []}
        dataset = post(client, '/demo')
        empty = post(client, '/datasets', {'name': '空数据集'})
        identities = []
        for index in range(50):
            body = {
                'dataset_id': dataset['id'] if index % 2 else empty['id'],
                'name': f'分析 {index}', 'goal': '保留分析范围与覆盖率',
                'metadata': {'slot': index, 'label': '原始字段'},
                'result_schema': {'type': 'object'} if index % 3 else None,
            }
            result = post(client, '/analyses', body)
            identities.append(result['id'])
            if index % 2:
                inputs = client.get(f"/api/v1/analyses/{result['id']}/inputs").json()['items']
                post(client, f"/analyses/{result['id']}/batches", {
                    'batch_id': 'mixed', 'processed_ids': [inputs[0]['id']],
                    'skipped': {inputs[1]['id']: '不相关'},
                    'failed': {inputs[2]['id']: '待处理'},
                })
        response = client.get('/api/v1/analyses')
        assert response.status_code == 200
        rows = response.json()['items']
        assert len(rows) == 50
        assert [row['id'] for row in rows] == identities[::-1]
        for row in rows:
            assert row == client.get('/api/v1/analyses/' + row['id']).json()
            assert row['coverage'] == (
                {'total': 6, 'pending': 3, 'processed': 1, 'skipped': 1, 'failed': 1}
                if row['metadata']['slot'] % 2 else
                {'total': 0, 'pending': 0, 'processed': 0, 'skipped': 0, 'failed': 0}
            )


def test_analysis_list_uses_one_read_snapshot_during_atomic_completion(tmp_path, monkeypatch):
    with TestClient(make_app(tmp_path, run_queue=False)) as client:
        client.get('/api/v1/session')
        _, identity, _ = prepare(client)
        original_connect = Database.connect
        completed = False

        def finish_during_coverage_read(statement):
            nonlocal completed
            sql = statement.upper()
            if not completed and 'SELECT' in sql and 'COUNT(' in sql and 'ANALYSIS_INPUTS' in sql:
                completed = True
                with sqlite3.connect(tmp_path / 'workbench.sqlite3') as writer:
                    writer.execute("UPDATE analysis_inputs SET state='processed'")
                    writer.execute("UPDATE analyses SET state='completed'")

        @contextmanager
        def concurrent_storage(database):
            with original_connect(database) as connection:
                connection.set_trace_callback(finish_during_coverage_read)
                yield connection

        monkeypatch.setattr(Database, 'connect', concurrent_storage)
        response = client.get('/api/v1/analyses')
        assert response.status_code == 200
        assert completed, 'The concurrent storage write must actually happen'
        row = response.json()['items'][0]
        assert row['id'] == identity['id']
        assert row['state'] == 'waiting_agent'
        assert row['coverage'] == {'total': 6, 'pending': 6, 'processed': 0, 'skipped': 0, 'failed': 0}
        detail = client.get('/api/v1/analyses/' + row['id']).json()
        assert detail['state'] == 'completed'
        assert detail['coverage']['processed'] == 6
