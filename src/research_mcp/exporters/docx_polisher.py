"""Post-processing of exported `.docx` files via python-docx."""

from pathlib import Path

from research_mcp.exporters.formatting import apply_docx_formatting
from research_mcp.styles.models import FormattingSpec


def apply_formatting(path: Path, fmt: FormattingSpec) -> None:
    """Apply a style's page size, margins, font and spacing to an exported `.docx`."""
    apply_docx_formatting(path, fmt)


def polish_docx(path: Path, title: str) -> None:
    """Set the document title in `.docx` core properties."""
    from docx import Document

    document = Document(str(path))
    document.core_properties.title = title
    document.save(str(path))
