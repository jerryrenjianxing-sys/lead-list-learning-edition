from __future__ import annotations
import os
import ntpath
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = "MediaWorkbench"
DISPLAY_NAME = "Media Deep Researcher"
_WINDOWS = os.name == "nt"


def filesystem_path(value: str | os.PathLike[str]) -> str:
    """Use Windows extended paths for filesystem access, without changing metadata."""
    value = os.fspath(value)
    if not _WINDOWS:
        return value
    if value.startswith("\\\\?\\"):
        return value
    absolute = ntpath.abspath(value)
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


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
