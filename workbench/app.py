from __future__ import annotations
import csv
import hashlib
import io
import json
import secrets
import sys
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
import jsonschema
from fastapi import FastAPI, Request, UploadFile, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from . import __version__, analysis
from .database import (
    Database,
    Conflict,
    Missing,
    dump,
    uid,
    now,
    insert_records,
    record_view,
)
from .exports import export_analysis, save_artifact
from .models import (
    CrawlRequest,
    DatasetRequest,
    AnalysisRequest,
    BatchRequest,
    FinishRequest,
    ExportRequest,
    JobHistoryRequest,
)
from .paths import ROOT, PRODUCT, DISPLAY_NAME, data_root
from .queue import Queue, PLATFORMS, create_job, control, job_view, clear_history
from .auth import platform_view
from .sessions import SessionManager

SKILL = ROOT / "skills" / "media-workbench"


def parse_import(name, content):
    suffix = Path(name).suffix.lower()
    if suffix == ".csv":
        return list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    if suffix == ".xlsx":
        from openpyxl import load_workbook

        book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        try:
            rows = iter(book.active.values)
            header = [str(x or f"column_{i + 1}") for i, x in enumerate(next(rows, []))]
            return [
                dict(
                    zip(
                        header,
                        [x.isoformat() if hasattr(x, "isoformat") else x for x in row],
                    )
                )
                for row in rows
            ]
        finally:
            book.close()
    text = content.decode("utf-8-sig")
    if suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if suffix == ".json":
        result = json.loads(text)
        if isinstance(result, dict) and isinstance(result.get("records"), list):
            return result["records"]
        return result if isinstance(result, list) else [result]
    raise ValueError(
        "Import JSON, JSONL, CSV, or XLSX; other analysis outputs can be attached as artifacts"
    )


def make_app(root=None, *, run_queue=True):
    root = Path(root or data_root()).resolve()
    db = Database(root)
    sessions = SessionManager(db)
    queue = Queue(db, sessions=sessions if run_queue else None)
    key_file = root / ".access-token"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(32), encoding="ascii")
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
    token = key_file.read_text(encoding="ascii").strip()
    sessions.token = token

    @asynccontextmanager
    async def lifespan(app):
        if run_queue:
            queue.start()
        yield
        queue.close()
        sessions.close()

    app = FastAPI(title=DISPLAY_NAME, version=__version__, lifespan=lifespan)
    app.state.db, app.state.queue, app.state.token = db, queue, token
    app.state.sessions = sessions
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
        allow_credentials=True,
    )

    @app.middleware("http")
    async def local_access(request, call_next):
        if request.url.hostname not in ("127.0.0.1", "localhost", "::1", "testserver"):
            return JSONResponse(
                {"detail": "Use the local application address"}, status_code=403
            )
        origin = request.headers.get("origin")
        if (
            origin
            and origin != str(request.base_url).rstrip("/")
            and origin not in ("http://127.0.0.1:5173", "http://localhost:5173")
        ):
            return JSONResponse(
                {"detail": "Origin is not this local application"}, status_code=403
            )
        public = request.url.path in (
            "/api/v1/health",
            "/api/v1/session",
            "/api/v1/capabilities",
            "/api/platform-skill",
            "/api/v1/skill",
            "/api/health",
        )
        if (
            request.url.path.startswith("/api/")
            and not public
            and request.method != "OPTIONS"
        ):
            supplied = request.headers.get("authorization", "").removeprefix(
                "Bearer "
            ) or request.cookies.get("workbench_session", "")
            if not secrets.compare_digest(supplied, token):
                return JSONResponse(
                    {
                        "detail": "Connect through the local client or reload the workbench"
                    },
                    status_code=401,
                )
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(Missing)
    async def missing(_, exc):
        return JSONResponse({"detail": f"Not found: {exc.args[0]}"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(jsonschema.ValidationError)
    @app.exception_handler(jsonschema.SchemaError)
    async def invalid_schema(_, exc):
        return JSONResponse({"detail": exc.message}, status_code=422)

    def write(request, body, operation, scope=None):
        return db.write(
            request.headers.get("idempotency-key"),
            scope or f"{request.method} {request.url.path}",
            body,
            operation,
        )

    @app.get("/api/health")
    @app.get("/api/v1/health")
    def health():
        return {
            "product": PRODUCT,
            "display_name": DISPLAY_NAME,
            "version": __version__,
            "api_version": 1,
            "status": "ok",
        }

    @app.get("/api/v1/session")
    def session():
        response = JSONResponse({**health(), "token": token})
        response.set_cookie(
            "workbench_session", token, httponly=True, samesite="strict"
        )
        return response

    @app.get("/api/v1/capabilities")
    def capabilities():
        return {
            **health(),
            "platforms": [
                {
                    "id": p,
                    "name": name,
                    "modes": ["search", "detail", "creator", "login"],
                    "comments": True,
                    "enrich_profiles": p in ("xhs", "dy", "ks"),
                    "media": False,
                    "live_validation": "pending",
                }
                for p, name in PLATFORMS.items()
            ],
            "operations": [
                "jobs",
                "datasets",
                "analyses",
                "artifacts",
                "settings",
                "diagnostics",
                "imports",
            ],
            "analysis": "user_defined",
            "exports": ["csv", "json", "xlsx", "docx", "md"],
            "openapi": "/openapi.json",
            "agent_compatibility": {
                "codex": "fresh_agent_validation_pending",
                "hermes": "fresh_agent_validation_pending",
            },
            "resume_strategy": "Saved records are deduplicated on replay; analysis snapshots and coverage persist.",
        }

    @app.get("/api/v1/skill")
    @app.get("/api/platform-skill")
    def skill(request: Request, format: str = "zip"):
        if format == "connect":
            address = str(request.base_url).rstrip("/")
            return PlainTextResponse(
                f"请使用Media Deep Researcher处理我的任务。它已在本机运行：{address}。下载 {address}/api/v1/skill 的完整 ZIP，解压后读取 media-workbench/SKILL.md，使用随包客户端连接。先运行 scripts/workbench.ps1 check（Windows），或 python scripts/workbench.py check。按我的目标自行决定分析方法、字段和输出形式，进度与成果保存回软件。连接失败时报告实际原因。",
                media_type="text/plain",
            )
        paths = sorted(
            p
            for p in SKILL.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        )
        if format == "markdown":
            parts = [(SKILL / "SKILL.md").read_text(encoding="utf-8")]
            for file in paths:
                if file.name != "SKILL.md":
                    parts.append(
                        f"\n## File: {file.relative_to(SKILL).as_posix()}\n````\n{file.read_text(encoding='utf-8')}\n````"
                    )
            return PlainTextResponse("\n".join(parts), media_type="text/markdown")
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in paths:
                info = zipfile.ZipInfo(
                    "media-workbench/" + file.relative_to(SKILL).as_posix(),
                    date_time=(2026, 9, 22, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, file.read_bytes())
        return Response(
            out.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": "attachment; filename=MediaWorkbench-Skill.zip"
            },
        )

    @app.get("/api/v1/requests/{request_id}")
    def receipt(request_id: str):
        rows = db.query(
            "SELECT response,scope,created FROM receipts WHERE key=?", (request_id,)
        )
        if not rows:
            raise Missing(request_id)
        return {
            "request_id": request_id,
            "response": json.loads(rows[0]["response"]),
            "scope": rows[0]["scope"],
        }

    @app.get("/api/v1/jobs")
    def jobs(include_archived: bool = False):
        return {
            "items": [
                job_view(row)
                for row in db.query(
                    "SELECT j.*,a.archived_at FROM jobs j LEFT JOIN job_history_archive a ON a.job_id=j.id "
                    + ("" if include_archived else "WHERE a.job_id IS NULL ")
                    + "ORDER BY j.created DESC"
                )
            ],
            "archived_count": db.query("SELECT COUNT(*) n FROM job_history_archive")[0][
                "n"
            ],
        }

    @app.post("/api/v1/jobs/clear-history")
    def hide_history(body: JobHistoryRequest, request: Request):
        return write(
            request, body.model_dump(), lambda con: clear_history(con, body.ids)
        )

    @app.post("/api/v1/jobs/restore-history")
    def restore_history(body: JobHistoryRequest, request: Request):
        return write(
            request,
            body.model_dump(),
            lambda con: clear_history(con, body.ids, restore=True),
        )

    @app.post("/api/v1/jobs")
    def start_job(body: CrawlRequest, request: Request):
        options = body.model_dump()
        return write(request, options, lambda con: create_job(con, options))

    @app.get("/api/v1/jobs/{job_id}")
    def job(job_id: str):
        return job_view(db.one("jobs", job_id))

    @app.post("/api/v1/jobs/{job_id}/{action}")
    def job_control(job_id: str, action: str, request: Request):
        if action not in ("stop", "resume", "retry"):
            raise HTTPException(404)
        return write(request, {}, lambda con: control(con, job_id, action))

    @app.get("/api/v1/jobs/{job_id}/events")
    def events(job_id: str, after: int = 0, limit: int = Query(200, ge=1, le=2000)):
        db.one("jobs", job_id)
        return {
            "items": db.query(
                "SELECT * FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?",
                (job_id, after, limit),
            )
        }

    @app.get("/api/v1/jobs/{job_id}/qrcode")
    def qrcode(job_id: str):
        db.one("jobs", job_id)
        file = root / "runs" / job_id / ".login_qrcode.png"
        if not file.is_file():
            raise HTTPException(
                404, "No QR code currently available; inspect task events or browser"
            )
        return FileResponse(file, media_type="image/png")

    @app.get("/api/v1/platforms")
    def platforms():
        items = []
        for p in capabilities()["platforms"]:
            last = db.query(
                "SELECT id,state,updated,error FROM jobs WHERE platform=? ORDER BY updated DESC LIMIT 1",
                (p["id"],),
            )
            items.append(
                {
                    **p,
                    "saved_session": any((root / "browser_data").glob(f"*{p['id']}_*")),
                    **platform_view(db, p["id"]),
                    **sessions.store.view(p["id"]),
                    "last_task": last[0] if last else None,
                }
            )
        return {"items": items}

    @app.post("/api/v1/internal/session", include_in_schema=False)
    def session_command(body: dict):
        return sessions.call("command", **body)

    @app.post("/api/v1/platforms/{platform}/session/{action}")
    def platform_action(platform: str, action: str, request: Request):
        if platform not in PLATFORMS:
            raise HTTPException(404)
        if action == "forget":
            return sessions.call("forget", platform=platform)
        if action not in ("check", "open", "login"):
            raise HTTPException(404)
        options = CrawlRequest(
            platform=platform, crawler_type="login", session_action=action
        ).model_dump()
        return write(request, options, lambda con: create_job(con, options))

    @app.get("/api/v1/datasets")
    def datasets():
        return {
            "items": [
                {**r, "metadata": json.loads(r["metadata"])}
                for r in db.query(
                    "SELECT d.*,COUNT(r.id) record_count FROM datasets d LEFT JOIN records r ON d.id=r.dataset_id GROUP BY d.id ORDER BY d.created DESC"
                )
            ]
        }

    @app.post("/api/v1/datasets")
    def add_dataset(body: DatasetRequest, request: Request):
        def operation(con):
            identity = uid()
            con.execute(
                "INSERT INTO datasets VALUES(?,?,?,?)",
                (identity, body.name, now(), dump(body.metadata)),
            )
            return {"id": identity, **insert_records(con, identity, body.records)}

        return write(request, body.model_dump(), operation)

    @app.post("/api/v1/datasets/{dataset_id}/records")
    def add_records(dataset_id: str, body: dict, request: Request):
        if not isinstance(body.get("records"), list):
            raise ValueError("records must be an array of JSON objects")
        return write(
            request, body, lambda con: insert_records(con, dataset_id, body["records"])
        )

    @app.get("/api/v1/datasets/{dataset_id}/records")
    def records(
        dataset_id: str,
        after: int = 0,
        limit: int = Query(100, ge=1, le=2000),
        q: str = "",
        kind: str = "",
    ):
        db.one("datasets", dataset_id)
        where = "dataset_id=? AND ordinal>?"
        args = [dataset_id, after]
        if q:
            where += " AND instr(payload,?)>0"
            args.append(q)
        if kind:
            where += " AND kind=?"
            args.append(kind)
        rows = db.query(
            f"SELECT * FROM records WHERE {where} ORDER BY ordinal LIMIT ?",
            (*args, limit + 1),
        )
        return {
            "items": [record_view(r) for r in rows[:limit]],
            "next_after": rows[limit - 1]["ordinal"] if len(rows) > limit else None,
        }

    @app.get("/api/v1/datasets/{dataset_id}/stats")
    def stats(dataset_id: str):
        dataset = db.one("datasets", dataset_id)
        return {
            **dataset,
            "groups": db.query(
                "SELECT platform,kind,COUNT(*) count FROM records WHERE dataset_id=? GROUP BY platform,kind",
                (dataset_id,),
            ),
        }

    @app.post("/api/v1/imports/file")
    async def import_file(request: Request, file: UploadFile):
        content = await file.read()
        rows = parse_import(file.filename or "data.json", content)

        def operation(con):
            identity = uid()
            con.execute(
                "INSERT INTO datasets VALUES(?,?,?,?)",
                (
                    identity,
                    file.filename or "Import",
                    now(),
                    dump({"source_sha256": hashlib.sha256(content).hexdigest()}),
                ),
            )
            return {"id": identity, **insert_records(con, identity, rows)}

        return write(
            request,
            {"name": file.filename, "hash": hashlib.sha256(content).hexdigest()},
            operation,
        )

    @app.post("/api/v1/imports/legacy")
    def import_legacy(body: dict, request: Request):
        directory = Path(body["path"]).resolve()
        if not directory.is_dir():
            raise ValueError("Select an existing legacy data directory")
        files = sorted(
            p
            for p in directory.rglob("*")
            if p.is_file()
            and p.suffix.lower() in (".json", ".jsonl", ".csv", ".xlsx")
            and not p.name.startswith(".")
        )
        loaded = []
        errors = []
        for file in files:
            try:
                loaded.append((file, parse_import(file.name, file.read_bytes())))
            except Exception as exc:
                errors.append(
                    {"file": str(file.relative_to(directory)), "error": str(exc)}
                )

        def operation(con):
            identity = uid()
            con.execute(
                "INSERT INTO datasets VALUES(?,?,?,?)",
                (
                    identity,
                    body.get("name", directory.name),
                    now(),
                    dump({"legacy_path": str(directory), "errors": errors}),
                ),
            )
            count = 0
            for file, rows in loaded:
                platform = next(
                    (p for p in file.relative_to(directory).parts if p in PLATFORMS),
                    "import",
                )
                kind = "comments" if "comment" in file.name else "contents"
                count += insert_records(
                    con, identity, rows, platform=platform, kind=kind
                )["inserted"]
            return {
                "id": identity,
                "inserted": count,
                "files": len(loaded),
                "errors": errors,
            }

        return write(request, body, operation)

    @app.post("/api/v1/demo")
    def demo(request: Request):
        body = json.loads(
            (ROOT / "examples" / "demo-data.json").read_text(encoding="utf-8")
        )
        return add_dataset(DatasetRequest(**body), request)

    @app.get("/api/v1/analyses")
    def analyses():
        return {
            "items": [
                analysis_detail(r["id"])
                for r in db.query("SELECT id FROM analyses ORDER BY created DESC")
            ]
        }

    @app.post("/api/v1/analyses")
    def start_analysis(body: AnalysisRequest, request: Request):
        return write(
            request,
            body.model_dump(),
            lambda con: analysis.create(con, body.model_dump()),
        )

    @app.get("/api/v1/analyses/{analysis_id}")
    def analysis_detail(analysis_id: str):
        row = db.one("analyses", analysis_id)
        with db.connect() as con:
            progress = analysis.coverage(con, analysis_id)
        schema = row.pop("schema_json")
        return {
            **row,
            "result_schema": json.loads(schema) if schema is not None else None,
            "metadata": json.loads(row["metadata"]),
            "coverage": progress,
        }

    @app.get("/api/v1/analyses/{analysis_id}/inputs")
    def inputs(
        analysis_id: str,
        after: int = 0,
        limit: int = Query(100, ge=1, le=2000),
        state: str = "",
    ):
        db.one("analyses", analysis_id)
        sql = "SELECT * FROM analysis_inputs WHERE analysis_id=? AND ordinal>?"
        args = [analysis_id, after]
        if state:
            sql += " AND state=?"
            args.append(state)
        rows = db.query(sql + " ORDER BY ordinal LIMIT ?", (*args, limit + 1))
        return {
            "items": [
                {**json.loads(r["snapshot"]), "state": r["state"], "note": r["note"]}
                for r in rows[:limit]
            ],
            "next_after": rows[limit - 1]["ordinal"] if len(rows) > limit else None,
        }

    @app.post("/api/v1/analyses/{analysis_id}/batches")
    def submit_batch(analysis_id: str, body: BatchRequest, request: Request):
        return write(
            request,
            body.model_dump(),
            lambda con: analysis.submit(con, analysis_id, body.model_dump()),
        )

    @app.post("/api/v1/analyses/{analysis_id}/finish")
    def finish(analysis_id: str, body: FinishRequest, request: Request):
        return write(
            request,
            body.model_dump(),
            lambda con: analysis.finish(con, analysis_id, body.allow_partial),
        )

    @app.get("/api/v1/analyses/{analysis_id}/results")
    def results(
        analysis_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=2000),
    ):
        db.one("analyses", analysis_id)
        rows = db.query(
            "SELECT * FROM results WHERE analysis_id=? ORDER BY created,id LIMIT ? OFFSET ?",
            (analysis_id, limit + 1, offset),
        )
        return {
            "items": [
                {
                    "id": r["id"],
                    "payload": json.loads(r["payload"]),
                    "evidence_ids": json.loads(r["evidence"]),
                }
                for r in rows[:limit]
            ],
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    @app.post("/api/v1/analyses/{analysis_id}/export")
    def export(analysis_id: str, body: ExportRequest, request: Request):
        db.one("analyses", analysis_id)
        return write(
            request,
            body.model_dump(),
            lambda con: export_analysis(con, root, analysis_id, body.format),
        )

    @app.post("/api/v1/analyses/{analysis_id}/attachments")
    async def attach(analysis_id: str, request: Request, file: UploadFile):
        db.one("analyses", analysis_id)
        content = await file.read()
        return write(
            request,
            {"name": file.filename, "hash": hashlib.sha256(content).hexdigest()},
            lambda con: save_artifact(
                con,
                root,
                analysis_id,
                file.filename or "attachment",
                file.content_type or "application/octet-stream",
                content,
            ),
        )

    @app.get("/api/v1/artifacts")
    def artifacts():
        return {
            "items": [
                {**r, "download_url": f"/api/v1/artifacts/{r['id']}/download"}
                for r in db.query("SELECT * FROM artifacts ORDER BY created DESC")
            ]
        }

    @app.get("/api/v1/artifacts/{artifact_id}/download")
    def download(artifact_id: str):
        row = db.one("artifacts", artifact_id)
        return FileResponse(
            root / row["relative_path"], media_type=row["mime"], filename=row["name"]
        )

    @app.get("/api/v1/settings")
    def settings():
        return {
            "data_directory": str(root),
            "browser_path": "",
            **{
                r["key"]: json.loads(r["value"])
                for r in db.query("SELECT * FROM settings")
            },
        }

    @app.put("/api/v1/settings")
    def set_settings(body: dict, request: Request):
        if set(body) - {"browser_path", "update_feed", "theme"}:
            raise ValueError("Supported settings: browser_path, update_feed, theme")
        if body.get("browser_path") and not Path(body["browser_path"]).is_file():
            raise ValueError("Browser executable does not exist")

        def operation(con):
            con.executemany(
                "INSERT OR REPLACE INTO settings VALUES(?,?)",
                ((k, dump(v)) for k, v in body.items()),
            )
            return body

        return write(request, body, operation)

    @app.get("/api/v1/diagnostics")
    def diagnostics():
        return {
            **health(),
            "python": sys.version.split()[0],
            "data_directory": str(root),
            "queue_alive": bool(queue.thread and queue.thread.is_alive()),
            "active_jobs": db.query(
                "SELECT id,state,platform FROM jobs WHERE state IN ('running','stopping','queued')"
            ),
            "database": db.query("PRAGMA quick_check")[0],
            "upstream_revision": "380b426000aac3d612837ed72c99808347dc94c9",
        }

    @app.post("/api/v1/backup")
    def backup():
        return {"path": str(db.backup())}

    @app.post("/api/v1/host/stop")
    def stop_host():
        queue.stop_event.set()
        app.state.stop_requested = True
        return {"state": "stopping"}

    @app.post("/api/v1/host/prepare-update")
    def prepare_update():
        # Serialize against job submission and queue claims, then freeze all writes.
        with db.lock:
            if db.query(
                "SELECT id FROM jobs WHERE state IN ('running','stopping','queued') LIMIT 1"
            ):
                raise Conflict(
                    "Collection tasks are still active; apply the update when idle"
                )
            backup_path = db.backup()
            db.accept_writes = False
            queue.stop_event.set()
            app.state.stop_requested = True
            return {"state": "stopping", "backup": str(backup_path)}

    # The legacy surface delegates to this same durable queue; no second process manager.
    from .legacy import register_legacy

    register_legacy(app, db, root, start_job, job_control, job, qrcode)
    web = ROOT / "api" / "webui"
    if web.exists():
        app.mount("/", StaticFiles(directory=web, html=True), name="frontend")
    return app
