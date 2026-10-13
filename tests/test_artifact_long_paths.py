"""Regression for the packaged Windows self-test's nested artifact paths."""
import hashlib
import io
import json
import os
import shutil
import zipfile
from pathlib import Path, PurePosixPath

import pytest
from fastapi.testclient import TestClient

from workbench import paths
from workbench.app import make_app


@pytest.mark.parametrize("source,expected", [
    (r"C:\data\results\file.json", r"\\?\C:\data\results\file.json"),
    (r"\\server\share\results\file.json", r"\\?\UNC\server\share\results\file.json"),
    (r"\\?\C:\data\results\file.json", r"\\?\C:\data\results\file.json"),
    (r"\\?\UNC\server\share\results\file.json", r"\\?\UNC\server\share\results\file.json"),
])
def test_windows_filesystem_conversion_preserves_prefixed_paths(monkeypatch, source, expected):
    monkeypatch.setattr(paths, "_WINDOWS", True)
    assert paths.filesystem_path(source) == expected


def test_other_systems_keep_filesystem_path(monkeypatch):
    monkeypatch.setattr(paths, "_WINDOWS", False)
    assert paths.filesystem_path(PurePosixPath("/tmp/研究/artifact.json")) == "/tmp/研究/artifact.json"


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows MAX_PATH filesystem behavior")
def test_windows_deep_artifact_exports_and_downloads(tmp_path):
    root = tmp_path / "中文 用户数据"
    while len(str(root)) < 185:
        root /= "深层目录123456789"
    assert len(str(root / "workbench.sqlite3")) < 240
    try:
        with TestClient(make_app(root, run_queue=False)) as client:
            client.get("/api/v1/session")

            def post(route, body=None):
                response = client.post("/api/v1" + route, json=body or {})
                assert response.status_code == 200, response.text
                return response.json()

            dataset = post("/demo")
            analysis = post("/analyses", {"dataset_id": dataset["id"], "name": "深路径导出", "goal": "导出与下载"})
            inputs = client.get(f"/api/v1/analyses/{analysis['id']}/inputs").json()["items"]
            post(f"/analyses/{analysis['id']}/batches", {
                "batch_id": "long-path", "processed_ids": [row["id"] for row in inputs],
                "results": [{"payload": {"内容": "中文深路径导出"}, "evidence_ids": [inputs[0]["id"]]}],
            })
            post(f"/analyses/{analysis['id']}/finish")
            for extension in ("json", "csv", "xlsx", "docx", "md"):
                artifact = post(f"/analyses/{analysis['id']}/export", {"format": extension})
                response = client.get(artifact["download_url"])
                assert response.status_code == 200
                assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
                row = client.app.state.db.one("artifacts", artifact["id"])
                regular = root / row["relative_path"]
                assert len(str(regular)) > 260
                assert not row["relative_path"].startswith("\\\\?\\")
                assert row["name"] == f"analysis-{analysis['id']}.{extension}"
                assert Path(paths.filesystem_path(regular)).read_bytes() == response.content
                if extension == "json":
                    assert json.loads(response.content)["results"][0]["payload"]["内容"] == "中文深路径导出"
                elif extension in ("xlsx", "docx"):
                    assert zipfile.is_zipfile(io.BytesIO(response.content))
                else:
                    assert "中文深路径导出" in response.content.decode("utf-8-sig")
            filename = "附件" + "x" * 135 + ".txt"
            attachment = client.post(f"/api/v1/analyses/{analysis['id']}/attachments", files={"file": (filename, b"deep-attachment", "text/plain")})
            assert attachment.status_code == 200
            assert attachment.json()["name"] == filename
            assert client.get(attachment.json()["download_url"]).content == b"deep-attachment"
            assert client.app.state.db.root == root.resolve()
    finally:
        if root.exists():
            shutil.rmtree(paths.filesystem_path(root))
