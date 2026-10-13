"""Fetch the exact official Electron binary used by Forge without changing locks."""

import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
NAME = "electron-v44.7.0-win32-x64.zip"


def prepare():
    locked = json.loads((ROOT / "packaging/downloads.lock.json").read_text(encoding="utf-8"))[NAME]
    directory = ROOT / "packaging/cache"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / NAME
    if not target.exists():
        temporary = target.with_suffix(".zip.part")
        try:
            with urllib.request.urlopen(locked["url"], timeout=90) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            with temporary.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != locked["sha256"] or temporary.stat().st_size != locked["size"]:
                raise RuntimeError("Electron distribution checksum or size mismatch")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    with target.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != locked["sha256"] or target.stat().st_size != locked["size"]:
        raise RuntimeError("Cached Electron distribution checksum or size mismatch")
    return target


if __name__ == "__main__":
    print(json.dumps({"electron_distribution": str(prepare()), "verified": True}))
