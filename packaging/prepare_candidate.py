"""Add an isolated candidate launcher without touching the signed update payload."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


def prepare(directory):
    integrity = json.loads((directory / 'build-integrity.json').read_text(encoding='utf-8'))
    version, revision = integrity['version'], integrity['packaged_manifest_revision']
    if integrity['packaged_manifest_dirty'] or revision != integrity['source_archive_revision']:
        raise RuntimeError('Candidate source and packaged code must agree')
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Invalid source revision')
    if any(c not in '0123456789.' for c in version):
        raise ValueError('Invalid version')
    name = f'MediaWorkbench-{version}-Candidate-{revision[:12]}-Portable.zip'
    original = directory / 'MediaWorkbench.Desktop-win-preview-Portable.zip'
    target = directory / name
    launcher = (
        '@echo off\r\nsetlocal DisableDelayedExpansion\r\n'
        f'set "MEDIAWORKBENCH_DATA_DIR=%LOCALAPPDATA%\\MediaWorkbench\\candidates\\{version}-{revision[:12]}\\data"\r\n'
        f'set "MEDIAWORKBENCH_INSTANCE=%LOCALAPPDATA%\\MediaWorkbench\\candidates\\{version}-{revision[:12]}\\instance.json"\r\n'
        'start "" "%~dp0current\\MediaWorkbench.exe"\r\n'
    )
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as result:
        if 'current/MediaWorkbench.exe' not in source.namelist():
            raise RuntimeError('Portable layout is not supported')
        for entry in source.infolist():
            with source.open(entry) as content, result.open(entry, 'w') as output:
                shutil.copyfileobj(content, output)
        result.writestr('Start-Candidate.cmd', launcher)
        result.writestr('CANDIDATE-README.txt',
            'Extract all files, then run Start-Candidate.cmd.\n'
            'This launcher uses a separate data directory and does not replace the daily installation.\n'
            'Do not run Setup.exe for an isolated preview: it shares the installed product identity.\n'
            'This candidate is not a public release; real external Agent acceptance is pending.\n')
    manifest = {
        'version': version, 'source_revision': revision, 'publication': 'candidate-only',
        'public_release': False, 'daily_installation_modified': False,
        'isolated_portable': name, 'start': 'Start-Candidate.cmd',
        'pending_publication_gates': ['TRAE: three fresh sessions', 'Codex: three fresh sessions'],
        'hermes_validation': 'not_tested',
    }
    (directory / 'CANDIDATE.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')


def checksums(directory):
    rows = []
    for path in sorted(directory.rglob('*')):
        if path.is_file() and path.name != 'SHA256SUMS.txt':
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            rows.append(f'{digest}  {path.relative_to(directory).as_posix()}')
    (directory / 'SHA256SUMS.txt').write_text('\n'.join(rows) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--refresh-checksums', action='store_true')
    args = parser.parse_args()
    if not args.refresh_checksums:
        prepare(args.directory)
    checksums(args.directory)
    print(json.dumps({'candidate': True, 'checksums_complete': True}))


if __name__ == '__main__':
    main()
