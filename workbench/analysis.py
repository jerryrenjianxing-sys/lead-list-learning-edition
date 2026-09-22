import json
import jsonschema
from .database import Conflict, Missing, dump, digest, uid, now, record_view


def coverage(con, analysis_id):
    counts = {
        row["state"]: row["n"]
        for row in con.execute(
            "SELECT state,COUNT(*) n FROM analysis_inputs WHERE analysis_id=? GROUP BY state",
            (analysis_id,),
        )
    }
    return {
        "total": sum(counts.values()),
        **{k: counts.get(k, 0) for k in ("pending", "processed", "skipped", "failed")},
    }


def create(con, body):
    dataset = con.execute(
        "SELECT * FROM datasets WHERE id=?", (body["dataset_id"],)
    ).fetchone()
    if not dataset:
        raise Missing(body["dataset_id"])
    schema = body.get("result_schema")
    if schema is not None:
        jsonschema.Draft202012Validator.check_schema(schema)

        # External schema retrieval would make a local validation depend on the network.
        def check_refs(value):
            if isinstance(value, dict):
                for key, val in value.items():
                    if (
                        key in ("$ref", "$dynamicRef")
                        and isinstance(val, str)
                        and not val.startswith("#")
                    ):
                        raise ValueError(
                            "Use an embedded result schema; external schema references are not fetched"
                        )
                    check_refs(val)
            elif isinstance(value, list):
                for val in value:
                    check_refs(val)

        check_refs(schema)
    identity = uid()
    stamp = now()
    con.execute(
        "INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?,?)",
        (
            identity,
            body["dataset_id"],
            body["name"],
            body["goal"],
            dump(schema) if schema is not None else None,
            "waiting_agent",
            stamp,
            stamp,
            dump(body.get("metadata", {})),
        ),
    )
    selected = set(body["record_ids"]) if body.get("record_ids") is not None else None
    found = set()
    for row in con.execute(
        "SELECT * FROM records WHERE dataset_id=? ORDER BY ordinal",
        (body["dataset_id"],),
    ).fetchall():
        if selected is None or row["id"] in selected:
            found.add(row["id"])
            con.execute(
                "INSERT INTO analysis_inputs(analysis_id,record_id,ordinal,snapshot) VALUES(?,?,?,?)",
                (identity, row["id"], row["ordinal"], dump(record_view(row))),
            )
    if selected is not None and selected != found:
        raise ValueError("Selected record IDs must belong to this dataset")
    return {
        "id": identity,
        "state": "waiting_agent",
        "coverage": coverage(con, identity),
    }


def submit(con, analysis_id, body):
    analysis = con.execute(
        "SELECT * FROM analyses WHERE id=?", (analysis_id,)
    ).fetchone()
    if not analysis:
        raise Missing(analysis_id)
    fingerprint = digest(body)
    previous = con.execute(
        "SELECT * FROM batches WHERE analysis_id=? AND batch_id=?",
        (analysis_id, body["batch_id"]),
    ).fetchone()
    if previous:
        if previous["hash"] != fingerprint:
            raise Conflict("This batch ID already has different content")
        return json.loads(previous["response"])
    if analysis["state"] == "completed":
        raise Conflict(
            "Analysis is finalized; create another analysis for a new goal or revised results"
        )
    inputs = {
        row[0]
        for row in con.execute(
            "SELECT record_id FROM analysis_inputs WHERE analysis_id=?", (analysis_id,)
        )
    }
    assignments = [
        ("processed", {x: "" for x in body["processed_ids"]}),
        ("skipped", body["skipped"]),
        ("failed", body["failed"]),
    ]
    seen = set()
    for state, entries in assignments:
        if seen.intersection(entries):
            raise ValueError(
                "A record cannot have multiple processing states in one batch"
            )
        if not set(entries).issubset(inputs):
            raise ValueError(
                "Coverage record IDs must belong to this analysis snapshot"
            )
        seen.update(entries)
    schema = (
        json.loads(analysis["schema_json"])
        if analysis["schema_json"] is not None
        else None
    )
    written = []
    for result in body["results"]:
        evidence = list(dict.fromkeys(result["evidence_ids"]))
        if not set(evidence).issubset(inputs):
            raise ValueError("Evidence IDs must belong to this analysis snapshot")
        if schema is not None:
            jsonschema.Draft202012Validator(schema).validate(result["payload"])
        result_id = (
            result.get("id")
            or digest({"payload": result["payload"], "evidence": evidence})[:32]
        )
        payload = dump(result["payload"])
        old = con.execute(
            "SELECT payload,evidence FROM results WHERE analysis_id=? AND id=?",
            (analysis_id, result_id),
        ).fetchone()
        if old and (old[0] != payload or old[1] != dump(evidence)):
            raise Conflict(
                "A result ID already contains different data; submit a new result ID"
            )
        con.execute(
            "INSERT OR IGNORE INTO results VALUES(?,?,?,?,?)",
            (result_id, analysis_id, payload, dump(evidence), now()),
        )
        written.append(result_id)
    for state, entries in assignments:
        con.executemany(
            "UPDATE analysis_inputs SET state=?,note=? WHERE analysis_id=? AND record_id=?",
            (
                (state, note, analysis_id, record_id)
                for record_id, note in entries.items()
            ),
        )
    con.execute(
        "UPDATE analyses SET updated=?,state='waiting_agent' WHERE id=?",
        (now(), analysis_id),
    )
    response = {
        "analysis_id": analysis_id,
        "batch_id": body["batch_id"],
        "result_ids": written,
        "coverage": coverage(con, analysis_id),
    }
    con.execute(
        "INSERT INTO batches VALUES(?,?,?,?,?)",
        (analysis_id, body["batch_id"], fingerprint, dump(response), now()),
    )
    return response


def finish(con, analysis_id, allow_partial):
    if not con.execute("SELECT 1 FROM analyses WHERE id=?", (analysis_id,)).fetchone():
        raise Missing(analysis_id)
    progress = coverage(con, analysis_id)
    incomplete = progress["pending"] + progress["failed"] > 0
    if incomplete and not allow_partial:
        raise Conflict(
            "Analysis has pending or failed records; continue processing or explicitly finish a partial analysis"
        )
    state = "partial" if incomplete else "completed"
    con.execute(
        "UPDATE analyses SET state=?,updated=? WHERE id=?", (state, now(), analysis_id)
    )
    return {"id": analysis_id, "state": state, "coverage": progress}
