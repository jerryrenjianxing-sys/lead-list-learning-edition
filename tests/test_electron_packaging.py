"""Release safety checks at the Python / Forge packaging boundary."""

import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "packaging" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def shell(tmp_path):
    folder = tmp_path / "build/electron/MediaWorkbench-win32-x64"
    folder.mkdir(parents=True)
    versions = {"electron": "44.7.0", "forge": "8.0.1", "velopack": "1.2.0"}
    receipt = {"version": "0.3.0", "platform": "win32", "arch": "x64", **versions}
    (folder.parent / "build-shell.json").write_text(json.dumps(receipt), encoding="utf-8")
    files = {name: name.encode() for name in ("MediaWorkbench.exe", "LICENSE", "LICENSES.chromium.html", "icudtl.dat", "resources.pak", "chrome_100_percent.pak", "chrome_200_percent.pak", "resources/app.asar", "resources/app.asar.unpacked/node_modules/velopack/updater.node")}
    for name, data in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    cache = tmp_path / "cache"
    cache.mkdir()
    with zipfile.ZipFile(cache / "electron-v44.7.0-win32-x64.zip", "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return folder, cache, versions, receipt


def test_shell_merge_keeps_upstream_license_and_native_tree(shell, tmp_path):
    module = load("stage")
    folder, _, _, _ = shell
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "LICENSE").write_text("Noncommercial upstream license", encoding="utf-8")
    module.merge_shell(folder, stage, stage / "licenses")
    assert (stage / "LICENSE").read_text() == "Noncommercial upstream license"
    assert (stage / "licenses/Electron-LICENSE.txt").read_bytes() == (folder / "LICENSE").read_bytes()
    assert (stage / "resources/app.asar.unpacked/node_modules/velopack/updater.node").is_file()


def test_shell_receipt_and_runtime_must_match_locked_binary(shell):
    module = load("stage")
    folder, cache, versions, receipt = shell
    assert module.validate_shell(folder, "0.3.0", versions, cache) == receipt
    with pytest.raises(RuntimeError, match="version or architecture"):
        module.validate_shell(folder, "0.4.0", versions, cache)
    (folder / "icudtl.dat").write_bytes(b"different Electron build")
    with pytest.raises(RuntimeError, match="locked distribution"):
        module.validate_shell(folder, "0.3.0", versions, cache)


def test_native_updater_inside_asar_is_insufficient(shell):
    module = load("stage")
    folder, cache, versions, _ = shell
    (folder / "resources/app.asar.unpacked/node_modules/velopack/updater.node").unlink()
    with pytest.raises(RuntimeError, match="unpacked"):
        module.validate_shell(folder, "0.3.0", versions, cache)


def test_shell_cannot_be_reused_after_source_commit_or_cleanup():
    module = load("stage")
    receipt = {"source_revision": "commit-one", "source_dirty": False}
    module.bind_shell_source(receipt, "commit-one", False)
    with pytest.raises(RuntimeError, match="different source checkout"):
        module.bind_shell_source(receipt, "commit-two", False)
    with pytest.raises(RuntimeError, match="different source checkout"):
        module.bind_shell_source(receipt, "commit-one", True)


def test_runtime_notices_include_transitive_production_dependencies(tmp_path):
    module = load("stage")
    package = tmp_path / "package"
    production = package / "node_modules/transitive"
    production.mkdir(parents=True)
    (production / "LICENSE").write_text("License", encoding="utf-8")
    (production / "package.json").write_text("{}", encoding="utf-8")
    (package / "package-lock.json").write_text(json.dumps({"packages": {"": {}, "node_modules/transitive": {"version": "1.0.0"}, "node_modules/development": {"dev": True}, "node_modules/other-platform": {"optional": True}}}), encoding="utf-8")
    notices = tmp_path / "notices"
    module.npm_notices(package, notices)
    assert (notices / "transitive/LICENSE").read_text() == "License"
    assert not (notices / "development").exists()


def test_release_rejects_legacy_payload():
    module = load("collect_delivery")
    files = {name: {} for name in ("MediaWorkbench.exe", "resources/app.asar", "licenses/Electron-LICENSE.txt", "licenses/Chromium-LICENSES.html", "resources/app.asar.unpacked/node_modules/velopack/updater.node")}
    manifest = {"version": "0.3.0", "source_revision": "revision", "desktop": {"kind": "electron", "version": "0.3.0", "source_revision": "revision", "source_dirty": False}, "files": files}
    module.verify_desktop_manifest(manifest)
    files["WebView2Loader.dll"] = {}
    with pytest.raises(RuntimeError, match="Obsolete"):
        module.verify_desktop_manifest(manifest)


def test_delivery_rejects_dirty_or_stale_desktop_sources():
    module = load("collect_delivery")
    manifest = {"version": "0.3.0", "source_revision": "revision", "desktop": {"kind": "electron", "version": "0.3.0", "source_revision": "revision", "source_dirty": True}, "files": {}}
    with pytest.raises(RuntimeError, match="clean packaged source"):
        module.verify_desktop_manifest(manifest)
    manifest["desktop"].update(source_dirty=False, source_revision="old-revision")
    with pytest.raises(RuntimeError, match="clean packaged source"):
        module.verify_desktop_manifest(manifest)


def test_cached_electron_must_match_binary_lock(tmp_path, monkeypatch):
    module = load("bootstrap-electron")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    cache = tmp_path / "packaging/cache"
    cache.mkdir(parents=True)
    (cache / module.NAME).write_bytes(b"corrupt archive")
    (tmp_path / "packaging/downloads.lock.json").write_text(json.dumps({module.NAME: {"url": "https://example.invalid/not-fetched", "sha256": "0" * 64, "size": 15}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="checksum or size"):
        module.prepare()


def test_private_version_is_derived_without_source_mutation():
    module = load("prepare_test_release")
    assert module.next_patch("0.3.0") == "0.3.1"
    assert module.next_patch("0.3.1") == "0.3.2"
    assert module.next_patch("2.10.99") == "2.10.100"
    for version in ("0.3", "v0.3.0", "0.3.0-preview", "01.3.0"):
        with pytest.raises(ValueError):
            module.next_patch(version)


def test_private_fixture_cannot_replace_baseline_or_escape_build(tmp_path):
    module = load("prepare_test_release")
    root = tmp_path / "project"
    stage = root / "build/MediaWorkbench"
    stage.mkdir(parents=True)
    assert module.fixture_paths(stage, root / ".cache/private-update", root) == (stage.resolve(), (root / ".cache/private-update").resolve())
    for target in (stage, stage / "child", root / "build", tmp_path / "outside"):
        with pytest.raises(RuntimeError):
            module.fixture_paths(stage, target, root)


def test_upgrade_public_summary_removes_local_paths():
    module = load("collect_delivery")
    raw = {"ok": True, "version": "0.3.0", "baseline_version": "0.2.0", "next_version": "0.3.1", "cleanup_complete": True, "checks": ["native-update"], "limitations": ["Synthetic login fixture"], "directory": "C:/private/local/test", "source": "C:/private/source"}
    public = module.public_upgrade_summary(raw, "0.3.0")
    assert "directory" not in public and "source" not in public
    assert public["checks"] == ["native-update"]
    raw["next_version"] = None
    with pytest.raises(RuntimeError, match="private next version"):
        module.public_upgrade_summary(raw, "0.3.0")


def test_native_update_summary_requires_current_patch_and_cleanup():
    module = load("collect_delivery")
    raw = {"ok": True, "version": "0.3.1", "next_version": "0.3.2", "cleanup_complete": True, "implementation_override": False, "checks": ["actual-native-check", "actual-native-copy"], "limitations": ["Private loopback HTTP fixture"], "install_directory": "C:/private/installation", "package": "C:/private/next.nupkg"}
    public = module.public_native_update_summary(raw, "0.3.1")
    assert "install_directory" not in public and "package" not in public
    assert public["version"] == "0.3.1" and public["next_version"] == "0.3.2"
    assert public["implementation_override"] is False
    for field, value in (("version", "0.3.0"), ("next_version", "0.3.1"), ("cleanup_complete", False), ("ok", False), ("implementation_override", True)):
        with pytest.raises(RuntimeError, match="Native updater"):
            module.public_native_update_summary({**raw, field: value}, "0.3.1")
    without_override = {key: value for key, value in raw.items() if key != "implementation_override"}
    with pytest.raises(RuntimeError, match="Native updater"):
        module.public_native_update_summary(without_override, "0.3.1")
