import pytest
import pytest_asyncio

from research_mcp.db.database import get_connection
from research_mcp.db.repository import (
    create_project,
    get_draft_section,
    list_draft_sections,
    upsert_draft_section,
)


@pytest_asyncio.fixture
async def db(tmp_path):
    conn = await get_connection(tmp_path / "research_mcp.db")
    project = await create_project(conn, tmp_path / "workspaces", "Draft Repo")
    yield conn, project
    await conn.close()


@pytest.mark.asyncio
async def test_upsert_then_get_draft_section(db):
    conn, project = db
    await upsert_draft_section(conn, project.id, "01_abstract.md", 1, "Abstract", 42)

    row = await get_draft_section(conn, project.id, "01_abstract.md")

    assert row is not None
    assert row["filename"] == "01_abstract.md"
    assert row["section_order"] == 1
    assert row["title"] == "Abstract"
    assert row["word_count"] == 42


@pytest.mark.asyncio
async def test_upsert_same_filename_updates_in_place(db):
    conn, project = db
    await upsert_draft_section(conn, project.id, "01_abstract.md", 1, "Abstract", 42)
    await upsert_draft_section(conn, project.id, "01_abstract.md", 1, "Abstract", 99)

    row = await get_draft_section(conn, project.id, "01_abstract.md")

    assert row["word_count"] == 99
    sections = await list_draft_sections(conn, project.id)
    assert len(sections) == 1


@pytest.mark.asyncio
async def test_list_draft_sections_orders_by_section_order(db):
    conn, project = db
    await upsert_draft_section(conn, project.id, "02_intro.md", 2, "Intro", 10)
    await upsert_draft_section(conn, project.id, "01_abstract.md", 1, "Abstract", 20)

    sections = await list_draft_sections(conn, project.id)

    assert [s["filename"] for s in sections] == ["01_abstract.md", "02_intro.md"]
