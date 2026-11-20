"""Post-processing of exported `.docx` files via python-docx."""

from pathlib import Path


def polish_docx(path: Path, title: str) -> None:
    """Set the document title in `.docx` core properties."""
    from docx import Document

    document = Document(str(path))
    document.core_properties.title = title
    document.save(str(path))
