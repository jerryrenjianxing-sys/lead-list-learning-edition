import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest


@pytest.fixture
def publisher(monkeypatch):
    folder = Path(__file__).resolve().parents[1] / "packaging"
    for name in ("collect_delivery", "publish_github"):
        spec = importlib.util.spec_from_file_location(name, folder / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return module


def test_draft_lookup_uses_authenticated_paginated_listing(publisher, monkeypatch):
    draft = {"id": 42, "tag_name": "v0.2.0", "draft": True, "assets": []}
    def gh(*args):
        assert args[:3] == ("api", "--paginate", "--slurp")
        assert "/releases?" in args[3]
        return SimpleNamespace(stdout=json.dumps([[{"tag_name": "v0.1.1", "draft": False}], [draft]]))
    monkeypatch.setattr(publisher, "gh", gh)
    assert publisher.find_release("v0.2.0") == draft
    assert publisher.find_release("v0.3.0") is None


def test_lookup_errors_are_not_treated_as_missing_release(publisher, monkeypatch):
    def offline(*args):
        raise RuntimeError("Network unavailable")
    monkeypatch.setattr(publisher, "gh", offline)
    with pytest.raises(RuntimeError, match="Network unavailable"):
        publisher.find_release("v0.2.0")


def test_ambiguous_drafts_cannot_be_published(publisher, monkeypatch):
    page = [{"id": n, "tag_name": "v0.2.0", "draft": True} for n in (1, 2)]
    monkeypatch.setattr(publisher, "gh", lambda *args: SimpleNamespace(stdout=json.dumps([page])))
    with pytest.raises(RuntimeError, match="Multiple releases"):
        publisher.find_release("v0.2.0")
