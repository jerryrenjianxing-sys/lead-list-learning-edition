from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = "MediaWorkbench"
DISPLAY_NAME = "Media Deep Researcher"


def data_root() -> Path:
    return Path(
        os.environ.get("MEDIAWORKBENCH_DATA_DIR")
        or (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share"))
            / PRODUCT
            / "data"
        )
    ).resolve()


def discovery_path() -> Path:
    return Path(
        os.environ.get("MEDIAWORKBENCH_INSTANCE")
        or (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share"))
            / PRODUCT
            / "instance.json"
        )
    )
