"""Candidate upgrade evidence and CLI configuration, without running installers."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module_at(relative):
    spec = importlib.util.spec_from_file_location("candidate_upgrade_contract", ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_candidate_public_evidence_requires_the_approved_previous_electron_version():
    collector = module_at("packaging/collect_delivery.py")
    result = {"ok": True, "version": "0.4.0", "baseline_version": "0.3.1", "next_version": "0.4.1", "cleanup_complete": True,
              "checks": ["full-installer-0.3.1-to-0.4.0", "native-package-update-0.3.1-to-0.4.0"],
              "limitations": ["Synthetic account only"], "directory": "C:/private/fixture"}
    public = collector.public_upgrade_summary(result, "0.4.0")
    assert public["baseline_version"] == "0.3.1"
    assert public["next_version"] == "0.4.1"
    assert public["checks"] == result["checks"]
    assert "directory" not in public
    for field, value in (("baseline_version", "0.2.0"), ("next_version", "0.4.2"), ("cleanup_complete", False)):
        with pytest.raises(RuntimeError, match="Upgrade checks"):
            collector.public_upgrade_summary({**result, field: value}, "0.4.0")


def test_upgrade_cli_selects_electron_baseline_and_derives_private_next_version(tmp_path):
    harness = module_at("tests/electron_upgrade_smoke.py")
    args = harness.parse_args(["--new-setup", str(tmp_path / "Setup.exe"), "--new-package", str(tmp_path / "candidate.nupkg"),
                               "--version", "0.4.0", "--baseline-version", "0.3.1"])
    assert args.baseline_version == "0.3.1"
    assert args.next_version == "0.4.1"
    assert args.old_setup.parent.name == "Media Deep Researcher 0.3.1 Windows安装包"


def test_historical_upgrade_cli_defaults_still_use_the_winforms_baseline(tmp_path):
    harness = module_at("tests/electron_upgrade_smoke.py")
    args = harness.parse_args(["--new-setup", str(tmp_path / "Setup.exe"), "--new-package", str(tmp_path / "candidate.nupkg")])
    assert args.baseline_version == "0.2.0"
    assert args.version == "0.3.0"
    assert args.next_version == "0.3.1"
    assert args.old_setup.parent.name == "Media Deep Researcher 0.2.0 Windows安装包"


def test_state_recovery_release_requires_both_deployed_baselines_and_a_real_rollback():
    collector=module_at('packaging/collect_delivery.py')
    proof={'ok':True,'version':'0.5.0','baseline_version':'0.4.3','next_version':'0.5.1','cleanup_complete':True,
           'rollback_verified':True,'checks':['full-installer','native-package-update','rollback-uninstall-reinstall'],
           'limitations':['Synthetic account only'],'directory':'C:/private'}
    other={**proof,'baseline_version':'0.4.4','rollback_verified':False}
    public=collector.public_upgrade_matrix(proof,[other],'0.5.0')
    assert public['baseline_versions']==['0.4.3','0.4.4']
    assert public['rollback_verified'] is True
    assert 'C:/private' not in str(public)
    with pytest.raises(RuntimeError):collector.public_upgrade_matrix(proof,[],'0.5.0')
    with pytest.raises(RuntimeError):collector.public_upgrade_matrix({**proof,'rollback_verified':False},[other],'0.5.0')
