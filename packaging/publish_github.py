"""Publish a verified immutable preview; an incomplete upload stays a draft."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
REPO = "jerryrenjianxing-sys/lead-list-learning-edition"


def gh(*args, ok=True):
    result = subprocess.run(["gh", *args], cwd=ROOT, text=True, encoding="utf-8", capture_output=True)
    if ok and result.returncode:
        raise RuntimeError("GitHub operation failed: " + result.stderr[:600])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if args.tag != "v" + version:
        raise ValueError("Tag differs from source version")
    directory = ROOT / "release" / version
    integrity = json.loads((directory / "build-integrity.json").read_text())
    check = json.loads((directory / "SELF_TEST_RESULT.json").read_text(encoding="utf-8"))
    if integrity["packaged_manifest_dirty"] or check["local_state"] != "passed" or not check["cleanup_complete"]:
        raise RuntimeError("Release did not pass deterministic validation")
    commit = json.loads(gh("api", f"repos/{REPO}/commits/{args.tag}").stdout)["sha"]
    if commit != integrity["packaged_manifest_revision"] or commit != integrity["source_archive_revision"]:
        raise ValueError("Public tag and packaged source differ")
    files = sorted(p for p in directory.iterdir() if p.is_file())
    names = {p.name for p in files}
    required = {"releases.win-preview.json", f"MediaWorkbench.Desktop-{version}-full.nupkg", "MediaWorkbench-Skill.zip", "SHA256SUMS.txt", "LICENSE", "THIRD_PARTY_NOTICES.md"}
    if not required <= names or not any(n.endswith("Setup.exe") for n in names):
        raise ValueError("Missing release assets")
    hashes = {}
    for file in files:
        with file.open("rb") as stream:
            hashes[file.name] = hashlib.file_digest(stream, "sha256").hexdigest()
    for line in (directory / "SHA256SUMS.txt").read_text().splitlines():
        digest, name = line.split("  ", 1)
        if hashes.get(name) != digest:
            raise ValueError("Asset changed after collection: " + name)
    existing = gh("api", f"repos/{REPO}/releases/tags/{args.tag}", ok=False)
    if existing.returncode == 0:
        release = json.loads(existing.stdout)
        if not release["draft"]:
            raise RuntimeError("Published releases cannot be overwritten")
    else:
        if "404" not in existing.stderr:
            raise RuntimeError("Unable to inspect existing release")
        gh("release", "create", args.tag, "--repo", REPO, "--verify-tag", "--draft", "--prerelease", "--title", f"Media Deep Researcher {version} 预览版", "--notes-file", str(ROOT / "docs/RELEASE_NOTES.md"))
        release = json.loads(gh("api", f"repos/{REPO}/releases/tags/{args.tag}").stdout)
    present = {a["name"]: a for a in release["assets"]}
    for file in files:
        if file.name in present:
            if present[file.name].get("digest") != "sha256:" + hashes[file.name]:
                raise RuntimeError("Existing draft asset differs: " + file.name)
        else:
            gh("release", "upload", args.tag, str(file), "--repo", REPO)
    uploaded = json.loads(gh("api", f"repos/{REPO}/releases/tags/{args.tag}").stdout)
    assets = {a["name"]: a for a in uploaded["assets"]}
    if set(assets) != names:
        raise RuntimeError("Draft contains missing or unexpected assets")
    for file in files:
        if assets[file.name].get("digest") != "sha256:" + hashes[file.name] or assets[file.name]["size"] != file.stat().st_size:
            raise RuntimeError("Uploaded asset verification failed: " + file.name)
    gh("release", "edit", args.tag, "--repo", REPO, "--draft=false", "--prerelease", "--latest=false")
    print(json.dumps({"url": uploaded["html_url"], "assets_verified": len(files), "source_commit": commit}))


if __name__ == "__main__":
    main()
