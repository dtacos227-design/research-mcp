import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import anyio
import pymupdf
import pytest
import pytest_asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import create_project
from research_mcp.parsers import ingestion
from research_mcp.providers.base import SearchResult
from research_mcp.server import import_local_pdf, save_paper_to_project


def parse_list_tool_result(result) -> list[dict]:
    return [json.loads(block.text) for block in result.content]


def make_pdf(path: Path, text: str = "Local retrieval phrase for integration.") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()
    return path


@pytest_asyncio.fixture
async def project_db(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspaces"
    db_path = tmp_path / "research_mcp.db"
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(workspace_root))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(db_path))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    project = await create_project(conn, settings.workspace_dir, "Ingestion Tool Test")
    await conn.close()
    return project, settings.workspace_dir, settings.db_path


@pytest.mark.asyncio
async def test_import_local_pdf_stdio_round_trip(tmp_path: Path):
    source = make_pdf(tmp_path / "licensed-paper.pdf")
    workspace_root = tmp_path / "workspaces"
    db_path = tmp_path / "research_mcp.db"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "research_mcp.server"],
        env={
            "RESEARCH_MCP_WORKSPACE": str(workspace_root),
            "RESEARCH_MCP_DB_PATH": str(db_path),
        },
    )

    with anyio.fail_after(20):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                created = await session.call_tool("create_project", {"name": "PDF E2E"})
                assert created.is_error is False
                project = json.loads(created.content[0].text)

                imported = await session.call_tool(
                    "import_local_pdf",
                    {"project_id": project["id"], "file_path": str(source)},
                )
                assert imported.is_error is False
                payload = json.loads(imported.content[0].text)
                assert payload["import_status"] == "imported"
                assert payload["download_status"] == "not_applicable"
                assert payload["section_count"] > 0
                pdf_path = Path(payload["pdf_path"])
                assert pdf_path.is_relative_to(workspace_root / project["slug"])
                assert source.exists()

                sections = await session.call_tool(
                    "list_paper_sections",
                    {"project_id": project["id"], "citekey": payload["citekey"]},
                )
                assert sections.is_error is False
                headings = parse_list_tool_result(sections)
                assert [section["heading"] for section in headings]

                found = await session.call_tool(
                    "search_local_library",
                    {"project_id": project["id"], "query": "retrieval phrase"},
                )
                assert found.is_error is False
                results = parse_list_tool_result(found)
                assert results
                assert results[0]["citekey"] == payload["citekey"]


@pytest.mark.asyncio
async def test_save_paper_without_download_stores_metadata_only(project_db, monkeypatch):
    project, workspace_root, db_path = project_db
    monkeypatch.setattr(
        ingestion,
        "resolve_identifier",
        AsyncMock(
            return_value=SearchResult(
                source_provider="crossref",
                external_id="10.1/metadata",
                doi="10.1/metadata",
                title="Metadata Only",
                authors=["Alex Doe"],
                year=2024,
            )
        ),
    )
    monkeypatch.setattr(ingestion, "get_bibtex", AsyncMock(return_value=None))

    result = await save_paper_to_project(project.id, "10.1/metadata", download_pdf=False)

    assert result["title"] == "Metadata Only"
    assert result["download_status"] == "skipped"
    assert result["pdf_path"] is None
    assert list((workspace_root / project.slug / "pdfs").glob("*.pdf")) == []
    conn = await get_connection(db_path)
    try:
        async with conn.execute("SELECT COUNT(*) FROM paper_sections") as cursor:
            assert (await cursor.fetchone())[0] == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_invalid_doi_warning_does_not_prevent_local_import(
    project_db, tmp_path: Path, monkeypatch
):
    project, workspace_root, db_path = project_db
    source = make_pdf(tmp_path / "campus.pdf", "Campus license text.")
    monkeypatch.setattr(ingestion, "get_by_doi", AsyncMock(return_value=None))
    monkeypatch.setattr(ingestion, "get_bibtex", AsyncMock(return_value=None))

    result = await import_local_pdf(project.id, str(source), doi="10.1/not-found")

    assert result["import_status"] == "imported"
    assert any("Crossref has no metadata" in warning for warning in result["warnings"])
    assert Path(result["pdf_path"]).is_relative_to(workspace_root / project.slug)
    conn = await get_connection(db_path)
    try:
        async with conn.execute(
            "SELECT COUNT(*) FROM papers WHERE project_id = ?", (project.id,)
        ) as cursor:
            assert (await cursor.fetchone())[0] == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_invalid_local_pdf_returns_tool_error(project_db, tmp_path: Path):
    project, _, _ = project_db
    invalid = tmp_path / "invalid.pdf"
    invalid.write_text("not a PDF")

    with pytest.raises(ToolError, match="PDF"):
        await import_local_pdf(project.id, str(invalid))
