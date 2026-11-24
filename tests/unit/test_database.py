from pathlib import Path

import aiosqlite
import pytest

from research_mcp.config import Settings
from research_mcp.db import repository
from research_mcp.db.database import get_connection


def test_db_path_default_is_fixed_not_derived_from_output_root(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "anywhere" / "workspaces"))
    monkeypatch.delenv("RESEARCH_MCP_DB_PATH", raising=False)
    settings = Settings()
    assert settings.db_path == (Path.home() / ".research-mcp" / "research_mcp.db").resolve()
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
async def test_legacy_papers_without_item_type_read_as_other(tmp_path):
    """A Fase-1/2 DB has papers but no item_type column; after migration the
    existing rows must read back as item_type='other', not crash the model."""
    db_path = tmp_path / "legacy.db"
    legacy = await aiosqlite.connect(db_path)
    await legacy.executescript(
        """
        CREATE TABLE papers (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            citekey TEXT NOT NULL,
            doi TEXT,
            title TEXT NOT NULL,
            authors_json TEXT NOT NULL,
            year INTEGER,
            venue TEXT,
            abstract TEXT,
            citation_count INTEGER DEFAULT 0,
            pdf_path TEXT,
            source_provider TEXT,
            bibtex_raw TEXT,
            csl_json TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(project_id, citekey),
            UNIQUE(project_id, doi)
        );
        """
    )
    await legacy.execute(
        "INSERT INTO papers (id, project_id, citekey, title, authors_json, created_at) "
        "VALUES ('p1','proj','doe2026legacy','Legacy Paper','[]','now')"
    )
    await legacy.commit()
    await legacy.close()

    conn = await get_connection(db_path)
    try:
        # Migration ran on open; the legacy row must not crash list_papers.
        cursor = await conn.execute("PRAGMA table_info(papers)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "item_type" in columns
        papers = await repository.list_papers(conn, "proj")
        assert len(papers) == 1
        assert papers[0].item_type == "other"
    finally:
        await conn.close()


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
