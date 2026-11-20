from pathlib import Path

import aiosqlite

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
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
    external_id TEXT,
    item_type TEXT,
    authors_structured_json TEXT,
    bibtex_raw TEXT,
    csl_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, citekey),
    UNIQUE(project_id, doi)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_source_external
    ON papers(project_id, source_provider, external_id)
    WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS paper_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    section_order INTEGER NOT NULL,
    heading TEXT NOT NULL,
    content TEXT NOT NULL,
    word_count INTEGER DEFAULT 0
);

-- Regular (not external-content) FTS5 table: project_id is not a column of
-- paper_sections, so external-content mode cannot serve it. The table keeps its
-- own copy of the indexed text, kept in sync by the triggers below.
CREATE VIRTUAL TABLE IF NOT EXISTS paper_sections_fts USING fts5(
    heading, content, paper_id UNINDEXED, project_id UNINDEXED
);

CREATE TRIGGER IF NOT EXISTS paper_sections_ai AFTER INSERT ON paper_sections BEGIN
    INSERT INTO paper_sections_fts(rowid, heading, content, paper_id, project_id)
    VALUES (new.id, new.heading, new.content, new.paper_id,
            (SELECT project_id FROM papers WHERE id = new.paper_id));
END;

CREATE TRIGGER IF NOT EXISTS paper_sections_ad AFTER DELETE ON paper_sections BEGIN
    DELETE FROM paper_sections_fts WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS paper_sections_au AFTER UPDATE ON paper_sections BEGIN
    DELETE FROM paper_sections_fts WHERE rowid = old.id;
    INSERT INTO paper_sections_fts(rowid, heading, content, paper_id, project_id)
    VALUES (new.id, new.heading, new.content, new.paper_id,
            (SELECT project_id FROM papers WHERE id = new.paper_id));
END;

CREATE TABLE IF NOT EXISTS draft_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    section_order INTEGER NOT NULL,
    title TEXT,
    word_count INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, filename)
);
"""


async def _migrate_legacy_schema(conn: aiosqlite.Connection) -> None:
    """Add columns to a papers table created before these columns existed."""
    async with conn.execute("PRAGMA table_info(papers)") as cursor:
        columns = {row[1] async for row in cursor}
    if not columns:
        return
    additions = {
        "external_id": "TEXT",
        "item_type": "TEXT DEFAULT 'other'",
        "authors_structured_json": "TEXT",
    }
    for column, column_type in additions.items():
        if column not in columns:
            await conn.execute(f"ALTER TABLE papers ADD COLUMN {column} {column_type}")
    await conn.commit()


async def get_connection(db_path: Path) -> aiosqlite.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await _migrate_legacy_schema(conn)
    await conn.executescript(SCHEMA_SQL)
    await conn.commit()
    return conn
