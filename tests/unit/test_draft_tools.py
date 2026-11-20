import pytest
import pytest_asyncio
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import create_project, list_draft_sections
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
async def test_save_draft_rejects_path_traversal(project_db):
    project, _ = project_db

    with pytest.raises(ToolError):
        await save_draft_section(project.id, "../escape.md", "body")
    with pytest.raises(ToolError):
        await save_draft_section(project.id, "a/b.md", "body")


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
