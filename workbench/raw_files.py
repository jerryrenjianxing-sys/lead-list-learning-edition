"""Raw files are rebuildable derivatives of committed SQLite record revisions."""

import json
import os
from .database import Conflict, now
from .database import worker_database
from .recovery import event


def report(commit, state):
    db = worker_database()
    with db.lock, db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        # Only this exact receipt is updated; a late writer cannot overwrite newer raw state.
        con.execute(
            "UPDATE raw_commits SET state=?,updated=? WHERE job_id=? AND attempt=? AND record_id=? AND revision=? AND state='pending'",
            (
                state,
                now(),
                commit["job_id"],
                commit["attempt"],
                commit["record_id"],
                commit["revision"],
            ),
        )
        event(
            con,
            commit["job_id"],
            commit["attempt"],
            "Raw file " + state,
            source="storage",
            kind="raw_" + state,
            details={"record_id": commit["record_id"], "revision": commit["revision"]},
        )
        con.commit()


class RawFiles:
    def __init__(self, db):
        self.db = db

    def status(self, job_id):
        self.db.one("jobs", job_id)
        rows = self.db.query(
            "SELECT state,count(*) n FROM raw_commits WHERE job_id=? GROUP BY state",
            (job_id,),
        )
        return {"counts": {r["state"]: r["n"] for r in rows}, "rebuildable": True}

    def rebuild(self, con, job_id):
        job = self.db.one("jobs", job_id)
        if job["state"] in ("running", "stopping", "queued"):
            raise Conflict("Stop this task before rebuilding its raw files")
        rows = con.execute(
            "SELECT DISTINCT r.record_id,r.revision,v.payload FROM raw_commits r JOIN raw_versions v ON v.record_id=r.record_id AND v.revision=r.revision WHERE r.job_id=?",
            (job_id,),
        ).fetchall()
        path = self.db.root / "runs" / job_id / "rebuilt.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                for row in rows:
                    stream.write(
                        json.dumps(
                            {
                                "record_id": row["record_id"],
                                "revision": row["revision"],
                                "payload": json.loads(row["payload"]),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        con.execute(
            "UPDATE raw_commits SET state='rebuilt',updated=? WHERE job_id=?",
            (now(), job_id),
        )
        event(
            con,
            job_id,
            job["attempt"],
            "Raw files rebuilt",
            source="storage",
            kind="raw_rebuilt",
            details={"records": len(rows)},
        )
        return {"records": len(rows), "state": "rebuilt"}
