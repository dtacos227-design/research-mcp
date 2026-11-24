import logging
import os
import sys
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_OUTPUT_ROOT = Path.home() / ".research-mcp" / "workspaces"
DEFAULT_DB_PATH = Path.home() / ".research-mcp" / "research_mcp.db"
LEGACY_OUTPUT_ROOT_ENV = "RESEARCH_MCP_WORKSPACE"
OUTPUT_ROOT_ENV = "RESEARCH_MCP_OUTPUT_ROOT"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        validate_default=True,
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="RESEARCH_MCP_",
    )

    output_root: Path = Field(default_factory=lambda: DEFAULT_OUTPUT_ROOT)
    output_root_source: str = Field(default="default")
    db_path: Path | None = Field(default=None, validation_alias="RESEARCH_MCP_DB_PATH")
    email: str | None = Field(default=None, validation_alias="RESEARCH_MCP_EMAIL")
    log_level: str = Field(default="INFO", validation_alias="RESEARCH_MCP_LOG_LEVEL")
    ocr_language: str = Field(default="ind+eng", validation_alias="RESEARCH_MCP_OCR_LANGUAGE")
    ocr_fallback: bool = Field(default=True, validation_alias="RESEARCH_MCP_OCR_FALLBACK")

    @model_validator(mode="before")
    @classmethod
    def _apply_legacy_output_root_env(cls, values: object) -> object:
        """Honour the pre-0.2 `RESEARCH_MCP_WORKSPACE` name.

        Applied before sources are merged so it behaves like any other env var:
        the new name still wins when both are set.
        """
        if not isinstance(values, dict):
            return values
        if "output_root" in values:
            return values
        if os.environ.get(OUTPUT_ROOT_ENV):
            return values
        legacy = os.environ.get(LEGACY_OUTPUT_ROOT_ENV)
        if not legacy:
            return values
        return {**values, "output_root": legacy}

    @field_validator("output_root", mode="after")
    @classmethod
    def _resolve_output_root(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @model_validator(mode="after")
    def _resolve_db_path(self) -> "Settings":
        if self.db_path is None:
            legacy_path = (self.output_root.parent / "research_mcp.db").resolve()
            use_legacy = not DEFAULT_DB_PATH.exists() and legacy_path.exists()
            path = legacy_path if use_legacy else DEFAULT_DB_PATH
        else:
            path = self.db_path
        self.db_path = path.expanduser().resolve()
        return self

    @model_validator(mode="after")
    def _detect_output_root_source(self) -> "Settings":
        if "output_root_source" in self.model_fields_set:
            return self
        if os.environ.get(OUTPUT_ROOT_ENV):
            self.output_root_source = "env"
        elif os.environ.get(LEGACY_OUTPUT_ROOT_ENV):
            self.output_root_source = "env-legacy"
        return self

    @property
    def workspace_dir(self) -> Path:
        """Deprecated alias kept for the pre-existing call sites and tests."""
        return self.output_root


def get_settings(cli_output_dir: Path | None = None) -> Settings:
    if cli_output_dir is None:
        return Settings()
    return Settings(output_root=cli_output_dir, output_root_source="cli")


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
