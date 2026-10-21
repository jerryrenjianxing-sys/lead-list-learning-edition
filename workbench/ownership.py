"""One persisted fence for workers, account generations and terminal transitions."""

import hashlib
from .database import Conflict


def lease_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def valid_session(con, job_id, attempt):
    binding = con.execute(
        "SELECT e.generation,e.account_id,s.generation current_generation,s.account_id current_account FROM execution_sessions e JOIN jobs j ON j.id=e.job_id LEFT JOIN platform_sessions s ON s.platform=j.platform WHERE e.job_id=? AND e.attempt=?",
        (job_id, attempt),
    ).fetchone()
    return not binding or (
        binding["generation"] == binding["current_generation"]
        and binding["account_id"] == binding["current_account"]
    )


def worker_job(con, job_id, attempt, lease, platform=None):
    canonical = {
        "douyin": "dy",
        "xhs": "xhs",
        "xiaohongshu": "xhs",
        "kuaishou": "ks",
        "bilibili": "bili",
        "weibo": "wb",
        "tieba": "tieba",
        "zhihu": "zhihu",
    }.get(platform, platform)
    job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if (
        not job
        or not str(attempt).isdigit()
        or job["attempt"] != int(attempt)
        or job["state"] != "running"
        or canonical
        and job["platform"] != canonical
    ):
        raise Conflict("This task attempt no longer owns writes")
    binding = con.execute(
        "SELECT * FROM execution_sessions WHERE job_id=? AND attempt=?",
        (job_id, int(attempt)),
    ).fetchone()
    if binding:
        if (
            not lease
            or lease.get("job_id") != job_id
            or lease.get("attempt") != int(attempt)
            or lease_hash(lease.get("lease", "")) != binding["lease_hash"]
            or not valid_session(con, job_id, int(attempt))
        ):
            raise Conflict("The account session has changed")
    elif lease:
        raise Conflict("This browser lease is not registered")
    return job
