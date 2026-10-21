"""Create source/Skill archives, verify packaged files, and hash local deliverables."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def upgrade_baseline(version):
    """Pin deployed upgrade fixtures without invalidating historical evidence."""
    parts = tuple(map(int, version.split('.')))
    if parts >= (0,5,0):
        return '0.4.3'
    if parts >= (0, 4, 2):
        return "0.4.1"
    return "0.3.1" if parts >= (0, 4, 0) else "0.2.0"


def full_package(release, version):
    feed = json.loads((release / "releases.win-preview.json").read_text(encoding="utf-8"))
    candidates = [a for a in feed["Assets"] if a["PackageId"] == "MediaWorkbench.Desktop" and a["Version"] == version and a["Type"] == "Full"]
    if len(candidates) != 1:
        raise ValueError("Expected exactly one full update package for this version")
    name = candidates[0]["FileName"]
    if Path(name).name != name or not name.endswith("-full.nupkg"):
        raise ValueError("Invalid release package name")
    return release / name


def verify_desktop_manifest(manifest):
    desktop = manifest.get("desktop", {})
    if desktop.get("kind") != "electron" or desktop.get("version") != manifest["version"]:
        raise RuntimeError("Desktop shell and backend versions must agree")
    if desktop.get("source_revision") != manifest.get("source_revision") or desktop.get("source_dirty") is not False:
        raise RuntimeError("Desktop shell was not built from the clean packaged source revision")
    files = manifest["files"]
    required = {"MediaWorkbench.exe", "resources/app.asar", "licenses/Electron-LICENSE.txt", "licenses/Chromium-LICENSES.html"}
    if not required <= files.keys() or not any(name.startswith("resources/app.asar.unpacked/") and name.endswith(".node") for name in files):
        raise RuntimeError("Electron shell, native updater or runtime notices are missing")
    obsolete = {"WebView2Loader.dll", "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "Newtonsoft.Json.dll", "Velopack.dll", "runtime/WebView2RuntimeInstallerX64.exe"}
    if obsolete & files.keys():
        raise RuntimeError("Obsolete desktop dependencies were included in the Electron release")


def public_upgrade_summary(result, version, baseline=None):
    prefix, patch = version.rsplit(".", 1)
    expected_next = prefix + "." + str(int(patch) + 1)
    expected_baseline = baseline or upgrade_baseline(version)
    if result.get("ok") is not True or result.get("version") != version or result.get("baseline_version") != expected_baseline or result.get("next_version") != expected_next or result.get("cleanup_complete") is not True:
        raise RuntimeError("Upgrade checks did not verify the current and private next version with cleanup")
    # Directory, install paths, fixture files, process details and raw errors
    # remain in the local cache; public evidence includes only test outcomes.
    return {key: result[key] for key in ("ok", "version", "baseline_version", "next_version", "cleanup_complete", "checks", "limitations")}


def public_upgrade_matrix(primary,additional,version):
    required=['0.4.3','0.4.4']
    proofs={proof.get('baseline_version'):proof for proof in [primary,*additional]}
    if set(proofs)!=set(required) or primary.get('baseline_version')!='0.4.3' or primary.get('rollback_verified') is not True:
        raise RuntimeError('Upgrade checks require 0.4.3, 0.4.4 and the rehearsed 0.4.3 rollback')
    public=[public_upgrade_summary(proofs[baseline],version,baseline) for baseline in required]
    return {**public[0],'baseline_versions':required,'upgrade_paths':public,'rollback_verified':True}


def public_native_update_summary(result, version):
    prefix, patch = version.rsplit(".", 1)
    expected_next = prefix + "." + str(int(patch) + 1)
    if result.get("ok") is not True or result.get("version") != version or result.get("next_version") != expected_next or result.get("cleanup_complete") is not True or result.get("implementation_override") is not False:
        raise RuntimeError("Native updater did not verify the current and private next version with cleanup")
    return {key: result[key] for key in ("ok", "version", "next_version", "cleanup_complete", "implementation_override", "checks", "limitations")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--release-directory")
    parser.add_argument("--include-evidence", action="store_true")
    parser.add_argument("--upgrade-result", type=Path)
    parser.add_argument('--additional-upgrade-result',type=Path,action='append',default=[])
    parser.add_argument("--native-update-result", type=Path)
    args = parser.parse_args()
    release = Path(args.release_directory) if args.release_directory else ROOT / "release" / args.version
    if args.upgrade_result:
        raw=json.loads(args.upgrade_result.read_text(encoding='utf-8'))
        result=(public_upgrade_matrix(raw,[json.loads(p.read_text(encoding='utf-8')) for p in args.additional_upgrade_result],args.version)
                if tuple(map(int,args.version.split('.'))) >= (0,5,0) else public_upgrade_summary(raw,args.version))
        (release / "ELECTRON_UPGRADE_RESULT.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    if args.native_update_result:
        result = public_native_update_summary(json.loads(args.native_update_result.read_text(encoding="utf-8")), args.version)
        (release / "ELECTRON_NATIVE_UPDATE_RESULT.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    package = full_package(release, args.version)
    with zipfile.ZipFile(package) as archive:
        prefix = "lib/app/"
        manifest = json.loads(archive.read(prefix + "build-manifest.json"))
        revision = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT)
            .decode()
            .strip()
        )
        if (
            manifest["source_dirty"]
            or manifest["source_revision"] != revision
            or manifest["version"] != args.version
        ):
            raise RuntimeError(
                "Rebuild from a clean, matching source revision before collecting delivery files"
            )
        verify_desktop_manifest(manifest)
        for name, expected in manifest["files"].items():
            if archive.getinfo(prefix + name).file_size != expected["size"]:
                raise RuntimeError("Packaged file size mismatch: " + name)
            with archive.open(prefix + name) as content:
                actual = hashlib.file_digest(content, "sha256").hexdigest()
            if actual != expected["sha256"]:
                raise RuntimeError("Packaged file hash mismatch: " + name)
        with zipfile.ZipFile(
            release / "MediaWorkbench-Skill.zip", "w", zipfile.ZIP_DEFLATED
        ) as skill:
            for name in archive.namelist():
                skill_prefix = prefix + "skills/"
                if (
                    name.startswith(skill_prefix)
                    and not name.endswith("/")
                    and "__pycache__" not in name
                ):
                    skill.writestr(name.removeprefix(skill_prefix), archive.read(name))
    subprocess.run(
        [
            "git",
            "archive",
            "--format=zip",
            f"--prefix=MediaWorkbench-{args.version}/",
            "--output",
            str(release / f"MediaWorkbench-{args.version}-Source.zip"),
            "HEAD",
        ],
        cwd=ROOT,
        check=True,
    )
    with zipfile.ZipFile(release / f"MediaWorkbench-{args.version}-Source.zip") as source:
        prefix = f"MediaWorkbench-{args.version}/"
        names = set(source.namelist())
        required = {prefix + "electron/package.json", prefix + "electron/package-lock.json", prefix + "packaging/build-electron.ps1"}
        if not required <= names:
            raise RuntimeError("Source archive is missing the Electron desktop or locked build")
    revision = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    )
    integrity = {
        "packaged_files_verified": len(manifest["files"]),
        "source_archive_revision": revision,
        "packaged_manifest_revision": manifest["source_revision"],
        "packaged_manifest_dirty": manifest["source_dirty"],
        "version": args.version,
        "desktop": manifest["desktop"],
        "runtime": {"python": manifest["python"], "node": manifest["node"]},
    }
    (release / "build-integrity.json").write_text(
        json.dumps(integrity, indent=2), encoding="utf-8", newline="\n"
    )
    shutil.copy2(ROOT / "docs/VALIDATION.md", release / "VALIDATION.md")
    for source in (ROOT / "LICENSE", ROOT / "THIRD_PARTY_NOTICES.md", ROOT / "docs/RELEASE_NOTES.md"):
        shutil.copy2(source, release / source.name)
    shutil.copy2(ROOT / "docs/RECOVERY.md", release / "RECOVERY.md")
    if tuple(map(int,args.version.split('.'))) >= (0,5,0):
        shutil.copy2(ROOT/'docs/ROLLBACK_0.4.3.md',release/'ROLLBACK_0.4.3.md')
    shutil.copy2(ROOT / "docs/LOGIN_SESSIONS.md", release / "LOGIN_SESSIONS.md")
    shutil.copy2(ROOT / "docs/ELECTRON_MIGRATION.md", release / "ELECTRON_MIGRATION.md")
    if args.include_evidence:
        evidence = release / "test-evidence"
        evidence.mkdir(exist_ok=True)
        for name in (
            "pytest-results.xml",
            "all-tests.log",
            "portable-results.json",
            "desktop-results.json",
            "installer-smoke.log",
            "installer-smoke-second.log",
            "release-build.log",
        ):
            file = ROOT / ".cache" / name
            if file.exists():
                shutil.copy2(file, evidence / name)
        shutil.copytree(
            ROOT / ".cache/screenshots", evidence / "screenshots", dirs_exist_ok=True
        )
    lines = []
    for file in sorted(release.rglob("*")):
        if file.is_file() and file.name != "SHA256SUMS.txt":
            with file.open("rb") as content:
                digest = hashlib.file_digest(content, "sha256").hexdigest()
            lines.append(f"{digest}  {file.relative_to(release).as_posix()}")
    (release / "SHA256SUMS.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(integrity), flush=True)


if __name__ == "__main__":
    main()
