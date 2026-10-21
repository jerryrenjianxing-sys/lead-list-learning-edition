"""Additive schema for auditable collection attempts and recovery operations."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_attempts(
 job_id TEXT NOT NULL REFERENCES jobs(id), attempt INTEGER NOT NULL,
 state TEXT NOT NULL, started TEXT, finished TEXT,
 checkpoint TEXT NOT NULL DEFAULT '{}', origin TEXT NOT NULL DEFAULT 'live', PRIMARY KEY(job_id,attempt));
INSERT OR IGNORE INTO job_attempts(job_id,attempt,state,finished,checkpoint,origin)
 SELECT id,attempt,state,updated,checkpoint,'legacy_snapshot' FROM jobs WHERE attempt>0;
CREATE TABLE IF NOT EXISTS attempt_metrics(
 job_id TEXT NOT NULL REFERENCES jobs(id), attempt INTEGER NOT NULL,
 received INTEGER NOT NULL DEFAULT 0, inserted INTEGER NOT NULL DEFAULT 0,
 updated INTEGER NOT NULL DEFAULT 0, unchanged INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(job_id,attempt));
CREATE TABLE IF NOT EXISTS event_details(
 seq INTEGER PRIMARY KEY REFERENCES events(seq), attempt INTEGER,
 source TEXT NOT NULL, capability TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT '',
 kind TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS event_attempt ON event_details(attempt,seq);
CREATE INDEX IF NOT EXISTS events_job_seq ON events(job_id,seq);
CREATE TABLE IF NOT EXISTS operations(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, platform TEXT NOT NULL,
 capability TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
 job_id TEXT REFERENCES jobs(id), attempt INTEGER NOT NULL DEFAULT 0,
 created TEXT NOT NULL, updated TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS operation_job ON operations(job_id,state);
CREATE TABLE IF NOT EXISTS collection_scopes(
 job_id TEXT NOT NULL REFERENCES jobs(id), attempt INTEGER NOT NULL,
 capability TEXT NOT NULL, scope TEXT NOT NULL, origin TEXT NOT NULL,
 outcome TEXT NOT NULL, reason TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
 evidence_seq INTEGER REFERENCES events(seq), PRIMARY KEY(job_id,attempt,capability,scope));
CREATE TABLE IF NOT EXISTS raw_commits(
 job_id TEXT NOT NULL REFERENCES jobs(id), attempt INTEGER NOT NULL,
 record_id TEXT NOT NULL REFERENCES records(id), revision TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', updated TEXT NOT NULL,
 PRIMARY KEY(job_id,attempt,record_id,revision));
CREATE TABLE IF NOT EXISTS access_checks(
 operation_id TEXT PRIMARY KEY REFERENCES operations(id), environment TEXT NOT NULL,
 generation INTEGER NOT NULL, account_id TEXT, source_job TEXT REFERENCES jobs(id),
 source_attempt INTEGER, automatic INTEGER NOT NULL DEFAULT 0,
 control TEXT NOT NULL DEFAULT '{}', original_reason TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX IF NOT EXISTS access_singleflight ON access_checks(environment,operation_id);
CREATE TABLE IF NOT EXISTS access_state(
 platform TEXT NOT NULL, account_id TEXT NOT NULL, capability TEXT NOT NULL,
 state TEXT NOT NULL, reason TEXT NOT NULL, cooldown_until REAL,
 checks INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL, operation_id TEXT,
 PRIMARY KEY(platform,account_id,capability));
CREATE TABLE IF NOT EXISTS access_waits(
 job_id TEXT NOT NULL REFERENCES jobs(id), capability TEXT NOT NULL, account_id TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, PRIMARY KEY(job_id,capability));
CREATE TABLE IF NOT EXISTS access_samples(
 platform TEXT NOT NULL, account_id TEXT NOT NULL, capability TEXT NOT NULL,
 parameters TEXT NOT NULL, verified TEXT NOT NULL,
 PRIMARY KEY(platform,account_id,capability));
CREATE TABLE IF NOT EXISTS scope_metrics(
 job_id TEXT NOT NULL REFERENCES jobs(id), attempt INTEGER NOT NULL,
 capability TEXT NOT NULL, scope TEXT NOT NULL,
 received INTEGER NOT NULL DEFAULT 0, inserted INTEGER NOT NULL DEFAULT 0,
 updated INTEGER NOT NULL DEFAULT 0, unchanged INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(job_id,attempt,capability,scope));
CREATE TABLE IF NOT EXISTS scope_records(
 job_id TEXT NOT NULL REFERENCES jobs(id), attempt INTEGER NOT NULL,
 capability TEXT NOT NULL, scope TEXT NOT NULL, record_id TEXT NOT NULL REFERENCES records(id),
 PRIMARY KEY(job_id,attempt,capability,scope,record_id));
CREATE TABLE IF NOT EXISTS execution_sessions(
 job_id TEXT NOT NULL REFERENCES jobs(id), attempt INTEGER NOT NULL,
 generation INTEGER NOT NULL, account_id TEXT, lease_hash TEXT NOT NULL,
 PRIMARY KEY(job_id,attempt));
CREATE TABLE IF NOT EXISTS access_replays(
 job_id TEXT NOT NULL REFERENCES jobs(id), capability TEXT NOT NULL, generation INTEGER NOT NULL,
 operation_id TEXT, created TEXT NOT NULL, PRIMARY KEY(job_id,capability,generation));
"""
