"""Workspace read projections shared by the existing HTTP interfaces."""
from __future__ import annotations

import json

from . import analysis
from .auth import platform_view
from .queue import job_view


class WorkspaceReadModel:
    def __init__(self, db, sessions, platform_capabilities):
        self.db = db
        self.sessions = sessions
        self.platform_capabilities = platform_capabilities

    def jobs(self, include_archived=False):
        rows = self.db.query(
            "SELECT j.*,a.archived_at FROM jobs j LEFT JOIN job_history_archive a ON a.job_id=j.id "
            + ("" if include_archived else "WHERE a.job_id IS NULL ")
            + "ORDER BY j.created DESC"
        )
        return {
            "items": [job_view(row) for row in rows],
            "archived_count": self.db.query("SELECT COUNT(*) n FROM job_history_archive")[0]["n"],
        }

    def platforms(self):
        items = []
        for platform in self.platform_capabilities()["platforms"]:
            identity = platform["id"]
            last = self.db.query(
                "SELECT id,state,updated,error FROM jobs WHERE platform=? ORDER BY updated DESC LIMIT 1",
                (identity,),
            )
            items.append({
                **platform,
                "saved_session": any((self.db.root / "browser_data").glob(f"*{identity}_*")),
                **platform_view(self.db, identity),
                **self.sessions.store.view(identity),
                "last_task": last[0] if last else None,
            })
        return {"items": items}

    def datasets(self):
        return {"items": [
            {**row, "metadata": json.loads(row["metadata"])}
            for row in self.db.query(
                "SELECT d.*,COUNT(r.id) record_count FROM datasets d LEFT JOIN records r ON d.id=r.dataset_id "
                "GROUP BY d.id ORDER BY d.created DESC"
            )
        ]}

    def analysis(self, identity):
        row = self.db.one("analyses", identity)
        with self.db.connect() as connection:
            coverage = analysis.coverage(connection, identity)
        schema = row.pop("schema_json")
        return {
            **row, "result_schema": json.loads(schema) if schema is not None else None,
            "metadata": json.loads(row["metadata"]), "coverage": coverage,
        }

    def analyses(self):
        return {"items": [self.analysis(row["id"]) for row in self.db.query("SELECT id FROM analyses ORDER BY created DESC")]}

    def artifacts(self):
        return {"items": [
            {**row, "download_url": f"/api/v1/artifacts/{row['id']}/download"}
            for row in self.db.query("SELECT * FROM artifacts ORDER BY created DESC")
        ]}
