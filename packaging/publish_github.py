"""Publish a verified immutable preview; an incomplete upload stays a draft."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import tomllib
from collect_delivery import full_package

ROOT = Path(__file__).resolve().parents[1]
REPO = "jerryrenjianxing-sys/lead-list-learning-edition"
RELEASE_VISIBILITY_DELAYS = (0, 1, 2, 4, 8, 15)


def gh(*args, ok=True, input_text=None):
    result = subprocess.run(["gh", *args], cwd=ROOT, text=True, encoding="utf-8", capture_output=True, input=input_text)
    if ok and result.returncode:
        raise RuntimeError("GitHub operation failed: " + result.stderr[:600])
    return result


def find_release(tag):
    # GET /releases/tags/{tag} only returns published releases. List releases
    # with our write-capable token to include drafts and support failed-upload retries.
    pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{REPO}/releases?per_page=100", "-H", "Cache-Control: no-cache").stdout)
    matches = [release for page in pages for release in page if release["tag_name"] == tag]
    if len(matches) > 1:
        raise RuntimeError("Multiple releases use this tag; refusing ambiguous publication")
    return matches[0] if matches else None


def release_by_id(release_id, tag):
    release = json.loads(gh("api", f"repos/{REPO}/releases/{release_id}", "-H", "Cache-Control: no-cache").stdout)
    if release.get("id") != release_id or release.get("tag_name") != tag:
        raise RuntimeError("Release identity changed; refusing ambiguous publication")
    return release


def wait_for_draft(tag, release_id):
    # A successful create can precede release-list visibility. gh release
    # upload also resolves the tag, so wait before starting any uploads.
    for delay in RELEASE_VISIBILITY_DELAYS:
        if delay:
            time.sleep(delay)
        listed = find_release(tag)
        if listed is None:
            continue
        if listed.get("id") != release_id:
            raise RuntimeError("A different release appeared for the tag; refusing publication")
        release = release_by_id(release_id, tag)
        if not release.get("draft"):
            raise RuntimeError("Published releases cannot be overwritten")
        return release
    raise RuntimeError(f"Draft {release_id} did not become visible within the bounded wait; retry publication later without creating a second release")


def create_draft(tag, version):
    # Keep the ID from the create response instead of relying on an
    # immediately refreshed list. POST runs once; uncertain outcomes must
    # be recovered through find_release on the next publisher invocation.
    payload = {
        "tag_name": tag,
        "name": f"Media Deep Researcher {version} 预览版",
        "body": (ROOT / "docs/RELEASE_NOTES.md").read_text(encoding="utf-8"),
        "draft": True,
        "prerelease": True,
        "make_latest": "false",
    }
    created = json.loads(gh("api", "--method", "POST", f"repos/{REPO}/releases", "--input", "-", input_text=json.dumps(payload, ensure_ascii=False)).stdout)
    if type(created.get("id")) is not int or created["id"] <= 0 or created.get("tag_name") != tag or created.get("draft") is not True:
        raise RuntimeError("Created draft response has an unexpected identity or state")
    return wait_for_draft(tag, created["id"])


def verify_release_checks(version, integrity, check, desktop_check, lifecycle_check, upgrade_check):
    prefix, patch = version.rsplit(".", 1)
    next_version = prefix + "." + str(int(patch) + 1)
    if integrity.get("version") != version or integrity["packaged_manifest_dirty"] or check["local_state"] != "passed" or not check["cleanup_complete"] or desktop_check.get("ok") is not True or desktop_check.get("version") != version or desktop_check.get("cleanup_complete") is not True or lifecycle_check.get("ok") is not True or lifecycle_check.get("version") != version or integrity.get("desktop", {}).get("kind") != "electron" or upgrade_check.get("ok") is not True or upgrade_check.get("version") != version or upgrade_check.get("baseline_version") != "0.2.0" or upgrade_check.get("next_version") != next_version or upgrade_check.get("cleanup_complete") is not True:
        raise RuntimeError("Release did not pass deterministic validation")


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
    desktop_check = json.loads((directory / "ELECTRON_TEST_RESULT.json").read_text(encoding="utf-8"))
    lifecycle_check = json.loads((directory / "ELECTRON_LIFECYCLE_RESULT.json").read_text(encoding="utf-8"))
    upgrade_check = json.loads((directory / "ELECTRON_UPGRADE_RESULT.json").read_text(encoding="utf-8"))
    verify_release_checks(version, integrity, check, desktop_check, lifecycle_check, upgrade_check)
    commit = json.loads(gh("api", f"repos/{REPO}/commits/{args.tag}").stdout)["sha"]
    if commit != integrity["packaged_manifest_revision"] or commit != integrity["source_archive_revision"]:
        raise ValueError("Public tag and packaged source differ")
    files = sorted(p for p in directory.iterdir() if p.is_file())
    names = {p.name for p in files}
    required = {"releases.win-preview.json", full_package(directory, version).name, "MediaWorkbench-Skill.zip", "SHA256SUMS.txt", "LICENSE", "THIRD_PARTY_NOTICES.md", "ELECTRON_TEST_RESULT.json", "ELECTRON_LIFECYCLE_RESULT.json", "ELECTRON_UPGRADE_RESULT.json"}
    if not required <= names or not any(n.endswith("Setup.exe") for n in names):
        raise ValueError("Missing release assets")
    hashes = {}
    for file in files:
        with file.open("rb") as stream:
            hashes[file.name] = hashlib.file_digest(stream, "sha256").hexdigest()
    for entry in json.loads((directory / "releases.win-preview.json").read_text(encoding="utf-8"))["Assets"]:
        name = entry["FileName"]
        if hashes.get(name) != entry["SHA256"].lower() or (directory / name).stat().st_size != entry["Size"]:
            raise ValueError("Update feed does not match packaged files")
    for line in (directory / "SHA256SUMS.txt").read_text().splitlines():
        digest, name = line.split("  ", 1)
        if hashes.get(name) != digest:
            raise ValueError("Asset changed after collection: " + name)
    release = find_release(args.tag)
    if release is not None:
        if not release["draft"]:
            raise RuntimeError("Published releases cannot be overwritten")
        release = release_by_id(release["id"], args.tag)
        if not release.get("draft"):
            raise RuntimeError("Published releases cannot be overwritten")
    else:
        release = create_draft(args.tag, version)
    present = {a["name"]: a for a in release["assets"]}
    for file in files:
        if file.name in present:
            if present[file.name].get("digest") != "sha256:" + hashes[file.name]:
                raise RuntimeError("Existing draft asset differs: " + file.name)
        else:
            gh("release", "upload", args.tag, str(file), "--repo", REPO)
    uploaded = json.loads(gh("api", f"repos/{REPO}/releases/{release['id']}").stdout)
    assets = {a["name"]: a for a in uploaded["assets"]}
    if set(assets) != names:
        raise RuntimeError("Draft contains missing or unexpected assets")
    for file in files:
        if assets[file.name].get("digest") != "sha256:" + hashes[file.name] or assets[file.name]["size"] != file.stat().st_size:
            raise RuntimeError("Uploaded asset verification failed: " + file.name)
    gh("release", "edit", args.tag, "--repo", REPO, "--draft=false", "--prerelease", "--latest=false")
    published = json.loads(gh("api", f"repos/{REPO}/releases/{release['id']}").stdout)
    if published["draft"]:
        raise RuntimeError("Release is still a draft")
    print(json.dumps({"url": published["html_url"], "assets_verified": len(files), "source_commit": commit}))


if __name__ == "__main__":
    main()
