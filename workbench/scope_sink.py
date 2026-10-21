"""Fenced structural facts from inherited platform collectors; no response bodies."""

from .database import Conflict
from .queue import _collector_databases
from .paths import data_root
from .database import Database
from .recovery import scope_fact
import json
import os


def persist(item):
    root = data_root()
    if str(root) not in _collector_databases:
        _collector_databases[str(root)] = Database(root)
    db = _collector_databases[str(root)]
    with db.lock, db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        lease = json.loads(os.environ.get("MEDIAWORKBENCH_SESSION", "{}"))
        from .ownership import worker_job

        worker_job(con, item["job_id"], item["attempt"], lease)
        reason = scope_fact(
            con,
            item["job_id"],
            item["attempt"],
            item["stage"],
            item["scope"],
            item["reason"],
            {k: item[k] for k in ("page", "response") if k in item},
        )
        con.commit()
        return reason


def save_sample(platform, capability, parameters):
    from .database import dump, now

    root = data_root()
    db = _collector_databases[str(root)]
    with db.lock, db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        job_id = os.environ["MEDIAWORKBENCH_JOB_ID"]
        attempt = int(os.environ["MEDIAWORKBENCH_ATTEMPT"])
        from .ownership import worker_job

        worker_job(
            con,
            job_id,
            attempt,
            json.loads(os.environ.get("MEDIAWORKBENCH_SESSION", "{}")),
            platform,
        )
        if parameters:
            session = con.execute(
                "SELECT account_id FROM platform_sessions WHERE platform=?", (platform,)
            ).fetchone()
            con.execute(
                "INSERT INTO access_samples VALUES(?,?,?,?,?) ON CONFLICT(platform,account_id,capability) DO UPDATE SET parameters=excluded.parameters,verified=excluded.verified",
                (
                    platform,
                    session[0] if session and session[0] else "",
                    capability,
                    dump(parameters),
                    now(),
                ),
            )
        con.commit()
