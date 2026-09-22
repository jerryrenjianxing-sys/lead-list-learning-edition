"""Compatibility endpoints backed by the v1 queue and data directory."""

import json
from pathlib import Path
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from .models import CrawlRequest


def register_legacy(app, db, root, start_job, job_control, job, qrcode):
    def current(task_id=None):
        if task_id:
            aliases = db.query(
                "SELECT response FROM receipts WHERE key=?", ("legacy-task-" + task_id,)
            )
            if aliases:
                return job(json.loads(aliases[0]["response"])["id"])
            return job(task_id)
        rows = db.query(
            "SELECT id FROM jobs ORDER BY CASE WHEN state IN ('running','stopping') THEN 0 WHEN state='queued' THEN 1 ELSE 2 END,created DESC LIMIT 1"
        )
        return job(rows[0]["id"]) if rows else None

    @app.post("/api/crawler/start")
    def legacy_start(body: dict, request: Request):
        original_task_id = body.get("task_id")
        normalized = {k: v for k, v in body.items() if k in CrawlRequest.model_fields}
        normalized["enrich_profiles"] = body.get(
            "lead_mode", body.get("enrich_profiles", False)
        )
        for k in ("max_notes_count", "max_comments_count"):
            if normalized.get(k) is None:
                normalized[k] = 0
        if original_task_id:
            headers = [
                (k, v) for k, v in request.scope["headers"] if k != b"idempotency-key"
            ]
            request = Request(
                {
                    **request.scope,
                    "headers": headers
                    + [
                        (
                            b"idempotency-key",
                            ("legacy-task-" + original_task_id).encode(),
                        )
                    ],
                }
            )
        result = start_job(CrawlRequest(**normalized), request)
        return {
            "status": "ok",
            "message": "Crawler queued",
            "task_id": original_task_id or result["id"],
            "job_id": result["id"],
        }

    @app.post("/api/crawler/stop")
    def legacy_stop(body: dict, request: Request):
        selected = current(body.get("task_id"))
        if not selected:
            raise HTTPException(404)
        result = job_control(selected["id"], "stop", request)
        return {
            **result,
            "status": "ok",
            "task_id": body.get("task_id", selected["id"]),
        }

    @app.get("/api/crawler/status")
    def legacy_status(task_id: str | None = None):
        selected = current(task_id)
        if not selected:
            return {"status": "idle", "task_id": None}
        state = selected["state"]
        status = (
            "running"
            if state in ("queued", "running")
            else "stopping"
            if state == "stopping"
            else "error"
            if state in ("failed", "partial", "needs_login")
            else "idle"
        )
        return {
            "status": status,
            "task_id": task_id or selected["id"],
            "job_id": selected["id"],
            "platform": selected["platform"],
            "crawler_type": selected["options"]["crawler_type"],
            "started_at": selected["created"],
            "error_message": selected["error"],
        }

    @app.get("/api/crawler/logs")
    def legacy_logs(limit: int = 100):
        rows = db.query(
            "SELECT seq id,created timestamp,level,message FROM events ORDER BY seq DESC LIMIT ?",
            (max(1, min(limit, 2000)),),
        )
        return {"logs": list(reversed(rows))}

    @app.get("/api/crawler/qrcode")
    def legacy_qr():
        selected = current()
        if not selected:
            raise HTTPException(404)
        return qrcode(selected["id"])

    @app.post("/api/crawler/qrcode/refresh")
    def refresh():
        selected = current()
        if not selected:
            raise HTTPException(404)
        path = root / "runs" / selected["id"] / ".login_qrcode_refresh"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return {"status": "ok"}

    @app.post("/api/crawler/recover-xhs/{task_id}")
    @app.post("/api/crawler/recover-douyin-profiles/{task_id}")
    @app.post("/api/crawler/recover-profiles/{task_id}")
    def recover(task_id: str, request: Request):
        return job_control(current(task_id)["id"], "resume", request)

    directory = root / "runs"

    def safe_path(relative):
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise HTTPException(403, "Outside task data directory")
        if not path.is_file():
            raise HTTPException(404)
        return path

    @app.get("/api/data/files")
    def files(platform: str = "", file_type: str = ""):
        return {
            "files": [
                {
                    "name": p.name,
                    "path": p.relative_to(directory).as_posix(),
                    "size": p.stat().st_size,
                    "modified_at": p.stat().st_mtime,
                }
                for p in directory.rglob("*")
                if p.is_file()
                and p.suffix in (".json", ".jsonl", ".csv", ".xlsx")
                and (not platform or platform in p.parts)
                and (not file_type or p.suffix == "." + file_type)
            ]
        }

    @app.get("/api/data/download/{relative:path}")
    def download(relative: str):
        return FileResponse(safe_path(relative), filename=Path(relative).name)

    @app.get("/api/data/files/{relative:path}")
    def preview(relative: str, limit: int = 100):
        from .app import parse_import

        path = safe_path(relative)
        rows = parse_import(path.name, path.read_bytes())
        return {"data": rows[: max(0, limit)], "total": len(rows)}

    @app.get("/api/data/prompt")
    def prompt():
        return PlainTextResponse(
            "使用Media Deep Researcher Skill 连接本地服务；由用户定义分析目标、方法、字段与输出形式。",
            media_type="text/plain",
        )

    @app.get("/api/data/stats")
    def stats():
        return {
            "total_files": len(files()["files"]),
            "total_size": sum(f["size"] for f in files()["files"]),
        }
