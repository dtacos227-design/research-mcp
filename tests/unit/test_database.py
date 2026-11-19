import pytest

from research_mcp.config import Settings
from research_mcp.db.database import get_connection


def test_db_path_defaults_next_to_workspace_dir(monkeypatch):
    monkeypatch.delenv("RESEARCH_MCP_WORKSPACE", raising=False)
    settings = Settings()
    assert settings.db_path == settings.workspace_dir.parent / "research_mcp.db"
    assert settings.db_path.is_absolute()


@pytest.mark.asyncio
async def test_get_connection_creates_all_tables(tmp_path):
    db_path = tmp_path / "sub" / "research_mcp.db"
    conn = await get_connection(db_path)
    try:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
        )
        names = {row[0] for row in await cursor.fetchall()}
        assert {"projects", "papers", "paper_sections", "draft_sections"} <= names
        assert "paper_sections_fts" in names or "paper_sections_fts_data" in names
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_get_connection_is_idempotent_across_calls(tmp_path):
    db_path = tmp_path / "research_mcp.db"
    conn1 = await get_connection(db_path)
    await conn1.close()
    conn2 = await get_connection(db_path)  # must not raise on existing schema
    await conn2.close()


@pytest.mark.asyncio
async def test_fts5_stays_in_sync_with_paper_sections(tmp_path):
    db_path = tmp_path / "research_mcp.db"
    conn = await get_connection(db_path)
    try:
        now = "2026-11-20T00:00:00Z"
        await conn.execute(
            "INSERT INTO projects (id, slug, name, created_at, updated_at) VALUES (?,?,?,?,?)",
            ("p1", "proj-one", "Proj One", now, now),
        )
        await conn.execute(
            "INSERT INTO papers (id, project_id, citekey, title, authors_json, created_at) "
            "VALUES (?,?,?,?,?,?)",
            ("pa1", "p1", "doe2026test", "Test Paper", "[]", now),
        )
        await conn.execute(
            "INSERT INTO paper_sections (paper_id, section_order, heading, content) "
            "VALUES (?,?,?,?)",
            ("pa1", 1, "Abstract", "quantum entanglement results"),
        )
        await conn.commit()

        cursor = await conn.execute(
            "SELECT project_id FROM paper_sections_fts WHERE paper_sections_fts MATCH ?",
            ("entanglement",),
        )
        rows = await cursor.fetchall()
        assert rows == [("p1",)]

        await conn.execute("DELETE FROM paper_sections WHERE paper_id = ?", ("pa1",))
        await conn.commit()
        cursor = await conn.execute(
            "SELECT * FROM paper_sections_fts WHERE paper_sections_fts MATCH ?",
            ("entanglement",),
        )
        assert await cursor.fetchall() == []
    finally:
        await conn.close()
