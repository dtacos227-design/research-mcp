"""Resolve bundled export templates by name."""

from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

CSL_STYLES = ("apa", "chicago", "ieee", "vancouver")
DOCX_TEMPLATES = ("default_academic",)
LATEX_TEMPLATES = ("article_id", "ieee")


def _pick(value: str, available: tuple[str, ...], kind: str) -> str:
    if value not in available:
        raise ToolError(f"Unknown {kind} {value!r}; available {kind}s are {list(available)!r}.")
    return value


@contextmanager
def bundled_template(relative: str) -> Iterator[Path]:
    """Yield a real filesystem path to a bundled template file."""
    resource = resources.files("research_mcp.templates") / relative
    with resources.as_file(resource) as path:
        yield Path(path)


def resolve_csl(style: str) -> str:
    return _pick(style, CSL_STYLES, "CSL style")


def resolve_docx_template(name: str) -> str:
    return _pick(name, DOCX_TEMPLATES, "DOCX template")


def resolve_latex_template(name: str) -> str:
    return _pick(name, LATEX_TEMPLATES, "LaTeX template")
