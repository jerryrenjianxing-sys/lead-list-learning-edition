import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest


def test_recovery_upgrade_proof_requires_both_repairs_and_redacts_paths(publisher):
    proof={'ok':True,'version':'0.5.4','baseline_version':'0.5.3','next_version':'0.5.5',
           'cleanup_complete':True,'checks':['full-installer-exhausted-state-repaired','native-package-update-exhausted-state-repaired'],
           'limitations':[],'directory':'private-machine-path'}
    collect=sys.modules['collect_delivery']
    public=collect.public_recovery_upgrade_summary(proof,'0.5.4')
    assert 'directory' not in public
    with pytest.raises(RuntimeError,match='repair'):
        collect.public_recovery_upgrade_summary({**proof,'checks':proof['checks'][:1]},'0.5.4')


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


@pytest.mark.parametrize(("version", "next_version", "baseline"), [("0.3.0", "0.3.1", "0.2.0"), ("0.3.1", "0.3.2", "0.2.0"), ("0.4.1", "0.4.2", "0.3.1"), ("0.4.2", "0.4.3", "0.4.1")])
def test_release_requires_current_version_upgrade_and_cleanup(publisher, version, next_version, baseline):
    integrity = {"version": version, "packaged_manifest_dirty": False, "desktop": {"kind": "electron"}}
    local = {"local_state": "passed", "cleanup_complete": True}
    desktop = {"ok": True, "version": version, "cleanup_complete": True}
    lifecycle = {"ok": True, "version": version}
    upgrade = {"ok": True, "version": version, "baseline_version": baseline, "next_version": next_version, "cleanup_complete": True}
    native = {"ok": True, "version": version, "next_version": next_version, "cleanup_complete": True, "implementation_override": False}
    publisher.verify_release_checks(version, integrity, local, desktop, lifecycle, upgrade, native)
    for field, value in (("version", "0.2.0"), ("next_version", None), ("cleanup_complete", False), ("ok", False)):
        invalid = {**upgrade, field: value}
        with pytest.raises(RuntimeError, match="deterministic validation"):
            publisher.verify_release_checks(version, integrity, local, desktop, lifecycle, invalid, native)
    with pytest.raises(RuntimeError, match="deterministic validation"):
        publisher.verify_release_checks(version, {**integrity, "version": "0.2.0"}, local, desktop, lifecycle, upgrade, native)
    for field, value in (("version", "0.2.0"), ("next_version", None), ("cleanup_complete", False), ("ok", False), ("implementation_override", True)):
        invalid = {**native, field: value}
        with pytest.raises(RuntimeError, match="deterministic validation"):
            publisher.verify_release_checks(version, integrity, local, desktop, lifecycle, upgrade, invalid)
    without_override = {key: value for key, value in native.items() if key != "implementation_override"}
    with pytest.raises(RuntimeError, match="deterministic validation"):
        publisher.verify_release_checks(version, integrity, local, desktop, lifecycle, upgrade, without_override)


def test_created_draft_waits_for_visibility_and_reads_acknowledged_id(publisher, monkeypatch):
    draft = {"id": 42, "tag_name": "v0.3.0", "draft": True, "assets": []}
    resolved = {**draft, "assets": [{"name": "already-uploaded.zip"}]}
    visibility = iter([None, None, draft])
    monkeypatch.setattr(publisher, "find_release", lambda tag: next(visibility))
    slept, creations = [], []
    monkeypatch.setattr(publisher.time, "sleep", slept.append)
    def gh(*args, input_text=None):
        if args[:3] == ("api", "--method", "POST"):
            assert args[-2:] == ("--input", "-")
            payload = json.loads(input_text)
            assert payload["tag_name"] == "v0.3.0" and payload["draft"] is True and payload["prerelease"] is True
            assert payload["make_latest"] == "false"
            creations.append(payload)
            return SimpleNamespace(stdout=json.dumps(draft))
        assert args[:2] == ("api", f"repos/{publisher.REPO}/releases/42")
        return SimpleNamespace(stdout=json.dumps(resolved))
    monkeypatch.setattr(publisher, "gh", gh)
    assert publisher.create_draft("v0.3.0", "0.3.0") == resolved
    assert len(creations) == 1 and slept == [1, 2]


def test_delayed_draft_timeout_never_creates_a_second_release(publisher, monkeypatch):
    draft = {"id": 42, "tag_name": "v0.3.0", "draft": True, "assets": []}
    calls, slept = [], []
    monkeypatch.setattr(publisher, "find_release", lambda tag: None)
    monkeypatch.setattr(publisher.time, "sleep", slept.append)
    def gh(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps(draft))
    monkeypatch.setattr(publisher, "gh", gh)
    with pytest.raises(RuntimeError, match="without creating a second release"):
        publisher.create_draft("v0.3.0", "0.3.0")
    assert len(calls) == 1
    assert sum(slept) == 30


def test_visibility_cannot_switch_to_a_different_release(publisher, monkeypatch):
    monkeypatch.setattr(publisher, "find_release", lambda tag: {"id": 43, "tag_name": tag, "draft": True})
    with pytest.raises(RuntimeError, match="different release"):
        publisher.wait_for_draft("v0.3.0", 42)


def test_published_state_from_id_cannot_be_overwritten(publisher, monkeypatch):
    draft = {"id": 42, "tag_name": "v0.3.0", "draft": True}
    monkeypatch.setattr(publisher, "find_release", lambda tag: draft)
    monkeypatch.setattr(publisher, "gh", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps({**draft, "draft": False})))
    with pytest.raises(RuntimeError, match="cannot be overwritten"):
        publisher.wait_for_draft("v0.3.0", 42)


def test_release_by_id_rejects_mismatched_tag(publisher, monkeypatch):
    monkeypatch.setattr(publisher, "gh", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps({"id": 42, "tag_name": "v0.2.0", "draft": True})))
    with pytest.raises(RuntimeError, match="identity changed"):
        publisher.release_by_id(42, "v0.3.0")


@pytest.fixture
def reconnect_release_checks():
    version = '0.5.6'
    paths = [{'ok': True, 'version': version, 'baseline_version': baseline,
              'next_version': '0.5.7', 'cleanup_complete': True,
              'checks': ['full-installer', 'native-package-update'], 'limitations': []}
             for baseline in ('0.4.3', '0.4.4', '0.5.4')]
    paths[2]['checks'] = ['full-installer-0.5.4-to-0.5.6', 'native-package-update-0.5.4-to-0.5.6']
    return [version, {'version': version, 'packaged_manifest_dirty': False, 'desktop': {'kind': 'electron'}},
            {'local_state': 'passed', 'cleanup_complete': True},
            {'ok': True, 'version': version, 'cleanup_complete': True},
            {'ok': True, 'version': version},
            {**paths[0], 'baseline_versions': ['0.4.3', '0.4.4', '0.5.4'],
             'rollback_verified': True, 'upgrade_paths': paths},
            {'ok': True, 'version': version, 'next_version': '0.5.7',
             'cleanup_complete': True, 'implementation_override': False}]


def test_publisher_rejects_reconnect_release_without_054_evidence(publisher, reconnect_release_checks):
    checks = reconnect_release_checks
    upgrade = checks[5]
    checks[5] = {**upgrade, 'baseline_versions': ['0.4.3', '0.4.4'],
                 'upgrade_paths': upgrade['upgrade_paths'][:2]}
    with pytest.raises(RuntimeError, match='0.5.4'):
        publisher.verify_release_checks(*checks)


def test_publisher_validates_each_required_upgrade_path(publisher, reconnect_release_checks):
    checks = reconnect_release_checks
    publisher.verify_release_checks(*checks)
    upgrade = checks[5]
    checks[5] = {**upgrade, 'upgrade_paths': upgrade['upgrade_paths'][:2]}
    with pytest.raises(RuntimeError, match='upgrade'):
        publisher.verify_release_checks(*checks)
    invalid = {**upgrade['upgrade_paths'][2], 'cleanup_complete': False}
    checks[5] = {**upgrade, 'upgrade_paths': [*upgrade['upgrade_paths'][:2], invalid]}
    with pytest.raises(RuntimeError, match='cleanup'):
        publisher.verify_release_checks(*checks)


def test_reconnect_package_requires_real_recovery_and_cleanup(publisher):
    proof = {'ok': True, 'version': '0.5.6', 'electron_version': '44.7.0', 'faultObserved': True, 'recovered': True,
             'healthyRoutePreserved': True, 'manualRecoveryPreserved': True, 'cleanup_complete': True}
    publisher.verify_reconnect_check(proof, '0.5.6', '44.7.0')
    for key in ('ok', 'faultObserved', 'recovered', 'healthyRoutePreserved', 'manualRecoveryPreserved', 'cleanup_complete'):
        with pytest.raises(RuntimeError, match='reconnect'):
            publisher.verify_reconnect_check({**proof, key: False}, '0.5.6', '44.7.0')
    with pytest.raises(RuntimeError, match='reconnect'):
        publisher.verify_reconnect_check({**proof, 'version': '0.5.4'}, '0.5.6', '44.7.0')


def test_reconnect_receipt_requires_the_measured_electron_runtime(publisher):
    missing = {'ok': True, 'version': '0.5.6', 'faultObserved': True, 'recovered': True,
               'healthyRoutePreserved': True, 'manualRecoveryPreserved': True, 'cleanup_complete': True}
    with pytest.raises(RuntimeError, match='reconnect'):
        publisher.verify_reconnect_check(missing, '0.5.6', '44.7.0')
    measured = {**missing, 'electron_version': '44.7.0'}
    publisher.verify_reconnect_check(measured, '0.5.6', '44.7.0')
    with pytest.raises(RuntimeError, match='reconnect'):
        publisher.verify_reconnect_check(measured, '0.5.6', '43.0.0')
    with pytest.raises(RuntimeError, match='reconnect'):
        publisher.verify_reconnect_check(measured, '0.5.6', None)
