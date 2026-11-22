"""`start_research` creates the draft skeleton from the chosen style outline."""

import pytest
import pytest_asyncio

from research_mcp import server
from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import create_project, get_project, list_draft_sections
from research_mcp.styles.registry import get_style


@pytest_asyncio.fixture
async def project_id(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        project = await create_project(conn, settings.workspace_dir, "Skeleton Project")
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


async def _draft_rows(pid: str):
    conn = await get_connection(get_settings().db_path)
    try:
        return await list_draft_sections(conn, pid)
    finally:
        await conn.close()


async def _complete_brief(pid: str) -> None:
    await server.save_research_brief(
        pid, "skripsi_s1", "Judul Skripsi", "Rumusan masalah?", citation="apa"
    )


@pytest.mark.asyncio
async def test_skeleton_creates_all_outline_files(project_id):
    await _complete_brief(project_id)

    result = await server.start_research(project_id)

    style = get_style("skripsi_s1")
    expected = [section.filename for section in sorted(style.outline, key=lambda s: s.order)]
    assert result["status"] == "created"
    assert result["created_files"] == expected
    assert "00_cover.md" in expected and "10_bab1_pendahuluan.md" in expected
    assert result["style_id"] == "skripsi_s1"
    assert result["guidance"] == style.guidance

    drafts = await _project_dir(project_id) / "drafts"
    assert {path.name for path in drafts.glob("*.md")} == set(expected)
    text = (drafts / "00_cover.md").read_text(encoding="utf-8")
    assert text.startswith("# Sampul\n")
    assert "<!-- guidance:" in text
    assert "Title, author name, study program" in text


@pytest.mark.asyncio
async def test_skeleton_registers_draft_rows_in_style_order(project_id):
    await _complete_brief(project_id)

    await server.start_research(project_id)

    style = get_style("skripsi_s1")
    expected = sorted(style.outline, key=lambda section: section.order)
    rows = await _draft_rows(project_id)
    assert [row["filename"] for row in rows] == [section.filename for section in expected]
    assert [row["section_order"] for row in rows] == [section.order for section in expected]
    assert [row["title"] for row in rows] == [section.title for section in expected]
    drafts = await _project_dir(project_id) / "drafts"
    for row in rows:
        content = (drafts / row["filename"]).read_text(encoding="utf-8")
        assert row["word_count"] == len(content.split())


@pytest.mark.asyncio
async def test_skeleton_refuses_overwrite(project_id):
    await _complete_brief(project_id)
    await server.start_research(project_id)
    drafts = await _project_dir(project_id) / "drafts"
    edited = drafts / "00_cover.md"
    edited.write_text("# Sampul\n\nTulisan asli pengguna.\n", encoding="utf-8")

    again = await server.start_research(project_id)

    assert again["status"] == "exists"
    assert again["conflicts"]
    assert "00_cover.md" in again["conflicts"]
    assert "10_bab1_pendahuluan.md" in again["conflicts"]
    assert edited.read_text(encoding="utf-8") == "# Sampul\n\nTulisan asli pengguna.\n"


@pytest.mark.asyncio
async def test_skeleton_conflict_blocks_every_write(project_id):
    await _complete_brief(project_id)
    drafts = await _project_dir(project_id) / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    (drafts / "10_bab1_pendahuluan.md").write_text("# Pendahuluan\n\nIsi saya.\n", encoding="utf-8")

    result = await server.start_research(project_id)

    assert result["status"] == "exists"
    assert result["conflicts"] == ["10_bab1_pendahuluan.md"]
    assert {path.name for path in drafts.glob("*.md")} == {"10_bab1_pendahuluan.md"}
    assert await _draft_rows(project_id) == []


@pytest.mark.asyncio
async def test_skeleton_overwrite_rewrites_existing_files(project_id):
    await _complete_brief(project_id)
    await server.start_research(project_id)
    drafts = await _project_dir(project_id) / "drafts"
    (drafts / "00_cover.md").write_text("# Rusak\n", encoding="utf-8")

    result = await server.start_research(project_id, overwrite=True)

    assert result["status"] == "created"
    restored = (drafts / "00_cover.md").read_text(encoding="utf-8")
    assert restored.startswith("# Sampul\n")
    assert "<!-- guidance:" in restored
    assert len(result["created_files"]) == len(get_style("skripsi_s1").outline)


@pytest.mark.asyncio
async def test_skeleton_treats_empty_files_as_missing(project_id):
    await _complete_brief(project_id)
    drafts = await _project_dir(project_id) / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    (drafts / "00_cover.md").write_text("", encoding="utf-8")

    result = await server.start_research(project_id)

    assert result["status"] == "created"
    assert "00_cover.md" in result["created_files"]
    assert (drafts / "00_cover.md").read_text(encoding="utf-8").startswith("# Sampul")
