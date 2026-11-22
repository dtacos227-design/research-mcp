"""CRUD tools: fix metadata, delete papers, drafts and projects."""

import pytest
import pytest_asyncio
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import (
    create_project,
    get_paper_by_citekey,
    list_draft_sections,
    save_paper_and_sections,
    set_project_brief,
)
from research_mcp.models import PaperModel, ResearchBrief
from research_mcp.server import (
    delete_draft_section,
    delete_paper,
    delete_project,
    save_draft_section,
    update_paper_metadata,
)


@pytest_asyncio.fixture
async def project(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    created = await create_project(conn, settings.workspace_dir, "CRUD Project")
    project_dir = settings.workspace_dir / created.slug

    pdf_path = project_dir / "pdfs" / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 dummy")
    parsed_path = project_dir / "parsed" / "paper.md"
    parsed_path.write_text("# Paper\n\nBody.", encoding="utf-8")

    paper = PaperModel(
        id="p-1",
        project_id=created.id,
        citekey="doe2024valid",
        doi="10.1/valid",
        title="Valid Paper",
        authors=["Jane Doe"],
        authors_structured=[{"given": "Jane", "family": "Doe", "literal": None}],
        year=2024,
        venue="Nature",
        item_type="journal-article",
        pdf_path=str(pdf_path),
        created_at="2024-01-01T00:00:00Z",
    )
    await save_paper_and_sections(conn, created.id, paper, sections=None)
    brief = ResearchBrief(
        style_id="skripsi_s1",
        title="CRUD Project",
        research_question="Can metadata be corrected?",
        citation="apa",
    )
    await set_project_brief(conn, created.id, brief.model_dump_json(), "skripsi_s1")
    await conn.commit()
    await conn.close()

    await save_draft_section(created.id, "01_intro.md", "# Pendahuluan\n\nKlaim [@doe2024valid].")
    await save_draft_section(created.id, "02_metode.md", "# Metode\n\nTeks tanpa sitasi.")

    return created, settings, project_dir


@pytest.mark.asyncio
async def test_update_paper_metadata_rewrites_fields_and_bibliography(project):
    created, settings, _project_dir = project

    result = await update_paper_metadata(
        created.id,
        "doe2024valid",
        title="Corrected Title",
        authors=["Jane Doe", "John Roe"],
        year=2025,
    )

    assert result["paper"]["title"] == "Corrected Title"
    assert result["paper"]["year"] == 2025
    conn = await get_connection(settings.db_path)
    try:
        stored = await get_paper_by_citekey(conn, created.id, "doe2024valid")
    finally:
        await conn.close()
    assert stored is not None
    assert stored.authors == ["Jane Doe", "John Roe"]
    bib = (settings.workspace_dir / created.slug / "references.bib").read_text(encoding="utf-8")
    assert "Corrected Title" in bib


@pytest.mark.asyncio
async def test_update_paper_metadata_renames_citekey_and_reports_stale_drafts(project):
    created, settings, _project_dir = project

    result = await update_paper_metadata(created.id, "doe2024valid", new_citekey="doe2025valid")

    assert result["paper"]["citekey"] == "doe2025valid"
    assert result["stale_draft_citations"] == ["01_intro.md"]
    conn = await get_connection(settings.db_path)
    try:
        assert await get_paper_by_citekey(conn, created.id, "doe2024valid") is None
        assert await get_paper_by_citekey(conn, created.id, "doe2025valid") is not None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_update_paper_metadata_rejects_unknown_citekey(project):
    created, _settings, _project_dir = project

    with pytest.raises(ToolError, match="not in project"):
        await update_paper_metadata(created.id, "missing2024key", title="Nope")


@pytest.mark.asyncio
async def test_update_paper_metadata_rejects_duplicate_citekey(project):
    created, _settings, _project_dir = project
    await save_draft_section(created.id, "03_extra.md", "# Extra\n\nTanpa sitasi.")
    conn = await get_connection(get_settings().db_path)
    try:
        duplicate = PaperModel(
            id="p-2",
            project_id=created.id,
            citekey="roe2024other",
            title="Other Paper",
            authors=["John Roe"],
            authors_structured=[{"given": "John", "family": "Roe", "literal": None}],
            year=2024,
            item_type="journal-article",
            created_at="2024-01-01T00:00:00Z",
        )
        await save_paper_and_sections(conn, created.id, duplicate, sections=None)
    finally:
        await conn.close()

    with pytest.raises(ToolError, match="already used"):
        await update_paper_metadata(created.id, "doe2024valid", new_citekey="roe2024other")


@pytest.mark.asyncio
async def test_update_paper_metadata_requires_a_field(project):
    created, _settings, _project_dir = project

    with pytest.raises(ToolError, match="at least one field"):
        await update_paper_metadata(created.id, "doe2024valid")


@pytest.mark.asyncio
async def test_delete_paper_removes_row_sections_and_artifacts(project):
    created, settings, project_dir = project

    result = await delete_paper(created.id, "doe2024valid")

    assert result["deleted"] is True
    assert result["cited_by"] == ["01_intro.md"]
    conn = await get_connection(settings.db_path)
    try:
        assert await get_paper_by_citekey(conn, created.id, "doe2024valid") is None
    finally:
        await conn.close()
    assert not (project_dir / "pdfs" / "paper.pdf").exists()
    assert not (project_dir / "parsed" / "paper.md").exists()


@pytest.mark.asyncio
async def test_delete_paper_rejects_unknown_citekey(project):
    created, _settings, _project_dir = project

    with pytest.raises(ToolError, match="not in project"):
        await delete_paper(created.id, "missing2024key")


@pytest.mark.asyncio
async def test_delete_draft_section_removes_file_and_row(project):
    created, settings, project_dir = project

    result = await delete_draft_section(created.id, "02_metode.md")

    assert result["deleted"] is True
    assert not (project_dir / "drafts" / "02_metode.md").exists()
    conn = await get_connection(settings.db_path)
    try:
        remaining = await list_draft_sections(conn, created.id)
    finally:
        await conn.close()
    assert [row["filename"] for row in remaining] == ["01_intro.md"]


@pytest.mark.asyncio
async def test_delete_draft_section_rejects_missing_file(project):
    created, _settings, _project_dir = project

    with pytest.raises(ToolError, match="No draft section"):
        await delete_draft_section(created.id, "99_missing.md")


@pytest.mark.asyncio
async def test_delete_project_requires_slug_confirmation(project):
    created, _settings, project_dir = project

    with pytest.raises(ToolError, match="confirm"):
        await delete_project(created.id, "DELETE")

    assert project_dir.exists()


@pytest.mark.asyncio
async def test_delete_project_removes_rows_and_workspace(project):
    created, settings, project_dir = project

    result = await delete_project(created.id, created.slug)

    assert result["deleted"] is True
    assert not project_dir.exists()
    from research_mcp.server import get_project_status

    with pytest.raises(ToolError):
        await get_project_status(created.id)
    conn = await get_connection(settings.db_path)
    try:
        async with conn.execute("SELECT COUNT(*) FROM draft_sections") as cursor:
            assert (await cursor.fetchone())[0] == 0
    finally:
        await conn.close()
