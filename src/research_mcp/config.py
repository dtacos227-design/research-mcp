import json
import logging
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Literal

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
DB_PATH_ENV = "RESEARCH_MCP_DB_PATH"

logger = logging.getLogger(__name__)

OutputRootSource = Literal["cli", "env", "env-legacy", "config", "default"]
_BARE_TOML_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


class ConfigError(RuntimeError):
    """The configuration cannot be read; callers should surface it, not crash."""


_cli_output_root: Path | None = None


def set_cli_output_root(path: Path | None) -> None:
    """Pin the output root chosen on the command line for this process.

    Every tool calls `get_settings()` afresh, so a `--output-dir` that was not
    persisted (or not readable yet) has to live in module state to reach them.
    """
    global _cli_output_root
    _cli_output_root = path


def config_file_path() -> Path:
    raw = os.environ.get(CONFIG_FILE_ENV)
    path = Path(raw).expanduser() if raw else DEFAULT_CONFIG_FILE
    return path


def read_config_file() -> dict[str, object]:
    path = config_file_path()
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path} cannot be read: {exc}") from exc


def _format_toml_key(key: str) -> str:
    return key if _BARE_TOML_KEY.match(key) else json.dumps(key)


def _format_toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value))


def persist_output_root(path: Path) -> None:
    """Store `path` as the config-file default output root, atomically.

    Keys already in the file are kept. A nested table is refused rather than
    dropped, because writing it back is not something this tool can do losslessly.
    """
    target = config_file_path()
    existing = read_config_file()
    for key, value in existing.items():
        if isinstance(value, dict):
            raise ConfigError(
                f"{target} contains a nested table ([{key}]), which research-mcp cannot "
                "rewrite. Remove it and set `output_root` there by hand."
            )
    existing["output_root"] = str(Path(path).expanduser().resolve())

    lines = ["# Managed by research-mcp. `research-mcp --output-dir <path>` updates output_root."]
    for key, value in existing.items():
        lines.append(f"{_format_toml_key(key)} = {_format_toml_value(value)}")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.part")
    try:
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def validate_output_root(raw: str | Path) -> Path:
    """Resolve a user-supplied output folder, creating it when missing.

    Raises `ValueError` rather than returning a path the caller would only fail
    on later: a filesystem root (which would make `rmtree` catastrophic), an
    existing non-directory, or a folder this process cannot write to.
    """
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    resolved = candidate.resolve()

    if resolved == Path(resolved.anchor):
        raise ValueError(f"{resolved} is the filesystem root, which is not allowed.")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"{resolved} exists and is not a directory.")
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except NotADirectoryError as exc:
        raise ValueError(
            f"{resolved} cannot be created because a parent path is not a directory."
        ) from exc
    except OSError as exc:
        raise ValueError(f"{resolved} cannot be created: {exc}") from exc
    if not os.access(resolved, os.W_OK):
        raise ValueError(f"{resolved} is not writable by this process.")
    return resolved


def describe_root(path: Path) -> dict[str, bool]:
    """Report a root's usability without touching the filesystem."""
    exists = path.exists()
    is_dir = path.is_dir()
    if exists:
        writable = is_dir and os.access(path, os.W_OK)
    else:
        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        writable = os.access(probe, os.W_OK) and probe.is_dir()
    return {"exists": exists, "is_dir": is_dir, "writable": writable}


class _MappedTomlSource(TomlConfigSettingsSource):
    """TOML keys use field names; validation uses each field's env alias.

    Unknown keys are dropped with a warning: a typo in a hand-edited config file
    must not stop the server from starting.
    """

    def __call__(self) -> dict[str, object]:
        try:
            data = super().__call__() or {}
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{config_file_path()} is not valid TOML: {exc}") from exc

        fields = self.settings_cls.model_fields
        mapped: dict[str, object] = {}
        for key, value in data.items():
            field = fields.get(key)
            if field is None:
                logger.warning("ignoring unknown key %r in %s", key, config_file_path())
                continue
            alias = getattr(field, "validation_alias", None)
            mapped[alias if isinstance(alias, str) else key] = value
        return mapped


def _existing_db_candidates(output_root: Path) -> list[Path]:
    """Databases this process should adopt, most trustworthy first.

    An existing database always beats creating a fresh one: losing a user's
    projects is the failure this feature exists to prevent.
    """
    candidates: list[Path] = []
    legacy_root = os.environ.get(LEGACY_OUTPUT_ROOT_ENV)
    if legacy_root:
        candidates.append(Path(legacy_root).expanduser().resolve().parent / "research_mcp.db")
    candidates.append(DEFAULT_DB_PATH)
    candidates.append(output_root.parent / "research_mcp.db")
    return [candidate for candidate in candidates if candidate.exists()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        validate_default=True,
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="RESEARCH_MCP_",
    )

    output_root: Path = Field(default_factory=lambda: DEFAULT_OUTPUT_ROOT)
    output_root_source: OutputRootSource = Field(default="default")
    db_path: Path | None = Field(default=None, validation_alias=DB_PATH_ENV)
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

        Injected as an init value so it outranks the config file, matching the
        documented precedence (env beats file). The new name still wins, and a
        command-line choice outranks both.
        """
        if not isinstance(values, dict):
            return values
        if values.get("output_root_source") == "cli":
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
            existing = _existing_db_candidates(self.output_root)
            path = existing[0] if existing else DEFAULT_DB_PATH
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
    root = cli_output_dir if cli_output_dir is not None else _cli_output_root
    if root is None:
        return Settings()
    return Settings(output_root=root, output_root_source="cli")


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
