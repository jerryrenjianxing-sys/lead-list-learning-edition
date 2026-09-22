"""Create source/Skill archives, verify packaged files, and hash local deliverables."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="0.1.1")
    parser.add_argument("--include-evidence", action="store_true")
    args = parser.parse_args()
    release = ROOT / "release"
    package = release / f"MediaWorkbench.Desktop-{args.version}-full.nupkg"
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
        for name, expected in manifest["files"].items():
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
    revision = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    )
    integrity = {
        "packaged_files_verified": len(manifest["files"]),
        "source_archive_revision": revision,
        "packaged_manifest_revision": manifest["source_revision"],
        "packaged_manifest_dirty": manifest["source_dirty"],
        "version": args.version,
    }
    (release / "build-integrity.json").write_text(
        json.dumps(integrity, indent=2), encoding="utf-8", newline="\n"
    )
    shutil.copy2(ROOT / "docs/VALIDATION.md", release / "VALIDATION.md")
    shutil.copy2(ROOT / "docs/RECOVERY.md", release / "RECOVERY.md")
    shutil.copy2(ROOT / "docs/LOGIN_SESSIONS.md", release / "LOGIN_SESSIONS.md")
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
