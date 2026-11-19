import logging
import sys
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        validate_default=True, env_file=".env", env_file_encoding="utf-8"
    )

    workspace_dir: Path = Field(
        default_factory=lambda: Path.home() / ".research-mcp" / "workspaces",
        validation_alias="RESEARCH_MCP_WORKSPACE",
    )
    db_path: Path = Field(
        default_factory=lambda: Path.home() / ".research-mcp" / "research_mcp.db",
        validation_alias="RESEARCH_MCP_DB_PATH",
    )
    email: str | None = Field(default=None, validation_alias="RESEARCH_MCP_EMAIL")
    log_level: str = Field(default="INFO", validation_alias="RESEARCH_MCP_LOG_LEVEL")

    @field_validator("workspace_dir", "db_path", mode="after")
    @classmethod
    def _resolve_paths(cls, value: Path) -> Path:
        return value.expanduser().resolve()


def get_settings() -> Settings:
    return Settings()


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)

    has_stderr_handler = any(
        isinstance(handler, logging.StreamHandler) and handler.stream is sys.stderr
        for handler in root.handlers
    )
    if has_stderr_handler:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
