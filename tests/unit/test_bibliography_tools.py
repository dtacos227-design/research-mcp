import bibtexparser
import pytest
import pytest_asyncio

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import create_project, save_paper_and_sections
from research_mcp.models import PaperModel
from research_mcp.server import generate_bibliography, verify_citations


@pytest_asyncio.fixture
async def project_with_papers(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspaces"
    db_path = tmp_path / "research_mcp.db"
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(workspace_root))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(db_path))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    project = await create_project(conn, settings.workspace_dir, "Citations")

    raw = (
        "@article{some_crossref_key,\n"
        "  author = {Doe, Jane},\n"
        "  title = {Publisher Title},\n"
        "  journal = {Nature},\n"
        "  year = {2020},\n"
        "}\n"
    )
    paper_with_raw = PaperModel(
        id="p-raw",
        project_id=project.id,
        citekey="doe2024valid",
        doi="10.1/valid",
        title="Valid Paper",
        authors=["Jane Doe"],
        authors_structured=[{"given": "Jane", "family": "Doe", "literal": None}],
        year=2024,
        venue="Nature",
        item_type="journal-article",
        bibtex_raw=raw,
        created_at="2024-01-01T00:00:00Z",
    )
    paper_uncited = PaperModel(
        id="p-plain",
        project_id=project.id,
        citekey="roe2024plain",
        doi="10.1/plain",
        title="Plain Paper",
        authors=["John Roe"],
        year=2024,
        item_type="preprint",
        created_at="2024-01-02T00:00:00Z",
    )
    await save_paper_and_sections(conn, project.id, paper_with_raw, sections=None)
    await save_paper_and_sections(conn, project.id, paper_uncited, sections=None)
    await conn.close()
    return project, workspace_root


@pytest.mark.asyncio
async def test_generate_bibliography_writes_bib_with_our_citekeys(project_with_papers):
    project, workspace_root = project_with_papers

    result = await generate_bibliography(project.id, formats=["bibtex"])

    bib_path = workspace_root / project.slug / "references.bib"
    assert bib_path.exists()
    assert result["paper_count"] == 2
    parsed = bibtexparser.parse_string(bib_path.read_text())
    keys = {entry.key for entry in parsed.entries}
    assert keys == {"doe2024valid", "roe2024plain"}


@pytest.mark.asyncio
async def test_verify_citations_finds_missing_and_uncited(project_with_papers):
    project, _ = project_with_papers

    result = await verify_citations(project.id, markdown_text="[@doe2024valid]")

    assert result["cited_but_missing"] == []
    assert result["cited_and_found"] == ["doe2024valid"]
    assert [p["citekey"] for p in result["uncited_papers"]] == ["roe2024plain"]


@pytest.mark.asyncio
async def test_verify_citations_flags_fake_key(project_with_papers):
    project, _ = project_with_papers

    result = await verify_citations(project.id, markdown_text="[@fake9999]")

    assert result["cited_but_missing"] == ["fake9999"]
