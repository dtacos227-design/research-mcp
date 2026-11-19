import logging
import sys
from pathlib import Path

from research_mcp.config import Settings, configure_logging, get_settings


def test_default_workspace_dir_is_home_research_mcp_workspaces(monkeypatch):
    monkeypatch.delenv("RESEARCH_MCP_WORKSPACE", raising=False)
    settings = Settings()
    assert settings.workspace_dir == Path.home() / ".research-mcp" / "workspaces"


def test_workspace_dir_override_from_env_is_resolved_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", "relative/workspace")
    settings = Settings()
    assert settings.workspace_dir.is_absolute()
    assert settings.workspace_dir == (tmp_path / "relative/workspace").resolve()


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
