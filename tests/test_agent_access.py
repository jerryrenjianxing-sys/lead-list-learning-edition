"""The AgentAccess Interface shared by the page, handshake and discovery."""

import json
from pathlib import Path
import sys

from workbench.agent_access import AgentAccess
import workbench.agent_access as agent_access


SKILL = Path(__file__).resolve().parents[1] / "skills/media-workbench"


def test_handshake_is_compact_credential_free_and_stable_for_one_instance(tmp_path):
    access = AgentAccess(tmp_path / "data", SKILL, Path(sys.executable), tmp_path / "instance.json")
    result = access.handshake()
    assert result["product"] == "MediaWorkbench"
    assert result["api_version"] == 1
    assert result["status"] == "ok"
    assert result == access.handshake()
    assert len(result["instance_id"]) == 32
    assert len(result["workspace_id"]) == 64
    assert "self_test" in result["summary"]["operations"]
    assert result["summary"]["analysis"] == "user_defined"
    assert "token" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)
    assert len(json.dumps(result)) < 2500


def test_revision_changes_for_capability_definition_not_patch_identity(tmp_path, monkeypatch):
    first = AgentAccess(tmp_path, SKILL, sys.executable, tmp_path / "instance.json")
    monkeypatch.setattr(agent_access, "__version__", "99.0.123")
    second = AgentAccess(tmp_path, SKILL, sys.executable, tmp_path / "instance.json")
    assert first.handshake()["capability_revision"] == second.handshake()["capability_revision"]
    assert first.instance_id != second.instance_id
    assert first.workspace_id == second.workspace_id


