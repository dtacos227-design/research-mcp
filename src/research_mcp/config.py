import logging
import os
import sys
import tomllib
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

DEFAULT_OUTPUT_ROOT = Path.home() / ".research-mcp" / "workspaces"
DEFAULT_DB_PATH = Path.home() / ".research-mcp" / "research_mcp.db"
DEFAULT_CONFIG_FILE = Path.home() / ".research-mcp" / "config.toml"
LEGACY_OUTPUT_ROOT_ENV = "RESEARCH_MCP_WORKSPACE"
OUTPUT_ROOT_ENV = "RESEARCH_MCP_OUTPUT_ROOT"
CONFIG_FILE_ENV = "RESEARCH_MCP_CONFIG_FILE"


def config_file_path() -> Path:
    raw = os.environ.get(CONFIG_FILE_ENV)
    path = Path(raw).expanduser() if raw else DEFAULT_CONFIG_FILE
    return path


def read_config_file() -> dict[str, object]:
    path = config_file_path()
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _format_toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def persist_output_root(path: Path) -> None:
    """Store `path` as the config-file default output root.

    Keys already in the file are kept; a nested table is refused rather than
    dropped, because writing it back is not something this tool can do losslessly.
    """
    target = config_file_path()
    existing = read_config_file()
    for key, value in existing.items():
        if isinstance(value, dict):
            raise ValueError(
                f"{target} contains a nested table ([{key}]), which research-mcp cannot "
                "rewrite. Remove it and set `output_root` there by hand."
            )
    existing["output_root"] = str(Path(path).expanduser().resolve())

    lines = ["# Managed by research-mcp. `research-mcp --output-dir <path>` updates output_root."]
    for key, value in existing.items():
        lines.append(f"{key} = {_format_toml_value(value)}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


class _MappedTomlSource(TomlConfigSettingsSource):
    """TOML keys use field names; validation uses each field's env alias."""

    def __call__(self) -> dict[str, object]:
        data = super().__call__() or {}
        fields = self.settings_cls.model_fields
        mapped: dict[str, object] = {}
        for key, value in data.items():
            field = fields.get(key)
            alias = getattr(field, "validation_alias", None) if field is not None else None
            mapped[alias if isinstance(alias, str) else key] = value
        return mapped



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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            _MappedTomlSource(settings_cls, toml_file=str(config_file_path())),
            dotenv_settings,
            file_secret_settings,
        )

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
        elif "output_root" in read_config_file():
            self.output_root_source = "config"
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
