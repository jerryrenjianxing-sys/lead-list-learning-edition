import importlib.util
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("collect_delivery", Path(__file__).resolve().parents[1] / "packaging/collect_delivery.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_channel_package_filename_comes_from_feed(tmp_path):
    name = "MediaWorkbench.Desktop-0.2.0-win-preview-full.nupkg"
    (tmp_path / "releases.win-preview.json").write_text(json.dumps({"Assets": [{"PackageId": "MediaWorkbench.Desktop", "Version": "0.2.0", "Type": "Full", "FileName": name}]}))
    assert module.full_package(tmp_path, "0.2.0") == tmp_path / name
    with pytest.raises(ValueError):
        module.full_package(tmp_path, "0.1.1")


def test_feed_cannot_select_a_package_outside_release(tmp_path):
    (tmp_path / "releases.win-preview.json").write_text(json.dumps({"Assets": [{"PackageId": "MediaWorkbench.Desktop", "Version": "0.2.0", "Type": "Full", "FileName": "../outside-full.nupkg"}]}))
    with pytest.raises(ValueError):
        module.full_package(tmp_path, "0.2.0")
