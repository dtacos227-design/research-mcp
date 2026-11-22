"""Load research style specifications from bundled YAML and user overrides."""

import os
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from research_mcp.exporters.templates import resolve_docx_template, resolve_latex_template
from research_mcp.styles.models import StyleSpec

USER_STYLES_DIR_ENV = "RESEARCH_MCP_STYLES_DIR"

_BUNDLED_PACKAGE = "research_mcp.templates"
_BUNDLED_SUBDIR = "styles"
_STYLE_SUFFIXES = (".yaml", ".yml")

_cache: dict[str, StyleSpec] | None = None


def _default_user_styles_dir() -> Path:
    return Path.home() / ".research-mcp" / "styles"


def _user_styles_dir() -> Path:
    raw = os.environ.get(USER_STYLES_DIR_ENV, "").strip()
    directory = Path(raw).expanduser() if raw else _default_user_styles_dir().expanduser()
    return directory.resolve()


def _describe_validation_error(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
        for error in exc.errors()
    )


def _validate_export(spec: StyleSpec, path: Path) -> None:
    resolvers = (
        ("docx_template", resolve_docx_template),
        ("latex_template", resolve_latex_template),
    )
    for key, resolve in resolvers:
        value = spec.export.get(key)
        if value is None:
            continue
        try:
            resolve(value)
        except ToolError as exc:
            raise ToolError(f"Invalid style file {path}: {exc}") from exc


def _load_style_file(path: Path) -> StyleSpec:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolError(f"Could not read style file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ToolError(f"Invalid YAML in style file {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ToolError(
            f"Invalid style file {path}: expected a YAML mapping with style fields "
            f"(id, label, outline), got {type(raw).__name__}."
        )

    try:
        spec = StyleSpec.model_validate(raw)
    except ValidationError as exc:
        raise ToolError(f"Invalid style file {path}: {_describe_validation_error(exc)}") from exc

    if not spec.outline:
        raise ToolError(f"Invalid style file {path}: outline must define at least one section.")
    _validate_export(spec, path)
    return spec


def _load_style_dir(directory: Path, *, required: bool) -> dict[str, StyleSpec]:
    styles: dict[str, StyleSpec] = {}
    if not directory.is_dir():
        if required:
            raise ToolError(f"Bundled styles directory not found: {directory}")
        return styles

    root = directory.resolve()
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise ToolError(f"Could not read styles directory {directory}: {exc}") from exc
    for path in entries:
        if path.suffix.lower() not in _STYLE_SUFFIXES or not path.is_file():
            continue
        # Guard against symlinks pointing outside the styles directory.
        if path.resolve().parent != root:
            raise ToolError(f"Refusing to load style file outside {root}: {path}")
        spec = _load_style_file(path)
        styles[spec.id] = spec
    return styles


def _load_bundled_styles() -> dict[str, StyleSpec]:
    resource = resources.files(_BUNDLED_PACKAGE) / _BUNDLED_SUBDIR
    if not resource.is_dir():
        raise ToolError(f"Bundled styles directory not found: {resource}")
    with resources.as_file(resource) as path:
        return _load_style_dir(Path(path), required=True)


def _load_user_styles() -> dict[str, StyleSpec]:
    directory = _user_styles_dir()
    if os.environ.get(USER_STYLES_DIR_ENV, "").strip() and not directory.is_dir():
        raise ToolError(
            f"{USER_STYLES_DIR_ENV} points to {directory}, which is not a directory. "
            f"Create it, or unset the variable to use the default {_default_user_styles_dir()}."
        )
    return _load_style_dir(directory, required=False)


def _read_styles() -> dict[str, StyleSpec]:
    global _cache
    if _cache is None:
        styles = _load_bundled_styles()
        # User styles are merged by id and win over the bundled definitions.
        styles.update(_load_user_styles())
        _cache = styles
    return _cache


def reload_styles() -> None:
    """Re-read bundled + user YAML, dropping the cached styles first.

    Loading is eager so an invalid style file surfaces its ``ToolError`` here
    rather than on the next lookup.
    """
    global _cache
    _cache = None
    _read_styles()


def list_styles() -> list[dict[str, Any]]:
    """Summarize every available style, sorted by id."""
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "description": spec.description,
            "sections": [
                {
                    "id": section.id,
                    "title": section.title,
                    "filename": section.filename,
                    "order": section.order,
                    "kind": section.kind,
                }
                for section in spec.outline
            ],
        }
        for spec in sorted(_read_styles().values(), key=lambda style: style.id)
    ]


def get_style(style_id: str) -> StyleSpec:
    """Return the full style specification, or raise ToolError when it is unknown."""
    styles = _read_styles()
    try:
        return styles[style_id]
    except KeyError:
        available = ", ".join(sorted(styles)) or "(none)"
        raise ToolError(
            f"Unknown research style {style_id!r}; available styles: {available}. "
            f"Add a custom style as YAML in {_user_styles_dir()} and call reload_styles()."
        ) from None
