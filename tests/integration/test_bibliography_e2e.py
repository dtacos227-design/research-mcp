from unittest.mock import AsyncMock

import bibtexparser
import pytest
import pytest_asyncio

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import create_project
from research_mcp.providers.aggregator import AggregatedSearch
from research_mcp.providers.base import SearchResult
from research_mcp.server import generate_bibliography, save_paper_to_project, verify_citations


@pytest_asyncio.fixture
async def arxiv_project(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspaces"
    db_path = tmp_path / "research_mcp.db"
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(workspace_root))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(db_path))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    project = await create_project(conn, settings.workspace_dir, "E2E Citations")
    await conn.close()

    found = SearchResult(
        source_provider="arxiv",
        external_id="1706.03762v7",
        doi=None,
        title="Attention Is All You Need",
        authors=["Ashish Vaswani"],
        year=2017,
        item_type="preprint",
        pdf_url="https://export.arxiv.org/pdf/1706.03762v7",
    )
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.search_all",
        AsyncMock(return_value=AggregatedSearch(results=[found], errors={})),
    )
    return project, workspace_root


@pytest.mark.asyncio
async def test_bibliography_and_verification_end_to_end(arxiv_project):
    project, workspace_root = arxiv_project

    saved = await save_paper_to_project(project.id, "1706.03762v7", download_pdf=False)
    citekey = saved["citekey"]

    bib_result = await generate_bibliography(project.id, formats=["bibtex"])
    bib_path = workspace_root / project.slug / "references.bib"
    parsed = bibtexparser.parse_string(bib_path.read_text())
    assert bib_result["paper_count"] == 1
    assert [entry.key for entry in parsed.entries] == [citekey]
    # Re-keyed to our citekey; arXiv has no raw BibTeX so the entry is built from
    # structured fields and must be @misc (preprint).
    assert parsed.entries[0].entry_type == "misc"

    verify = await verify_citations(project.id, markdown_text=f"[@{citekey}]")
    assert verify["cited_but_missing"] == []
    assert verify["uncited_papers"] == []
    assert verify["cited_and_found"] == [citekey]
