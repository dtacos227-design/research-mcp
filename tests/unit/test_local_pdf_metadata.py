"""Imported PDFs should not be titled after their file name."""

import pytest
import pytest_asyncio

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import create_project
from research_mcp.parsers import ingestion
from research_mcp.parsers.pdf_extractor import PdfMetadata


@pytest_asyncio.fixture
async def project(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    created = await create_project(conn, settings.workspace_dir, "Metadata Project")
    yield conn, created, settings
    await conn.close()


def _stub_pdf_io(monkeypatch, *, metadata: PdfMetadata, markdown: str) -> None:
    def fake_import_pdf(source, destination, project_dir):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-1.4 dummy")

    monkeypatch.setattr(ingestion, "import_pdf", fake_import_pdf)
    monkeypatch.setattr(ingestion, "extract_pdf_metadata", lambda pdf, root: metadata)
    monkeypatch.setattr(ingestion, "extract_markdown", lambda pdf, root: markdown)


@pytest.mark.asyncio
async def test_missing_pdf_title_falls_back_to_first_markdown_heading(
    project, tmp_path, monkeypatch
):
    conn, created, settings = project
    dummy = tmp_path / "yolo2016.pdf"
    dummy.write_bytes(b"%PDF-1.4 dummy")
    _stub_pdf_io(
        monkeypatch,
        metadata=PdfMetadata(title=None, author=None, year=2016),
        markdown="# You Only Look Once: Unified, Real-Time Object Detection\n\nBody.",
    )

    result = await ingestion.ingest_local_pdf(conn, created.id, settings.workspace_dir, dummy, None)

    assert result["title"] == "You Only Look Once: Unified, Real-Time Object Detection"


@pytest.mark.asyncio
async def test_file_stem_title_is_replaced_by_markdown_heading(project, tmp_path, monkeypatch):
    conn, created, settings = project
    dummy = tmp_path / "rtdetr2023.pdf"
    dummy.write_bytes(b"%PDF-1.4 dummy")
    _stub_pdf_io(
        monkeypatch,
        metadata=PdfMetadata(title="rtdetr2023", author=None, year=2024),
        markdown="# DETRs Beat YOLOs on Real-time Object Detection\n\nBody.",
    )

    result = await ingestion.ingest_local_pdf(conn, created.id, settings.workspace_dir, dummy, None)

    assert result["title"] == "DETRs Beat YOLOs on Real-time Object Detection"


@pytest.mark.asyncio
async def test_real_pdf_title_is_kept(project, tmp_path, monkeypatch):
    conn, created, settings = project
    dummy = tmp_path / "paper.pdf"
    dummy.write_bytes(b"%PDF-1.4 dummy")
    _stub_pdf_io(
        monkeypatch,
        metadata=PdfMetadata(
            title="PKLot – A robust dataset", author="Paulo de Almeida", year=2015
        ),
        markdown="# A Different Running Head\n\nBody.",
    )

    result = await ingestion.ingest_local_pdf(conn, created.id, settings.workspace_dir, dummy, None)

    assert result["title"] == "PKLot – A robust dataset"
    assert result["authors"] == ["Paulo de Almeida"]
