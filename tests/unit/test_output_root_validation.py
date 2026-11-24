import pytest
from pathlib import Path

from research_mcp.config import validate_output_root


def test_creates_missing_directory(tmp_path):
    target = tmp_path / "a" / "b"
    assert validate_output_root(target) == target.resolve()
    assert target.is_dir()


def test_relative_path_resolves_against_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert validate_output_root("out") == (tmp_path / "out").resolve()
    assert (tmp_path / "out").is_dir()


def test_expands_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert validate_output_root("~/Riset") == (tmp_path / "Riset").resolve()


@pytest.mark.parametrize("bad", ["/"])
def test_filesystem_root_is_rejected(bad):
    with pytest.raises(ValueError, match="filesystem root"):
        validate_output_root(bad)


def test_existing_file_is_rejected(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        validate_output_root(target)


def test_unwritable_directory_is_rejected(tmp_path):
    target = tmp_path / "locked"
    target.mkdir()
    target.chmod(0o500)
    try:
        with pytest.raises(ValueError, match="not writable"):
            validate_output_root(target)
    finally:
        target.chmod(0o700)


def test_existing_directory_is_returned_unchanged(tmp_path):
    target = tmp_path / "Riset"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("x", encoding="utf-8")
    assert validate_output_root(target) == target.resolve()
    assert marker.is_file()


def test_nested_path_inside_a_file_is_rejected(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        validate_output_root(target / "sub")
