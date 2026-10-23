"""The installer gate accepts only the declared schema migration and preserved history."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest

from workbench.database import AUTH_SCHEMA, SCHEMA, SESSION_SCHEMA, Database


@pytest.fixture
def migrated(tmp_path):
    spec=importlib.util.spec_from_file_location('upgrade_preservation',Path(__file__).with_name('electron_upgrade_smoke.py'))
    harness=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    with sqlite3.connect(tmp_path/'workbench.sqlite3') as con:
        con.executescript(SCHEMA+AUTH_SCHEMA+SESSION_SCHEMA+'PRAGMA user_version=4;')
        con.execute("INSERT INTO datasets VALUES('legacy-data','saved research','fixture','{}')")
        con.execute("INSERT INTO jobs(id,platform,state,options,dataset_id,created,updated) VALUES('legacy','dy','completed','{}','legacy-data','fixture','fixture')")
    before=harness.snapshot(tmp_path)
    Database(tmp_path)
    return harness,tmp_path,before


def test_schema6_retry_migration_preserves_legacy_history(migrated):
    harness,data,before=migrated
    current=harness.preserved(data,before)
    assert current['rows']['job_retry_plans']==[]


def test_unrecognized_migration_table_is_still_rejected(migrated):
    harness,data,before=migrated
    with sqlite3.connect(data/'workbench.sqlite3') as con:
        con.execute('CREATE TABLE unexpected_business_table(id TEXT)')
    with pytest.raises(AssertionError,match='unrecognized business table'):
        harness.preserved(data,before)


def test_migration_cannot_enable_retry_for_historical_tasks(migrated):
    harness,data,before=migrated
    with sqlite3.connect(data/'workbench.sqlite3') as con:
        con.execute("INSERT INTO job_retry_plans VALUES('legacy',1,100,5400,7200,NULL,NULL,'',NULL,'fixture')")
    with pytest.raises(AssertionError,match='Historical tasks'):
        harness.preserved(data,before)
