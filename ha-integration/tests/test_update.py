"""Tests for TV app and integration updates."""

import io
import json
import zipfile
from pathlib import Path

import pytest

from custom_components.ha_tv_pip_plus.update import (
    install_integration_zip,
    is_newer,
)


def test_is_newer() -> None:
    assert is_newer("1.36.0", "1.35.1")
    assert is_newer("v1.36.0", "1.35.9")
    assert not is_newer("1.35.1", "1.35.1")
    assert not is_newer("1.35.0", "1.35.1")
    assert not is_newer(None, "1.0.0")
    assert not is_newer("1.0.0", None)


def _zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_install_swaps_folder_and_keeps_backup(tmp_path: Path) -> None:
    target = tmp_path / "custom_components" / "ha_tv_pip_plus"
    target.mkdir(parents=True)
    (target / "old.py").write_text("old")
    data = _zip(
        {
            "manifest.json": json.dumps({"domain": "ha_tv_pip_plus", "version": "9"}),
            "__init__.py": "new",
            "translations/en.json": "{}",
        }
    )
    backup = tmp_path / ".backup"
    install_integration_zip(data, target, tmp_path / ".staging", backup)

    assert (target / "__init__.py").read_text() == "new"
    assert (target / "translations" / "en.json").exists()
    assert not (target / "old.py").exists()
    assert (backup / "old.py").read_text() == "old"


def test_install_rejects_wrong_integration(tmp_path: Path) -> None:
    target = tmp_path / "ha_tv_pip_plus"
    target.mkdir()
    data = _zip({"manifest.json": json.dumps({"domain": "something_else"})})
    with pytest.raises(Exception, match="different integration"):
        install_integration_zip(data, target, tmp_path / "s", tmp_path / "b")
    assert target.exists()


def test_install_rejects_unsafe_paths(tmp_path: Path) -> None:
    target = tmp_path / "ha_tv_pip_plus"
    target.mkdir()
    data = _zip(
        {
            "manifest.json": json.dumps({"domain": "ha_tv_pip_plus"}),
            "../evil.py": "x",
        }
    )
    with pytest.raises(Exception, match="unsafe"):
        install_integration_zip(data, target, tmp_path / "s", tmp_path / "b")
