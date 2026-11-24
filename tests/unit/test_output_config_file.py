import pytest
from pathlib import Path

from research_mcp.config import (
    Settings,
    config_file_path,
    persist_output_root,
    read_config_file,
)


def test_config_file_path_follows_env(tmp_path, monkeypatch):
    target = tmp_path / "cfg" / "config.toml"
    monkeypatch.setenv("RESEARCH_MCP_CONFIG_FILE", str(target))
    assert config_file_path() == target


def test_config_file_value_is_used(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('output_root = "/tmp/from-config"\n', encoding="utf-8")
    monkeypatch.setenv("RESEARCH_MCP_CONFIG_FILE", str(cfg))
    settings = Settings()
    assert settings.output_root == Path("/tmp/from-config").resolve()
    assert settings.output_root_source == "config"


def test_env_beats_config_file(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('output_root = "/tmp/from-config"\n', encoding="utf-8")
    monkeypatch.setenv("RESEARCH_MCP_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", "/tmp/from-env")
    assert Settings().output_root == Path("/tmp/from-env").resolve()


def test_other_keys_in_config_file_are_accepted(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('output_root = "/tmp/x"\nlog_level = "DEBUG"\n', encoding="utf-8")
    monkeypatch.setenv("RESEARCH_MCP_CONFIG_FILE", str(cfg))
    assert Settings().log_level == "DEBUG"


def test_missing_config_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_CONFIG_FILE", str(tmp_path / "absent.toml"))
    assert Settings().output_root_source == "default"


def test_persist_round_trip_preserves_other_keys(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('log_level = "DEBUG"\n', encoding="utf-8")
    monkeypatch.setenv("RESEARCH_MCP_CONFIG_FILE", str(cfg))
    persist_output_root(tmp_path / "Riset")
    assert read_config_file()["log_level"] == "DEBUG"
    assert read_config_file()["output_root"] == str((tmp_path / "Riset").resolve())


def test_persist_creates_parent_directory(tmp_path, monkeypatch):
    cfg = tmp_path / "nested" / "dir" / "config.toml"
    monkeypatch.setenv("RESEARCH_MCP_CONFIG_FILE", str(cfg))
    persist_output_root(tmp_path / "Riset")
    assert cfg.is_file()


def test_persist_refuses_nested_tables(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[nested]\nkey = "v"\n', encoding="utf-8")
    monkeypatch.setenv("RESEARCH_MCP_CONFIG_FILE", str(cfg))
    with pytest.raises(ValueError, match="nested table"):
        persist_output_root(tmp_path / "Riset")
