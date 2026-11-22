"""Blocking CPU/subprocess work must run off the event-loop thread."""

import threading

import pytest
import pytest_asyncio

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import create_project
from research_mcp.parsers import ingestion
from research_mcp.parsers.pdf_extractor import PdfMetadata
from research_mcp.server import generate_chart

MAIN_THREAD = threading.main_thread()


@pytest_asyncio.fixture
async def project(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    created = await create_project(conn, settings.workspace_dir, "IO Project")
    yield conn, created, settings
    await conn.close()


@pytest.mark.asyncio
async def test_pdf_parsing_runs_off_the_event_loop(project, tmp_path, monkeypatch):
    conn, created, settings = project
    dummy = tmp_path / "input.pdf"
    dummy.write_bytes(b"%PDF-1.4 dummy")
    threads: list[str] = []

    def fake_import_pdf(source, destination, project_dir):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-1.4 dummy")

    def fake_pdf_metadata(pdf_path, project_dir):
        return PdfMetadata(title="Off-thread Paper", author="Jane Doe", year=2024)

    def fake_extract_markdown(pdf_path, project_dir):
        threads.append(threading.current_thread().name)
        assert threading.current_thread() is not MAIN_THREAD
        return "# Off-thread Paper\n\nBody."

    monkeypatch.setattr(ingestion, "import_pdf", fake_import_pdf)
    monkeypatch.setattr(ingestion, "extract_pdf_metadata", fake_pdf_metadata)
    monkeypatch.setattr(ingestion, "extract_markdown", fake_extract_markdown)

    await ingestion.ingest_local_pdf(conn, created.id, settings.workspace_dir, dummy, None)

    assert threads, "extract_markdown was never called"


@pytest.mark.asyncio
async def test_chart_generation_runs_off_the_event_loop(project, monkeypatch):
    _conn, created, _settings = project
    from research_mcp import server as server_module

    def fake_run_chart(project_dir, filename, python_code, dpi):
        assert threading.current_thread() is not MAIN_THREAD
        return [{"path": "/tmp/figure.png", "markdown": "![figure](assets/figure.png)"}]

    monkeypatch.setattr(server_module, "run_chart", fake_run_chart)

    result = await generate_chart(created.id, "figure.png", "plt.plot([1], [1])", 150)

    assert result["assets"][0]["path"] == "/tmp/figure.png"
