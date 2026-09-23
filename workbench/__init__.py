"""MediaWorkbench: local services for user-owned agents."""

import json
import tomllib
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
_build = _root / "build-version.json"
__version__ = (json.loads(_build.read_text(encoding="utf-8"))["version"] if _build.exists()
               else tomllib.loads((_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
