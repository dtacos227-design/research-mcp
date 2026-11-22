import aiosqlite
import pytest
import pytest_asyncio

from research_mcp.db.database import get_connection
from research_mcp.db.repository import (
    ProjectNotFoundError,
    create_project,
    get_project,
    get_project_brief,
    list_projects,
    set_project_brief,
    set_project_style,
)


@pytest_asyncio.fixture
async def db(tmp_path):
    conn = await get_connection(tmp_path / "r.db")
    project = await create_project(conn, tmp_path / "ws", "Brief")
    yield conn, project
    await conn.close()


@pytest.mark.asyncio
async def test_brief_roundtrip(db):
    conn, project = db
    await set_project_brief(conn, project.id, '{"title": "T"}', "skripsi_s1")
    brief = await get_project_brief(conn, project.id)
    assert brief == {"title": "T"}
    refreshed = await get_project(conn, project.id)
    assert refreshed.style_id == "skripsi_s1"


@pytest.mark.asyncio
async def test_brief_missing_returns_none(db):
    conn, project = db
    assert await get_project_brief(conn, project.id) is None


@pytest.mark.asyncio
async def test_new_project_has_no_style_id(db):
    conn, project = db
    assert project.style_id is None
    assert (await get_project(conn, project.id)).style_id is None


@pytest.mark.asyncio
async def test_set_project_style_updates_project(db):
    conn, project = db
    await set_project_style(conn, project.id, "ieee")
    assert (await get_project(conn, project.id)).style_id == "ieee"
    listed = {row["id"]: row for row in await list_projects(conn)}
    assert listed[project.id]["style_id"] == "ieee"
    await set_project_style(conn, project.id, None)
    assert (await get_project(conn, project.id)).style_id is None


@pytest.mark.asyncio
async def test_brief_accessors_raise_for_unknown_project(db):
    conn, _ = db
    with pytest.raises(ProjectNotFoundError):
        await set_project_brief(conn, "does-not-exist", '{"title": "T"}', "skripsi_s1")
    with pytest.raises(ProjectNotFoundError):
        await get_project_brief(conn, "does-not-exist")
    with pytest.raises(ProjectNotFoundError):
        await set_project_style(conn, "does-not-exist", "ieee")


@pytest.mark.asyncio
async def test_migration_adds_brief_columns_to_legacy_projects_table(tmp_path):
    db_path = tmp_path / "legacy.db"
    legacy = await aiosqlite.connect(db_path)
    await legacy.execute(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            slug TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    await legacy.execute("INSERT INTO projects VALUES ('p1', 'legacy', 'Legacy', '', 'now', 'now')")
    await legacy.commit()
    await legacy.close()

    conn = await get_connection(db_path)
    try:
        assert await get_project_brief(conn, "p1") is None
        await set_project_brief(conn, "p1", '{"title": "T"}', "skripsi_s1")
        assert await get_project_brief(conn, "p1") == {"title": "T"}
        assert (await get_project(conn, "p1")).style_id == "skripsi_s1"
    finally:
        await conn.close()
