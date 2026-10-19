import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import zipfile

import pytest


def test_candidate_policy_rejects_publication_before_any_upload(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / 'packaging'))
    spec = importlib.util.spec_from_file_location('candidate_publisher', root / 'packaging/publish_github.py')
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    (tmp_path / 'packaging').mkdir()
    (tmp_path / 'packaging/release-policy.json').write_text(json.dumps({'publication': 'candidate-only'}))
    (tmp_path / 'pyproject.toml').write_text('[project]\nversion="0.4.0"\n')
    monkeypatch.setattr(publisher, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['publish_github.py', '--tag', 'v0.4.0'])
    with pytest.raises(RuntimeError, match='Candidate'):
        publisher.main()


def test_candidate_portable_has_explicit_isolated_start_and_checksums(tmp_path):
    root = Path(__file__).resolve().parents[1]
    original = tmp_path / 'MediaWorkbench.Desktop-win-preview-Portable.zip'
    with zipfile.ZipFile(original, 'w') as archive:
        archive.writestr('current/MediaWorkbench.exe', 'fixture')
    (tmp_path / 'build-integrity.json').write_text(json.dumps({
        'version': '0.4.0', 'packaged_manifest_revision': 'a' * 40,
        'source_archive_revision': 'a' * 40, 'packaged_manifest_dirty': False,
    }))
    result = subprocess.run([sys.executable, str(root / 'packaging/prepare_candidate.py'),
                             '--directory', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / 'CANDIDATE.json').read_text())
    assert manifest['publication'] == 'candidate-only'
    assert manifest['public_release'] is False
    assert manifest['source_revision'] == 'a' * 40
    with zipfile.ZipFile(tmp_path / manifest['isolated_portable']) as archive:
        launcher = archive.read('Start-Candidate.cmd').decode('utf-8')
        assert 'MEDIAWORKBENCH_DATA_DIR=' in launcher
        assert 'MEDIAWORKBENCH_INSTANCE=' in launcher
        assert r'candidates\0.4.0-aaaaaaaaaaaa' in launcher
        assert 'current\\MediaWorkbench.exe' in launcher
        assert b'fixture' == archive.read('current/MediaWorkbench.exe')
    assert 'CANDIDATE.json' in (tmp_path / 'SHA256SUMS.txt').read_text()
