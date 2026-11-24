import logging
import sys
from pathlib import Path

from research_mcp.config import Settings, configure_logging, get_settings


def test_default_output_root(monkeypatch):
    monkeypatch.delenv("RESEARCH_MCP_WORKSPACE", raising=False)
    settings = Settings()
    assert settings.output_root == (Path.home() / ".research-mcp" / "workspaces").resolve()


def test_default_output_root_source_is_default():
    assert Settings().output_root_source == "default"


def test_output_root_override_from_env_is_resolved_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", "relative/workspace")
    settings = Settings()
    assert settings.output_root.is_absolute()
    assert settings.output_root == (tmp_path / "relative/workspace").resolve()
    assert settings.workspace_dir == settings.output_root
    assert settings.output_root_source == "env"


def test_legacy_workspace_env_is_still_honored(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "legacy" / "workspaces"))
    settings = Settings()
    assert settings.output_root == (tmp_path / "legacy" / "workspaces").resolve()
    assert settings.output_root_source == "env-legacy"


def test_new_output_root_env_beats_legacy_workspace_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "legacy"))
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "new"))
    assert Settings().output_root == (tmp_path / "new").resolve()


def test_db_path_does_not_follow_a_relocated_output_root(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "custom" / "output"))
    monkeypatch.delenv("RESEARCH_MCP_DB_PATH", raising=False)
    settings = Settings()
    assert settings.db_path == (Path.home() / ".research-mcp" / "research_mcp.db").resolve()


def test_db_path_explicit_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "custom" / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "elsewhere" / "db.sqlite"))
    settings = Settings()
    assert settings.db_path == (tmp_path / "elsewhere" / "db.sqlite").resolve()


def test_email_defaults_to_none_when_unset(monkeypatch):
    monkeypatch.delenv("RESEARCH_MCP_EMAIL", raising=False)
    settings = Settings()
    assert settings.email is None


def test_email_read_from_env(monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_EMAIL", "rezalkaryanto@gmail.com")
    settings = Settings()
    assert settings.email == "rezalkaryanto@gmail.com"


def test_email_read_from_dotenv_file(monkeypatch, tmp_path):
    monkeypatch.delenv("RESEARCH_MCP_EMAIL", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("RESEARCH_MCP_EMAIL=from-dotenv@example.com\n")
    settings = Settings()
    assert settings.email == "from-dotenv@example.com"


def test_log_level_defaults_to_info(monkeypatch):
    monkeypatch.delenv("RESEARCH_MCP_LOG_LEVEL", raising=False)
    settings = Settings()
    assert settings.log_level == "INFO"


def test_get_settings_returns_settings_instance():
    assert isinstance(get_settings(), Settings)


def test_configure_logging_attaches_only_stderr_handlers():
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    configure_logging("DEBUG")
    assert len(root.handlers) >= 1
    for handler in root.handlers:
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is sys.stderr


def test_configure_logging_is_idempotent():
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    configure_logging("INFO")
    first_count = len(root.handlers)
    configure_logging("INFO")
    assert len(root.handlers) == first_count


def test_ocr_language_defaults_to_ind_eng(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RESEARCH_MCP_OCR_LANGUAGE", raising=False)
    assert Settings().ocr_language == "ind+eng"


def test_ocr_language_read_from_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RESEARCH_MCP_OCR_LANGUAGE", "eng")
    assert Settings().ocr_language == "eng"


def test_ocr_fallback_defaults_true_and_reads_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RESEARCH_MCP_OCR_FALLBACK", raising=False)
    assert Settings().ocr_fallback is True

    monkeypatch.setenv("RESEARCH_MCP_OCR_FALLBACK", "false")
    assert Settings().ocr_fallback is False
