import pytest
import pytest_asyncio
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import create_project, list_draft_sections, set_project_brief
from research_mcp.models import ResearchBrief
from research_mcp.server import read_draft_section, save_draft_section


@pytest_asyncio.fixture
async def project_db(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspaces"
    db_path = tmp_path / "research_mcp.db"
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(workspace_root))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(db_path))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    project = await create_project(conn, settings.workspace_dir, "Draft Tools")
    brief = ResearchBrief(
        style_id="article_imrad",
        title="Draft Tools",
        research_question="Does drafting require a brief?",
        citation="apa",
    )
    await set_project_brief(conn, project.id, brief.model_dump_json(), "article_imrad")
    await conn.close()
    return project, workspace_root


@pytest.mark.asyncio
async def test_save_and_read_draft_section(project_db):
    project, workspace_root = project_db
    content = "# Abstract\n\nThis is the abstract body with several words."

    saved = await save_draft_section(project.id, "01_abstract.md", content)
    read_back = await read_draft_section(project.id, "01_abstract.md")

    assert saved["filename"] == "01_abstract.md"
    assert saved["title"] == "Abstract"
    assert saved["word_count"] == len(content.split())
    assert read_back["content"] == content
    assert (workspace_root / project.slug / "drafts" / "01_abstract.md").exists()


@pytest.mark.asyncio
async def test_save_draft_without_brief_returns_needs_input(project_db):
    project, workspace_root = project_db
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        unbriefed = await create_project(conn, settings.workspace_dir, "Unbriefed")
    finally:
        await conn.close()

    result = await save_draft_section(unbriefed.id, "01_abstract.md", "# Abstract\n\nBody.")

    assert result["status"] == "needs_input"
    assert result["missing"] == ["style_id", "title", "research_question", "citation"]
    assert result["ask_user"]
    assert result["defaults"]["citation"] == "apa"
    assert not (workspace_root / unbriefed.slug / "drafts" / "01_abstract.md").exists()


@pytest.mark.asyncio
async def test_save_draft_rejects_path_traversal(project_db):
    project, _ = project_db

    with pytest.raises(ToolError):
        await save_draft_section(project.id, "../escape.md", "body")
    with pytest.raises(ToolError):
        await save_draft_section(project.id, "a/b.md", "body")


@pytest.mark.asyncio
async def test_resaving_existing_draft_preserves_its_order(project_db):
    project, _ = project_db
    await save_draft_section(project.id, "01_abstract.md", "# Abstract\nold", order=1)
    await save_draft_section(project.id, "02_intro.md", "# Intro\nold", order=2)

    await save_draft_section(project.id, "01_abstract.md", "# Abstract\nnew")

    conn = await get_connection(get_settings().db_path)
    try:
        sections = await list_draft_sections(conn, project.id)
    finally:
        await conn.close()
    assert [(s["filename"], s["section_order"]) for s in sections] == [
        ("01_abstract.md", 1),
        ("02_intro.md", 2),
    ]


@pytest.mark.asyncio
async def test_second_save_updates_word_count_without_duplicate_file(project_db):
    project, workspace_root = project_db
    await save_draft_section(project.id, "01_abstract.md", "short")

    await save_draft_section(project.id, "01_abstract.md", "a much longer second body now")

    conn = await get_connection(get_settings().db_path)
    try:
        sections = await list_draft_sections(conn, project.id)
    finally:
        await conn.close()
    assert len(sections) == 1
    drafts_dir = workspace_root / project.slug / "drafts"
    assert len(list(drafts_dir.glob("*.md"))) == 1
