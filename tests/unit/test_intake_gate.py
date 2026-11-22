import pytest
import pytest_asyncio
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp import server
from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import (
    create_project,
    get_project,
    get_project_brief,
    set_project_style,
)


@pytest_asyncio.fixture
async def project_id(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        project = await create_project(conn, settings.workspace_dir, "Intake Gate")
    finally:
        await conn.close()
    return project.id


async def _project_dir(pid: str):
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        project = await get_project(conn, pid)
    finally:
        await conn.close()
    return settings.workspace_dir / project.slug


async def _stored_brief(pid: str):
    conn = await get_connection(get_settings().db_path)
    try:
        return await get_project_brief(conn, pid)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_start_research_without_brief_needs_input(project_id):
    result = await server.start_research(project_id)

    assert result["status"] == "needs_input"
    assert result["missing"] == ["style_id", "title", "research_question", "citation"]
    assert result["defaults"] == {"citation": "apa", "language": "id"}
    assert len(result["ask_user"]) == len(result["missing"])
    assert all(" / " in prompt for prompt in result["ask_user"])

    drafts = await _project_dir(project_id) / "drafts"
    assert not drafts.exists() or not list(drafts.glob("*.md"))


@pytest.mark.asyncio
async def test_start_research_incomplete_brief_creates_nothing(project_id):
    await server.save_research_brief(project_id, "skripsi_s1", "Judul", "Rumusan?")

    result = await server.start_research(project_id)

    assert result["status"] == "needs_input"
    assert result["missing"] == ["citation"]
    # R1: citation is never auto-filled, so the gate offers the style default to confirm.
    assert result["defaults"]["citation"] == "apa"
    assert len(result["ask_user"]) == 1
    drafts = await _project_dir(project_id) / "drafts"
    assert not drafts.exists() or not list(drafts.glob("*.md"))


@pytest.mark.asyncio
async def test_save_brief_without_citation_keeps_it_empty(project_id):
    saved = await server.save_research_brief(project_id, "skripsi_s1", "Judul", "Rumusan?")

    assert saved["status"] == "needs_input"
    assert saved["missing"] == ["citation"]
    stored = await _stored_brief(project_id)
    assert stored["citation"] is None
    assert stored["style_id"] == "skripsi_s1"


@pytest.mark.asyncio
async def test_save_brief_persists_citation_verbatim_and_completes(project_id):
    saved = await server.save_research_brief(
        project_id,
        "skripsi_s1",
        "Judul",
        "Rumusan?",
        citation="ieee",
        language="bilingual",
        scope="Satu kampus",
        target_length="12000 kata",
        deadline="2026-12-31",
    )

    assert saved["status"] == "complete"
    assert saved["complete"] is True
    assert saved["missing"] == []
    stored = await _stored_brief(project_id)
    assert stored["citation"] == "ieee"
    assert stored["language"] == "bilingual"
    assert stored["scope"] == "Satu kampus"
    assert stored["target_length"] == "12000 kata"
    assert stored["deadline"] == "2026-12-31"

    conn = await get_connection(get_settings().db_path)
    try:
        assert (await get_project(conn, project_id)).style_id == "skripsi_s1"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_save_brief_strips_whitespace_and_treats_blank_as_missing(project_id):
    saved = await server.save_research_brief(
        project_id,
        " skripsi_s1 ",
        "  Judul Riset  ",
        "   ",
        citation="\t",
        scope="  ",
    )

    assert saved["status"] == "needs_input"
    assert saved["missing"] == ["research_question", "citation"]
    stored = await _stored_brief(project_id)
    assert stored["style_id"] == "skripsi_s1"
    assert stored["title"] == "Judul Riset"
    assert stored["research_question"] is None
    assert stored["citation"] is None
    assert stored["scope"] == ""


@pytest.mark.asyncio
async def test_save_brief_rejects_unknown_style(project_id):
    with pytest.raises(ToolError, match="Unknown research style"):
        await server.save_research_brief(project_id, "no_such_style", "Judul", "Rumusan?")

    assert await _stored_brief(project_id) is None


@pytest.mark.asyncio
async def test_get_research_brief_reports_missing_fields(project_id):
    fresh = await server.get_research_brief(project_id)
    assert fresh["complete"] is False
    assert fresh["missing"] == ["style_id", "title", "research_question", "citation"]
    assert fresh["brief"]["title"] is None

    await server.save_research_brief(project_id, "ieee_paper", "Judul", "Rumusan?")
    partial = await server.get_research_brief(project_id)
    assert partial["complete"] is False
    assert partial["missing"] == ["citation"]

    await server.save_research_brief(project_id, "ieee_paper", "Judul", "Rumusan?", citation="apa")
    updated = await server.get_research_brief(project_id)
    assert updated["complete"] is True
    assert updated["missing"] == []
    assert updated["brief"]["style_id"] == "ieee_paper"
    assert updated["brief"]["language"] == "id"


@pytest.mark.asyncio
async def test_start_research_unknown_project_raises(project_id):
    with pytest.raises(ToolError, match="not found"):
        await server.start_research("does-not-exist")


@pytest.mark.asyncio
async def test_resolve_style_prefers_param_then_project_then_default(project_id):
    conn = await get_connection(get_settings().db_path)
    try:
        default = await server._resolve_style_for_export(conn, project_id, None)
        assert default.id == "article_imrad"

        await set_project_style(conn, project_id, "ieee_paper")
        persisted = await server._resolve_style_for_export(conn, project_id, None)
        assert persisted.id == "ieee_paper"

        explicit = await server._resolve_style_for_export(conn, project_id, "skripsi_s1")
        assert explicit.id == "skripsi_s1"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_resolve_style_rejects_unknown_style(project_id):
    conn = await get_connection(get_settings().db_path)
    try:
        with pytest.raises(ToolError, match="Unknown research style"):
            await server._resolve_style_for_export(conn, project_id, "no_such_style")
    finally:
        await conn.close()
