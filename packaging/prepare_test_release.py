"""Build a private next-patch update fixture from a clean release stage.

This never changes source versions, lock files, Git tags or public release files.
Only immutable files are hardlinked; archives and mutable metadata are copied.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def next_patch(version):
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version)
    if match is None:
        raise ValueError("Private update fixtures require a stable three-part source version")
    major, minor, patch = map(int, match.groups())
    return f"{major}.{minor}.{patch + 1}"


def fixture_paths(source, output, root=ROOT):
    source, output, root = Path(source).resolve(), Path(output).resolve(), Path(root).resolve()
    owned = [root / "build", root / ".cache"]
    if not source.is_relative_to(root) or not any(output.is_relative_to(path.resolve()) and output != path.resolve() for path in owned):
        raise RuntimeError("Fixture paths must remain inside the project's build or .cache directories")
    if source == output or source.is_relative_to(output) or output.is_relative_to(source):
        raise RuntimeError("Private output and baseline must be separate directories")
    if output.exists():
        raise RuntimeError("Use a fresh private fixture directory; existing artifacts are never overwritten")
    return source, output


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def hashes(directory):
    return {file.relative_to(directory).as_posix(): digest(file) for file in sorted(directory.rglob("*")) if file.is_file()}


def build(source, workspace):
    source, workspace = fixture_paths(source, workspace)
    manifest = json.loads((source / "build-manifest.json").read_text(encoding="utf-8"))
    build_version = json.loads((source / "build-version.json").read_text(encoding="utf-8"))
    baseline_version = build_version["version"]
    version = next_patch(baseline_version)
    if manifest["version"] != baseline_version or manifest["desktop"].get("kind") != "electron" or manifest["desktop"].get("version") != baseline_version or manifest["source_dirty"] or manifest["desktop"]["source_dirty"]:
        raise RuntimeError("The fixture must start from a verified clean Electron release stage")
    baseline = hashes(source)
    workspace.mkdir(parents=True)
    stage, release = workspace / "app", workspace / "release"
    mutable = {"MediaWorkbench.exe", "resources/app.asar", "build-version.json", "build-manifest.json"}
    def copy(source_file, destination_file):
        relative = Path(source_file).relative_to(source).as_posix()
        if relative in mutable or relative.startswith("resources/app.asar.unpacked/"):
            return shutil.copy2(source_file, destination_file)
        try:
            os.link(source_file, destination_file)
        except OSError:
            shutil.copy2(source_file, destination_file)
        return destination_file
    try:
        shutil.copytree(source, stage, copy_function=copy)
        build_version["version"] = version
        (stage / "build-version.json").write_text(json.dumps(build_version), encoding="utf-8")
        manifest.update(version=version, private_test=True, source_dirty=True)
        manifest["desktop"].update(version=version, source_dirty=True)
        (stage / "build-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        node = source / "runtime/node/node.exe"
        asar_module = ROOT / "electron/node_modules/@electron/asar/lib/asar.js"
        extract = workspace / "asar-extracted"
        code = """
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const [moduleFile, archive, extract, version] = process.argv.slice(1);
const asar = await import(pathToFileURL(moduleFile).href);
asar.extractAll(archive, extract);
const filename = path.join(extract, 'package.json');
const manifest = JSON.parse(fs.readFileSync(filename, 'utf8'));
manifest.version = version;
fs.writeFileSync(filename, JSON.stringify(manifest, null, 2) + '\\n');
await asar.createPackageWithOptions(extract, archive, { unpack: '**/*.node' });
// extractAll cached the old header, which is invalid after the archive rewrite.
asar.uncache(archive);
if (JSON.parse(asar.extractFile(archive, 'package.json').toString()).version !== version) throw new Error('Private version rewrite failed');
"""
        subprocess.run([str(node), "--input-type=module", "-e", code, str(asar_module), str(stage / "resources/app.asar"), str(extract), version], check=True, cwd=ROOT, timeout=180)
        tools = ROOT / "packaging/tools"
        notes = workspace / "PRIVATE-TEST-NOTES.md"
        notes.write_text("Private Electron upgrade fixture. Not a public release. Never publish or tag.\n", encoding="utf-8")
        environment = {**os.environ, "MEDIAWORKBENCH_DATA_DIR": str(workspace / "pack-validation-data"), "MEDIAWORKBENCH_INSTANCE": str(workspace / "pack-validation-instance.json")}
        subprocess.run([str(tools / "dotnet-runtime/expanded/dotnet.exe"), str(tools / "vpk-package/expanded/tools/net8.0/any/vpk.dll"), "pack", "--packId", "MediaWorkbench.Desktop", "--packVersion", version, "--channel", "win-preview", "--packDir", str(stage), "--mainExe", "MediaWorkbench.exe", "--packTitle", "Media Deep Researcher", "--icon", str(ROOT / "webui/public/brand/media-deep-researcher.ico"), "--packAuthors", "MediaWorkbench contributors", "--releaseNotes", str(notes), "--outputDir", str(release), "--delta", "None"], check=True, cwd=ROOT, env=environment, timeout=600)
    finally:
        after = hashes(source)
        if after != baseline:
            changed = sorted(set(baseline) ^ set(after) | {name for name in baseline.keys() & after.keys() if baseline[name] != after[name]})
            raise RuntimeError("Baseline changed while creating private fixture: " + ", ".join(changed[:20]))
    feed = json.loads((release / "releases.win-preview.json").read_text(encoding="utf-8"))
    entry, = [asset for asset in feed["Assets"] if asset["Version"] == version and asset["Type"] == "Full"]
    package = release / entry["FileName"]
    if digest(package) != entry["SHA256"].lower() or package.stat().st_size != entry["Size"]:
        raise RuntimeError("Private update package integrity differs from feed")
    with zipfile.ZipFile(package) as archive:
        if json.loads(archive.read("lib/app/build-version.json"))["version"] != version:
            raise RuntimeError("Private update has wrong backend version")
    result = {"private_test": True, "version": version, "baseline_version": baseline_version, "baseline_files_unchanged": len(baseline), "package": str(package), "feed": str(release / "releases.win-preview.json"), "sha256": digest(package), "bytes": package.stat().st_size, "public_tag_created": False}
    (workspace / "PRIVATE_TEST_RESULT.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output)))
