# Phase 1 — Database & Literature Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `research-mcp` a local SQLite+FTS5 database with project CRUD, and five free literature-search providers (OpenAlex, Semantic Scholar, arXiv, PubMed, Crossref) plus Unpaywall's OA-lookup, unified behind an aggregator with dedup and fault isolation — then expose all of it as six MCP tools: `create_project`, `list_projects`, `get_project_status`, `search_papers`, `get_citation_graph`, `resolve_doi`.

**Architecture:** `db/database.py` owns schema + connection (WAL, FTS5 with external-content sync triggers); `db/repository.py` is the only module that touches SQL. `providers/base.py` defines a `LiteratureProvider` ABC, a shared `SearchResult` model, and a retrying HTTP helper; each of `openalex.py`/`semantic_scholar.py`/`arxiv.py`/`pubmed.py`/`crossref.py` implements it; `unpaywall.py` stands alone (DOI-only lookup, no search). `providers/aggregator.py` fans out to requested providers with `asyncio.gather(..., return_exceptions=True)`, dedups by DOI then normalized title, and reports which providers failed. `server.py` grows six `@mcp.tool()` functions that are thin wrappers over repository/aggregator calls.

**Tech Stack:** `aiosqlite` (WAL + FTS5 external-content table), `httpx.AsyncClient` (one per call — compatible with `respx` mocking), `tenacity` for 429/503 backoff, `pydantic` v2 models, `mcp.server.mcpserver.exceptions.ToolError` for user-facing tool errors, `respx` for HTTP mocking in tests, stdlib `xml.etree.ElementTree` for arXiv/PubMed XML (no new XML dependency).

**Spec:** `AGENTS.md` (repo root) — §2.1 (tech stack), §2.3 (exact DB schema), §2.4 (directory layout), §4.1/§5.1 (CLI-safety, async fault isolation, polite-pool headers), §5.2 (tool design/anti-hallucination), §6.1.A–B (tool signatures 1–6), §7 Fase 1 (scope + verification criteria).

**Verified against live APIs during planning (2026-11-20), not assumed:** OpenAlex `works?search=` response shape (incl. `abstract_inverted_index`), Semantic Scholar Graph API v1 endpoint reachability (confirmed via a live 429 — the endpoint and param set are accepted; field shapes are Semantic Scholar's long-stable public contract), arXiv Atom feed shape, PubMed `esearch`/`esummary`/`efetch` shapes, Crossref `works?query.bibliographic=` shape and its `/works/{doi}/transform/application/x-bibtex` content-negotiated BibTeX endpoint, Unpaywall `v2/{doi}` shape including the `best_oa_location.url_for_pdf` sometimes being `null` while a different entry in `oa_locations` has it.

## Global Constraints

- DB schema is exactly `AGENTS.md` §2.3's five tables/virtual table — column names, types, and `UNIQUE` constraints copied verbatim.
- `httpx.AsyncClient` + `asyncio.gather(..., return_exceptions=True)` for multi-provider calls; one provider's failure never fails the whole search (`AGENTS.md` §5.1.2).
- OpenAlex, Crossref, and Unpaywall requests carry `User-Agent: ResearchMCP/0.1 (mailto:<RESEARCH_MCP_EMAIL>)` when an email is configured (`AGENTS.md` §5.1.3); all three also get the `mailto`/`email` query parameter, since that is each API's own documented polite-pool mechanism.
- `tenacity` exponential backoff on HTTP 429/503 (`AGENTS.md` §5.1.3).
- `search_papers` defaults to 10–15 results with abstracts truncated to ~300 characters (`AGENTS.md` §5.2.1); this plan uses exactly 300 and a default `limit` of 10, matching the tool signature in §6.1.4.
- Tool-level exceptions are caught and turned into actionable, structured errors (`AGENTS.md` §5.2.2) — this plan uses `mcp.server.mcpserver.exceptions.ToolError` (confirmed present in the installed `mcp==2.3.0` SDK) for every user-facing tool failure.
- Every provider has a unit test with mocked HTTP (`respx`) (`AGENTS.md` §5.3).
- `server.py` imports every tool dependency so its test-patch target is `research_mcp.server.<name>`: provider functions (`search_all`, `get_citations`, `get_references`, `get_by_doi`, `get_bibtex`, `get_oa_location`) are imported by bare name (`from research_mcp.providers.x import y`) since none collide with a tool name. `db.repository`'s functions **do** collide with tool names one-for-one (`create_project` tool vs. `create_project` repository function, etc.), so `server.py` imports that module qualified (`from research_mcp.db import repository`) and calls `repository.create_project(...)`, `repository.get_project(...)`, `repository.list_projects(...)`, `repository.ProjectNotFoundError` — this is Task 3's one non-obvious implementation decision.
- Phase exit requires `ruff check .`, `ruff format --check .`, `pytest`, **and** the live cross-provider integration test described in Task 15 (`AGENTS.md` §7 Fase 1: "tes integrasi pencarian nyata lintas sumber tanpa duplikasi").

## Review Focus

- A provider HTTP call that 404s/500s/times out must not take down `search_papers` for the other requested sources — the aggregator's `errors` map must name the failing source, and `results` must still contain whatever the healthy providers found. Covered in Task 11 (`test_one_provider_failing_does_not_drop_the_others`) and exercised end-to-end by Task 12's `search_papers` tool test.
- The same paper found by two providers (e.g. OpenAlex and arXiv both index the same preprint) must appear once in `search_papers`, not twice — dedup must work both when both results share a DOI and when one has no DOI but a near-identical title. Covered in Task 11 (`test_dedups_by_doi_keeping_first_seen`, `test_dedups_by_normalized_title_when_no_doi`).
- `get_project_status` / tools that take a `project_id` must raise a clear `ToolError`, not a raw `KeyError`/`IndexError`, when the project doesn't exist — a reasonable person typing a stale ID gets a message telling them that, not a traceback. Covered in Task 3.
- `create_project` called twice with the same (or slug-colliding) `name` must not violate the DB's `UNIQUE(slug)` constraint and crash — a reasonable person naming two projects similarly expects both to be created, with different slugs. Covered in Task 2.
- OpenAlex's `abstract_inverted_index` (not plain text) and PubMed's multi-`<AbstractText>` structured abstracts are both real response shapes the live checks above surfaced — a provider that doesn't reconstruct these ends up silently storing/returning no abstract for a large fraction of real papers. Covered in Tasks 5 and 7.

---

### Task 1: `db/database.py` — schema, WAL, FTS5 sync

**Files:**
- Create: `src/research_mcp/db/__init__.py` (empty)
- Create: `src/research_mcp/db/database.py`
- Test: `tests/unit/test_database.py`
- Modify: `src/research_mcp/config.py` — add `db_path: Path` field

**Interfaces:**
- Consumes: `research_mcp.config.get_settings() -> Settings` (Phase 0).
- Produces: `research_mcp.config.Settings.db_path: Path` (new field, default `workspace_dir.parent / "research_mcp.db"`, resolved absolute like `workspace_dir`). `async def get_connection(db_path: Path) -> aiosqlite.Connection` in `database.py` — opens (creating parent dirs), sets `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`, applies the full schema (idempotent `CREATE TABLE/TRIGGER IF NOT EXISTS`), and returns the open connection. Task 2 consumes `get_connection` by this exact name/signature.

- [ ] **Step 1: Write the failing tests**

```python
import aiosqlite
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_database.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'research_mcp.db'` (and `Settings` has no `db_path`).

- [ ] **Step 3: Add `db_path` to `Settings` in `config.py`**

Same pattern as `workspace_dir`: `Field(default_factory=lambda: Path.home() / ".research-mcp" / "research_mcp.db", validation_alias="RESEARCH_MCP_DB_PATH")`, resolved absolute by a `field_validator` identical in spirit to `workspace_dir`'s (`.expanduser().resolve()`). No new test is required for the override path itself — Phase 0's `workspace_dir` tests already pin that pattern; `test_db_path_defaults_next_to_workspace_dir` above is the one new assertion this task needs.

- [ ] **Step 4: Implement `database.py`**

```python
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
    bibtex_raw TEXT,
    csl_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, citekey),
    UNIQUE(project_id, doi)
);

CREATE TABLE IF NOT EXISTS paper_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    section_order INTEGER NOT NULL,
    heading TEXT NOT NULL,
    content TEXT NOT NULL,
    word_count INTEGER DEFAULT 0
);

CREATE VIRTUAL TABLE IF NOT EXISTS paper_sections_fts USING fts5(
    heading, content, paper_id UNINDEXED, project_id UNINDEXED,
    content='paper_sections', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS paper_sections_ai AFTER INSERT ON paper_sections BEGIN
    INSERT INTO paper_sections_fts(rowid, heading, content, paper_id, project_id)
    VALUES (new.id, new.heading, new.content, new.paper_id,
            (SELECT project_id FROM papers WHERE id = new.paper_id));
END;

CREATE TRIGGER IF NOT EXISTS paper_sections_ad AFTER DELETE ON paper_sections BEGIN
    INSERT INTO paper_sections_fts(paper_sections_fts, rowid, heading, content, paper_id, project_id)
    VALUES ('delete', old.id, old.heading, old.content, old.paper_id,
            (SELECT project_id FROM papers WHERE id = old.paper_id));
END;

CREATE TRIGGER IF NOT EXISTS paper_sections_au AFTER UPDATE ON paper_sections BEGIN
    INSERT INTO paper_sections_fts(paper_sections_fts, rowid, heading, content, paper_id, project_id)
    VALUES ('delete', old.id, old.heading, old.content, old.paper_id,
            (SELECT project_id FROM papers WHERE id = old.paper_id));
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


async def get_connection(db_path) -> aiosqlite.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.executescript(SCHEMA_SQL)
    await conn.commit()
    return conn
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_database.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add src/research_mcp/db/__init__.py src/research_mcp/db/database.py tests/unit/test_database.py src/research_mcp/config.py
git commit -m "feat: add SQLite+FTS5 schema and connection helper"
```

---

### Task 2: `db/repository.py` — project CRUD + workspace folders

**Files:**
- Create: `src/research_mcp/db/repository.py`
- Test: `tests/unit/test_repository.py`

**Interfaces:**
- Consumes: `get_connection(db_path) -> aiosqlite.Connection` (Task 1); `research_mcp.models.ProjectModel` (Phase 0).
- Produces: `class ProjectNotFoundError(Exception)`; `async def create_project(conn, workspace_root: Path, name: str, description: str = "") -> ProjectModel`; `async def get_project(conn, project_id: str) -> ProjectModel` (raises `ProjectNotFoundError` if missing); `async def list_projects(conn) -> list[dict]` (each dict is a `ProjectModel.model_dump()` plus `paper_count: int`, `draft_count: int`); `async def project_workspace_dir(workspace_root: Path, slug: str) -> Path`. Task 3 consumes all of these by these exact names.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
import pytest_asyncio

from research_mcp.db.database import get_connection
from research_mcp.db.repository import (
    ProjectNotFoundError,
    create_project,
    get_project,
    list_projects,
)


@pytest_asyncio.fixture
async def conn(tmp_path):
    connection = await get_connection(tmp_path / "research_mcp.db")
    yield connection
    await connection.close()


@pytest.mark.asyncio
async def test_create_project_creates_workspace_subdirs(tmp_path, conn):
    project = await create_project(conn, tmp_path / "workspaces", "My Research")
    assert project.slug == "my-research"
    workspace = tmp_path / "workspaces" / "my-research"
    for sub in ("pdfs", "parsed", "drafts", "assets", "scripts", "exports"):
        assert (workspace / sub).is_dir()


@pytest.mark.asyncio
async def test_create_project_with_colliding_name_gets_distinct_slug(tmp_path, conn):
    first = await create_project(conn, tmp_path / "workspaces", "Vision Study")
    second = await create_project(conn, tmp_path / "workspaces", "Vision Study")
    assert first.slug != second.slug
    assert second.slug.startswith("vision-study")


@pytest.mark.asyncio
async def test_get_project_raises_not_found_for_unknown_id(conn):
    with pytest.raises(ProjectNotFoundError):
        await get_project(conn, "does-not-exist")


@pytest.mark.asyncio
async def test_list_projects_reports_zero_counts_for_fresh_project(tmp_path, conn):
    await create_project(conn, tmp_path / "workspaces", "Fresh Project")
    projects = await list_projects(conn)
    assert len(projects) == 1
    assert projects[0]["paper_count"] == 0
    assert projects[0]["draft_count"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_mcp.db.repository'`

- [ ] **Step 3: Implement `repository.py`**

Slugify `name` with `re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")`; on a `sqlite3.IntegrityError` from the `slug` `UNIQUE` constraint, retry with `-2`, `-3`, ... appended until insert succeeds (this collision loop is the one piece of logic the test doesn't fully determine — write it explicitly: a `for suffix in itertools.count(2):` loop around a single insert attempt, breaking on success). `project_workspace_dir` just joins and does not create; `create_project` calls it, then `mkdir(parents=True, exist_ok=True)` for the project dir and each of `pdfs/parsed/drafts/assets/scripts/exports`, then inserts the row (`id=uuid.uuid4().hex`, timestamps via `datetime.now(UTC).isoformat()`), commits, and returns a `ProjectModel`. `list_projects` runs one query joining `projects` with `COUNT` subqueries/`LEFT JOIN` against `papers` and `draft_sections` grouped by project.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_repository.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/db/repository.py tests/unit/test_repository.py
git commit -m "feat: add project repository with slug collision handling"
```

---

### Task 3: Register `create_project`, `list_projects`, `get_project_status` tools

**Files:**
- Modify: `src/research_mcp/server.py`
- Test: `tests/integration/test_project_tools.py`

**Interfaces:**
- Consumes: `research_mcp.db.database.get_connection` (Task 1); `research_mcp.db.repository.{create_project, get_project, list_projects, ProjectNotFoundError}` (Task 2); `research_mcp.config.get_settings` (Phase 0).
- Produces: three `@mcp.tool()`-registered functions on the module-level `mcp` instance, callable by name through a real MCP session (next tasks' tools are added the same way, so later tasks' tests follow this task's pattern).

- [ ] **Step 1: Write the failing integration test**

```python
import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_create_list_and_get_status_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "research_mcp.server"],
        env={"RESEARCH_MCP_WORKSPACE": str(tmp_path / "workspaces"),
             "RESEARCH_MCP_DB_PATH": str(tmp_path / "research_mcp.db")},
    )
    with anyio.fail_after(15):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                created = await session.call_tool("create_project", {"name": "Integration Test"})
                assert created.is_error is False

                listed = await session.call_tool("list_projects", {})
                assert listed.is_error is False

                status = await session.call_tool("get_project_status", {"project_id": "nonexistent"})
                assert status.is_error is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/integration/test_project_tools.py -v`
Expected: FAIL — `create_project` is not a registered tool yet.

- [ ] **Step 3: Implement the tools in `server.py`**

`server.py` imports the repository module qualified — `from research_mcp.db import repository` — because the tool name `create_project` collides with the repository function of the same name (see Global Constraints). Each tool opens its own connection per call (`conn = await get_connection(get_settings().db_path)`, `try/finally: await conn.close()`) — simplest correct approach for Phase 1's call volume; a pooled/shared connection is a later optimization, not needed now.

```python
@mcp.tool()
async def create_project(name: str, description: str = "") -> dict:
    conn = await get_connection(get_settings().db_path)
    try:
        project = await repository.create_project(conn, get_settings().workspace_dir, name, description)
        return project.model_dump()
    finally:
        await conn.close()

@mcp.tool()
async def list_projects() -> list[dict]:
    conn = await get_connection(get_settings().db_path)
    try:
        return await repository.list_projects(conn)
    finally:
        await conn.close()

@mcp.tool()
async def get_project_status(project_id: str) -> dict:
    conn = await get_connection(get_settings().db_path)
    try:
        try:
            project = await repository.get_project(conn, project_id)
        except repository.ProjectNotFoundError as exc:
            raise ToolError(f"No project with id {project_id!r}") from exc
        workspace = get_settings().workspace_dir / project.slug
        cursor = await conn.execute("SELECT citekey FROM papers WHERE project_id = ?", (project_id,))
        citekeys = [row[0] for row in await cursor.fetchall()]
        return {
            **project.model_dump(),
            "workspace_path": str(workspace),
            "citekeys": citekeys,
            "draft_filenames": sorted(p.name for p in (workspace / "drafts").glob("*")),
            "chart_filenames": sorted(p.name for p in (workspace / "assets").glob("*")),
            "export_filenames": sorted(p.name for p in (workspace / "exports").glob("*")),
        }
    finally:
        await conn.close()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/integration/test_project_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/server.py tests/integration/test_project_tools.py
git commit -m "feat: register create_project, list_projects, get_project_status tools"
```

---

### Task 4: `providers/base.py` — shared provider contract

**Files:**
- Create: `src/research_mcp/providers/__init__.py` (empty)
- Create: `src/research_mcp/providers/base.py`
- Test: `tests/unit/test_providers_base.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `class SearchResult(BaseModel)` (`source_provider: str`, `external_id: str`, `doi: str | None`, `title: str`, `authors: list[str]`, `year: int | None`, `venue: str | None`, `abstract: str | None`, `citation_count: int = 0`, `pdf_url: str | None`, `landing_page_url: str | None`); `class UnpaywallResult(BaseModel)` (`is_oa: bool`, `pdf_url: str | None`, `landing_page_url: str | None`); `class LiteratureProvider(ABC)` with `name: ClassVar[str]` and `async def search(self, query: str, year_from: int | None, year_to: int | None, limit: int) -> list[SearchResult]` (abstract); `class RetryableHTTPError(Exception)`; `async def request_with_retry(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response`; `def normalize_doi(doi: str) -> str`; `def normalize_title(title: str) -> str`; `def polite_headers(email: str | None) -> dict[str, str]` (returns `{"User-Agent": "ResearchMCP/0.1 (mailto:<email>)"}` or `{"User-Agent": "ResearchMCP/0.1"}` when `email` is `None`). Tasks 5–10 consume all of these by these exact names; Task 11 consumes `SearchResult`, `normalize_doi`, `normalize_title`.

- [ ] **Step 1: Write the failing tests**

```python
import httpx
import pytest
import respx

from research_mcp.providers.base import (
    RetryableHTTPError,
    normalize_doi,
    normalize_title,
    polite_headers,
    request_with_retry,
)


def test_normalize_doi_strips_url_prefix_and_lowercases():
    assert normalize_doi("https://doi.org/10.1038/ABC.123") == "10.1038/abc.123"
    assert normalize_doi(" 10.1038/ABC.123 ") == "10.1038/abc.123"


def test_normalize_title_strips_punctuation_and_case():
    assert normalize_title("Attention Is All You Need!") == normalize_title("attention is all you need")


def test_polite_headers_with_and_without_email():
    assert polite_headers("a@b.com") == {"User-Agent": "ResearchMCP/0.1 (mailto:a@b.com)"}
    assert polite_headers(None) == {"User-Agent": "ResearchMCP/0.1"}


@pytest.mark.asyncio
@respx.mock
async def test_request_with_retry_retries_on_429_then_succeeds():
    route = respx.get("https://example.test/x").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": True})]
    )
    async with httpx.AsyncClient() as client:
        response = await request_with_retry(client, "GET", "https://example.test/x")
    assert response.json() == {"ok": True}
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_request_with_retry_raises_after_exhausting_retries_on_503():
    respx.get("https://example.test/y").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        with pytest.raises(RetryableHTTPError):
            await request_with_retry(client, "GET", "https://example.test/y")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_providers_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_mcp.providers'`

- [ ] **Step 3: Implement `base.py`**

```python
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

RETRY_STATUS_CODES = {429, 503}


class RetryableHTTPError(Exception):
    pass


async def request_with_retry(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    async for attempt in AsyncRetrying(
        retry=retry_if_exception_type(RetryableHTTPError),
        wait=wait_exponential(multiplier=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    ):
        with attempt:
            response = await client.request(method, url, **kwargs)
            if response.status_code in RETRY_STATUS_CODES:
                raise RetryableHTTPError(f"{method} {url} -> {response.status_code}")
            response.raise_for_status()
            return response
```

`normalize_doi`/`normalize_title`/`polite_headers`/`SearchResult`/`UnpaywallResult`/`LiteratureProvider` follow directly from their signatures above; `normalize_title` can be `re.sub(r"[^a-z0-9]+", "", title.lower())`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_providers_base.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/providers/__init__.py src/research_mcp/providers/base.py tests/unit/test_providers_base.py
git commit -m "feat: add LiteratureProvider base, SearchResult model, retry helper"
```

---

### Task 5: `providers/openalex.py`

**Files:**
- Create: `src/research_mcp/providers/openalex.py`
- Test: `tests/unit/test_provider_openalex.py`

**Interfaces:**
- Consumes: `LiteratureProvider`, `SearchResult`, `request_with_retry`, `polite_headers` (Task 4); `get_settings().email` (Phase 0).
- Produces: `class OpenAlexProvider(LiteratureProvider)` with `name = "openalex"`; module-level `OPENALEX = OpenAlexProvider()`. Task 11 consumes `OPENALEX`.

Verified live shape (2026-11-20): `GET https://api.openalex.org/works?search=<query>&per_page=<limit>&mailto=<email>` → `{"results": [{"id": "https://openalex.org/W...", "doi": "https://doi.org/10...", "title": str, "publication_year": int, "authorships": [{"author": {"display_name": str}}], "cited_by_count": int, "primary_location": {"pdf_url": str|None, "landing_page_url": str|None, "source": {"display_name": str|None}}, "abstract_inverted_index": {"word": [positions]} | None}]}`. Year range uses `&filter=publication_year:>{year_from-1},publication_year:<{year_to+1}` (OpenAlex comma = AND; build only the half that's present when only one of `year_from`/`year_to` is given).

- [ ] **Step 1: Write the failing tests**

```python
import httpx
import pytest
import respx

from research_mcp.providers.openalex import OPENALEX

FIXTURE = {
    "results": [
        {
            "id": "https://openalex.org/W2626778328",
            "doi": "https://doi.org/10.48550/arxiv.1706.03762",
            "title": "Attention Is All You Need",
            "publication_year": 2017,
            "authorships": [{"author": {"display_name": "Ashish Vaswani"}}],
            "cited_by_count": 12345,
            "primary_location": {
                "pdf_url": "https://arxiv.org/pdf/1706.03762",
                "landing_page_url": "http://arxiv.org/abs/1706.03762",
                "source": {"display_name": "arXiv (Cornell University)"},
            },
            "abstract_inverted_index": {"Attention": [0], "is": [1], "all": [2], "you": [3], "need": [4]},
        }
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_search_maps_fields_and_reconstructs_abstract():
    respx.get("https://api.openalex.org/works").mock(return_value=httpx.Response(200, json=FIXTURE))
    results = await OPENALEX.search("attention is all you need", None, None, 1)
    assert len(results) == 1
    r = results[0]
    assert r.source_provider == "openalex"
    assert r.doi == "10.48550/arxiv.1706.03762"
    assert r.title == "Attention Is All You Need"
    assert r.authors == ["Ashish Vaswani"]
    assert r.year == 2017
    assert r.citation_count == 12345
    assert r.pdf_url == "https://arxiv.org/pdf/1706.03762"
    assert r.abstract == "Attention is all you need"


@pytest.mark.asyncio
@respx.mock
async def test_search_propagates_http_errors():
    respx.get("https://api.openalex.org/works").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await OPENALEX.search("x", None, None, 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_provider_openalex.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_mcp.providers.openalex'`

- [ ] **Step 3: Implement `openalex.py`**

Abstract reconstruction is the one algorithm the signature doesn't determine:

```python
def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    if not inverted_index:
        return None
    max_pos = max(pos for positions in inverted_index.values() for pos in positions)
    words: list[str] = [""] * (max_pos + 1)
    for word, positions in inverted_index.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words)
```

`doi` field: strip with `normalize_doi` only for comparison elsewhere — here just strip the `"https://doi.org/"` prefix (keep original case of the DOI itself, OpenAlex already lowercases it). Use `httpx.AsyncClient() as client: response = await request_with_retry(client, "GET", "https://api.openalex.org/works", params=..., headers=polite_headers(get_settings().email))`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_provider_openalex.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/providers/openalex.py tests/unit/test_provider_openalex.py
git commit -m "feat: add OpenAlex provider"
```

---

### Task 6: `providers/arxiv.py`

**Files:**
- Create: `src/research_mcp/providers/arxiv.py`
- Test: `tests/unit/test_provider_arxiv.py`

**Interfaces:**
- Consumes: `LiteratureProvider`, `SearchResult`, `request_with_retry` (Task 4).
- Produces: `class ArxivProvider(LiteratureProvider)` with `name = "arxiv"`; module-level `ARXIV = ArxivProvider()`.

Verified live shape (2026-11-20): `GET http://export.arxiv.org/api/query?search_query=all:<query>&start=0&max_results=<limit>` → Atom XML, namespace `http://www.w3.org/2005/Atom`, each `entry` has `id` (e.g. `http://arxiv.org/abs/2105.02723v1`), `title`, `summary`, `published` (ISO8601), repeated `author/name`, and a `link` with `rel="related" type="application/pdf"` whose `href` is the PDF URL. No citation count or venue available from this feed. Year filtering: append ` AND submittedDate:[{year_from}01010000 TO {year_to}12312359]` to `search_query` when either bound is given (pad the missing bound with `00010101`/`99991231`).

- [ ] **Step 1: Write the failing tests**

```python
import httpx
import pytest
import respx

from research_mcp.providers.arxiv import ARXIV

FIXTURE = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <title>Attention Is All You Need</title>
    <summary>We propose a new simple network architecture, the Transformer.</summary>
    <published>2017-06-12T17:57:34Z</published>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <link rel="related" type="application/pdf" href="https://arxiv.org/pdf/1706.03762"/>
  </entry>
</feed>"""


@pytest.mark.asyncio
@respx.mock
async def test_search_parses_atom_entries():
    respx.get("http://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, content=FIXTURE, headers={"content-type": "application/atom+xml"})
    )
    results = await ARXIV.search("attention is all you need", None, None, 1)
    assert len(results) == 1
    r = results[0]
    assert r.source_provider == "arxiv"
    assert r.external_id == "1706.03762v7"
    assert r.title == "Attention Is All You Need"
    assert r.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert r.year == 2017
    assert r.pdf_url == "https://arxiv.org/pdf/1706.03762"


@pytest.mark.asyncio
@respx.mock
async def test_search_returns_empty_list_for_no_entries():
    respx.get("http://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(
            200,
            content='<feed xmlns="http://www.w3.org/2005/Atom"></feed>',
            headers={"content-type": "application/atom+xml"},
        )
    )
    assert await ARXIV.search("zzz_no_such_paper", None, None, 1) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_provider_arxiv.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_mcp.providers.arxiv'`

- [ ] **Step 3: Implement `arxiv.py`**

Parse with `xml.etree.ElementTree.fromstring(response.text)`, namespace map `{"atom": "http://www.w3.org/2005/Atom"}`. `external_id` is the last `/`-separated segment of `entry.find("atom:id", ns).text`. `year` is `int(entry.find("atom:published", ns).text[:4])`. `title`/`summary` need `.strip()` and inner whitespace collapsed (`" ".join(text.split())`) since arXiv often wraps them across lines.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_provider_arxiv.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/providers/arxiv.py tests/unit/test_provider_arxiv.py
git commit -m "feat: add arXiv provider"
```

---

### Task 7: `providers/pubmed.py`

**Files:**
- Create: `src/research_mcp/providers/pubmed.py`
- Test: `tests/unit/test_provider_pubmed.py`

**Interfaces:**
- Consumes: `LiteratureProvider`, `SearchResult`, `request_with_retry` (Task 4).
- Produces: `class PubmedProvider(LiteratureProvider)` with `name = "pubmed"`; module-level `PUBMED = PubmedProvider()`.

Verified live shape (2026-11-20): two-step. `GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=<query>&retmode=json&retmax=<limit>` → `{"esearchresult": {"idlist": [pmid, ...]}}`; when `idlist` is empty, return `[]` without calling efetch. Otherwise `GET .../efetch.fcgi?db=pubmed&id=<comma-joined ids>&retmode=xml&rettype=abstract` → XML `PubmedArticleSet/PubmedArticle`, each with `MedlineCitation/PMID`, `.../Article/ArticleTitle`, `.../Article/Abstract/AbstractText` (there can be more than one — join their text with a space), `.../Article/Journal/Title`, `.../Article/Journal/JournalIssue/PubDate/Year`, `.../Article/AuthorList/Author` (each has `ForeName`+`LastName`, or occasionally only `CollectiveName`), `.../Article/ELocationID[@EIdType="doi"]`. Year filtering: add `&mindate={year_from}&maxdate={year_to}&datetype=pdat` to the esearch call when either bound is given (NCBI requires both when either is present — default the missing one to `1800`/current year).

- [ ] **Step 1: Write the failing tests**

```python
import httpx
import pytest
import respx

from research_mcp.providers.pubmed import PUBMED

ESEARCH_FIXTURE = {"esearchresult": {"idlist": ["12345"]}}
EFETCH_FIXTURE = """<?xml version="1.0"?>
<PubmedArticleSet>
<PubmedArticle><MedlineCitation><PMID Version="1">12345</PMID>
<Article><Journal><Title>PLoS One</Title><JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue></Journal>
<ArticleTitle>A Pandemic Study</ArticleTitle>
<Abstract><AbstractText>Background text.</AbstractText><AbstractText>Results text.</AbstractText></Abstract>
<ELocationID EIdType="doi">10.1371/journal.pone.0000308</ELocationID>
<AuthorList><Author><LastName>Doe</LastName><ForeName>Jane</ForeName></Author></AuthorList>
</Article></MedlineCitation></PubmedArticle>
</PubmedArticleSet>"""


@pytest.mark.asyncio
@respx.mock
async def test_search_joins_multi_part_abstract_and_extracts_doi():
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=ESEARCH_FIXTURE)
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(200, content=EFETCH_FIXTURE)
    )
    results = await PUBMED.search("pandemic", None, None, 1)
    assert len(results) == 1
    r = results[0]
    assert r.source_provider == "pubmed"
    assert r.external_id == "12345"
    assert r.title == "A Pandemic Study"
    assert r.abstract == "Background text. Results text."
    assert r.doi == "10.1371/journal.pone.0000308"
    assert r.authors == ["Jane Doe"]
    assert r.year == 2021


@pytest.mark.asyncio
@respx.mock
async def test_search_returns_empty_list_without_calling_efetch_when_no_ids():
    esearch = respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": []}})
    )
    efetch = respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi")
    assert await PUBMED.search("zzz_no_such_paper", None, None, 1) == []
    assert esearch.call_count == 1
    assert efetch.call_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_provider_pubmed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_mcp.providers.pubmed'`

- [ ] **Step 3: Implement `pubmed.py`**

```python
def _author_name(author_el) -> str:
    collective = author_el.find("CollectiveName")
    if collective is not None:
        return collective.text
    fore = author_el.find("ForeName")
    last = author_el.find("LastName")
    return " ".join(p.text for p in (fore, last) if p is not None)
```

Abstract: `" ".join(el.text for el in article.findall("Article/Abstract/AbstractText") if el.text)`. DOI: `article.find("Article/ELocationID[@EIdType='doi']")` (may be `None`). Year: `article.find("Article/Journal/JournalIssue/PubDate/Year")` (may be `None` for in-press articles — leave `year=None` rather than raising).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_provider_pubmed.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/providers/pubmed.py tests/unit/test_provider_pubmed.py
git commit -m "feat: add PubMed provider"
```

---

### Task 8: `providers/crossref.py`

**Files:**
- Create: `src/research_mcp/providers/crossref.py`
- Test: `tests/unit/test_provider_crossref.py`

**Interfaces:**
- Consumes: `LiteratureProvider`, `SearchResult`, `request_with_retry`, `polite_headers` (Task 4); `get_settings().email` (Phase 0).
- Produces: `class CrossrefProvider(LiteratureProvider)` with `name = "crossref"`; module-level `CROSSREF = CrossrefProvider()`; `async def get_by_doi(doi: str) -> SearchResult | None` (returns `None` on a 404); `async def get_bibtex(doi: str) -> str | None` (returns `None` on 404). Task 9 (`resolve_doi` tool, via Task 14) consumes `get_by_doi` and `get_bibtex` by these exact names.

Verified live shape (2026-11-20): `GET https://api.crossref.org/works?query.bibliographic=<query>&rows=<limit>&mailto=<email>` → `{"message": {"items": [{"DOI": str, "title": [str], "author": [{"given": str, "family": str}], "published-print"|"published-online": {"date-parts": [[year, month, day]]}, "container-title": [str], "is-referenced-by-count": int, "link": [{"URL": str, "content-type": str}]}]}}`. `GET https://api.crossref.org/works/{doi}` → `{"message": {...one item...}}`, `404` if the DOI doesn't exist. `GET https://api.crossref.org/works/{doi}/transform/application/x-bibtex` → raw BibTeX text body, `404` if unknown. Year filter on search: `&filter=from-pub-date:{year_from}-01-01,until-pub-date:{year_to}-12-31` (build only the half that's present).

- [ ] **Step 1: Write the failing tests**

```python
import httpx
import pytest
import respx

from research_mcp.providers.crossref import CROSSREF, get_bibtex, get_by_doi

ITEM = {
    "DOI": "10.1371/journal.pone.0000308",
    "title": ["Sharing Detailed Research Data Is Associated with Increased Citation Rate"],
    "author": [{"given": "Heather A.", "family": "Piwowar"}],
    "published-print": {"date-parts": [[2007, 3, 21]]},
    "container-title": ["PLoS ONE"],
    "is-referenced-by-count": 900,
    "link": [{"URL": "https://example.test/paper.pdf", "content-type": "application/pdf"}],
}


@pytest.mark.asyncio
@respx.mock
async def test_search_maps_crossref_item_fields():
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json={"message": {"items": [ITEM]}})
    )
    results = await CROSSREF.search("citation rate", None, None, 1)
    r = results[0]
    assert r.doi == "10.1371/journal.pone.0000308"
    assert r.authors == ["Heather A. Piwowar"]
    assert r.year == 2007
    assert r.venue == "PLoS ONE"
    assert r.citation_count == 900
    assert r.pdf_url == "https://example.test/paper.pdf"


@pytest.mark.asyncio
@respx.mock
async def test_get_by_doi_returns_none_on_404():
    respx.get("https://api.crossref.org/works/10.0000/missing").mock(return_value=httpx.Response(404))
    assert await get_by_doi("10.0000/missing") is None


@pytest.mark.asyncio
@respx.mock
async def test_get_bibtex_returns_raw_text():
    respx.get("https://api.crossref.org/works/10.1371/journal.pone.0000308/transform/application/x-bibtex").mock(
        return_value=httpx.Response(200, text="@article{Piwowar_2007, title={Sharing...} }")
    )
    bibtex = await get_bibtex("10.1371/journal.pone.0000308")
    assert bibtex.startswith("@article{Piwowar_2007")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_provider_crossref.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_mcp.providers.crossref'`

- [ ] **Step 3: Implement `crossref.py`**

Shared `_map_item(item: dict) -> SearchResult` used by both `search` and `get_by_doi`. Year: try `published-print`, then `published-online`, then `published`, taking `date-parts[0][0]`; `None` if none present. `pdf_url`: `next((l["URL"] for l in item.get("link", []) if l.get("content-type") == "application/pdf"), None)`. `get_by_doi`/`get_bibtex` catch `httpx.HTTPStatusError` and return `None` only for a 404 status; any other status re-raises (a 500 is a real failure, not "DOI doesn't exist").

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_provider_crossref.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/providers/crossref.py tests/unit/test_provider_crossref.py
git commit -m "feat: add Crossref provider with DOI lookup and BibTeX export"
```

---

### Task 9: `providers/semantic_scholar.py`

**Files:**
- Create: `src/research_mcp/providers/semantic_scholar.py`
- Test: `tests/unit/test_provider_semantic_scholar.py`

**Interfaces:**
- Consumes: `LiteratureProvider`, `SearchResult`, `request_with_retry` (Task 4).
- Produces: `class SemanticScholarProvider(LiteratureProvider)` with `name = "semantic_scholar"`; module-level `SEMANTIC_SCHOLAR = SemanticScholarProvider()`; `async def get_citations(paper_id: str, limit: int) -> list[SearchResult]`; `async def get_references(paper_id: str, limit: int) -> list[SearchResult]`. Task 13 (`get_citation_graph`) consumes `get_citations`/`get_references` by these exact names.

Semantic Scholar Graph API v1 (endpoint and parameter set confirmed reachable live on 2026-11-20 — the request returned a rate-limit response, not a 404/400, proving the contract below; exact field names are Semantic Scholar's long-published, stable public schema): `GET https://api.semanticscholar.org/graph/v1/paper/search?query=<query>&limit=<limit>&fields=title,abstract,year,authors,externalIds,citationCount,openAccessPdf,venue[&year=<year_from>-<year_to>]` → `{"data": [{"paperId": str, "title": str, "abstract": str|None, "year": int|None, "authors": [{"name": str}], "externalIds": {"DOI": str|None, ...}, "citationCount": int, "openAccessPdf": {"url": str}|None, "venue": str|None}]}`. `GET .../graph/v1/paper/{paper_id}/citations?fields=title,abstract,year,authors,externalIds,citationCount,openAccessPdf,venue&limit=<limit>` → `{"data": [{"citingPaper": {...same fields...}}]}`. `GET .../graph/v1/paper/{paper_id}/references?...` → `{"data": [{"citedPaper": {...}}]}`. `paper_id` accepts a bare Semantic Scholar id, or `DOI:<doi>`/`ARXIV:<id>` prefixed external ids.

- [ ] **Step 1: Write the failing tests**

```python
import httpx
import pytest
import respx

from research_mcp.providers.semantic_scholar import SEMANTIC_SCHOLAR, get_citations, get_references

PAPER = {
    "paperId": "abc123",
    "title": "Attention Is All You Need",
    "abstract": "We propose a new architecture.",
    "year": 2017,
    "authors": [{"name": "Ashish Vaswani"}],
    "externalIds": {"DOI": "10.48550/arXiv.1706.03762"},
    "citationCount": 50000,
    "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762"},
    "venue": "NeurIPS",
}


@pytest.mark.asyncio
@respx.mock
async def test_search_maps_fields():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(200, json={"data": [PAPER]})
    )
    results = await SEMANTIC_SCHOLAR.search("attention is all you need", None, None, 1)
    r = results[0]
    assert r.external_id == "abc123"
    assert r.doi == "10.48550/arXiv.1706.03762"
    assert r.authors == ["Ashish Vaswani"]
    assert r.pdf_url == "https://arxiv.org/pdf/1706.03762"


@pytest.mark.asyncio
@respx.mock
async def test_get_citations_unwraps_citing_paper():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/DOI:10.1/x/citations").mock(
        return_value=httpx.Response(200, json={"data": [{"citingPaper": PAPER}]})
    )
    results = await get_citations("DOI:10.1/x", 1)
    assert results[0].external_id == "abc123"


@pytest.mark.asyncio
@respx.mock
async def test_get_references_unwraps_cited_paper():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/abc123/references").mock(
        return_value=httpx.Response(200, json={"data": [{"citedPaper": PAPER}]})
    )
    results = await get_references("abc123", 1)
    assert results[0].external_id == "abc123"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_provider_semantic_scholar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_mcp.providers.semantic_scholar'`

- [ ] **Step 3: Implement `semantic_scholar.py`**

Shared `_map_paper(paper: dict) -> SearchResult`. `get_citations`/`get_references` each unwrap their respective wrapper key (`citingPaper`/`citedPaper`) before mapping, and skip (don't crash on) any item where that key is missing or `None` — Semantic Scholar's citation graph can include withdrawn/unresolvable entries as `null`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_provider_semantic_scholar.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/providers/semantic_scholar.py tests/unit/test_provider_semantic_scholar.py
git commit -m "feat: add Semantic Scholar provider with citation graph lookups"
```

---

### Task 10: `providers/unpaywall.py`

**Files:**
- Create: `src/research_mcp/providers/unpaywall.py`
- Test: `tests/unit/test_provider_unpaywall.py`

**Interfaces:**
- Consumes: `UnpaywallResult`, `request_with_retry`, `polite_headers` (Task 4).
- Produces: `async def get_oa_location(doi: str, email: str | None) -> UnpaywallResult | None` (returns `None` on 404; does **not** implement `LiteratureProvider` — Unpaywall has no keyword-search endpoint, only per-DOI lookup, so it is intentionally outside the `search()` contract and the aggregator). Task 14 (`resolve_doi` tool) consumes `get_oa_location` by this exact name.

Verified live shape (2026-11-20): `GET https://api.unpaywall.org/v2/{doi}?email=<email>` → `{"is_oa": bool, "best_oa_location": {"url": str, "url_for_pdf": str|None}|None, "oa_locations": [{"url_for_pdf": str|None, "url": str}, ...]}`, `404` if the DOI is unknown to Unpaywall. **Confirmed live:** `best_oa_location.url_for_pdf` can be `null` even when `is_oa` is `true` and a different entry in `oa_locations` does have a direct PDF link — fall back to the first non-null `url_for_pdf` in `oa_locations` when `best_oa_location` lacks one.

- [ ] **Step 1: Write the failing tests**

```python
import httpx
import pytest
import respx

from research_mcp.providers.unpaywall import get_oa_location

FIXTURE_BEST_HAS_PDF = {
    "is_oa": True,
    "best_oa_location": {"url": "https://doi.org/10.x", "url_for_pdf": "https://example.test/best.pdf"},
    "oa_locations": [{"url": "https://doi.org/10.x", "url_for_pdf": "https://example.test/best.pdf"}],
}

FIXTURE_BEST_MISSING_PDF = {
    "is_oa": True,
    "best_oa_location": {"url": "https://doi.org/10.x", "url_for_pdf": None},
    "oa_locations": [
        {"url": "https://doi.org/10.x", "url_for_pdf": None},
        {"url": "https://pmc.example.test/landing", "url_for_pdf": "https://pmc.example.test/paper.pdf"},
    ],
}


@pytest.mark.asyncio
@respx.mock
async def test_uses_best_oa_location_pdf_when_present():
    respx.get("https://api.unpaywall.org/v2/10.x").mock(return_value=httpx.Response(200, json=FIXTURE_BEST_HAS_PDF))
    result = await get_oa_location("10.x", "a@b.com")
    assert result.is_oa is True
    assert result.pdf_url == "https://example.test/best.pdf"


@pytest.mark.asyncio
@respx.mock
async def test_falls_back_to_oa_locations_when_best_has_no_pdf():
    respx.get("https://api.unpaywall.org/v2/10.x").mock(
        return_value=httpx.Response(200, json=FIXTURE_BEST_MISSING_PDF)
    )
    result = await get_oa_location("10.x", "a@b.com")
    assert result.pdf_url == "https://pmc.example.test/paper.pdf"


@pytest.mark.asyncio
@respx.mock
async def test_returns_none_on_404():
    respx.get("https://api.unpaywall.org/v2/10.unknown").mock(return_value=httpx.Response(404))
    assert await get_oa_location("10.unknown", "a@b.com") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_provider_unpaywall.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_mcp.providers.unpaywall'`

- [ ] **Step 3: Implement `unpaywall.py`**

```python
def _first_pdf_url(data: dict) -> str | None:
    best = data.get("best_oa_location") or {}
    if best.get("url_for_pdf"):
        return best["url_for_pdf"]
    for location in data.get("oa_locations", []):
        if location.get("url_for_pdf"):
            return location["url_for_pdf"]
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_provider_unpaywall.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/providers/unpaywall.py tests/unit/test_provider_unpaywall.py
git commit -m "feat: add Unpaywall OA-location lookup"
```

---

### Task 11: `providers/aggregator.py`

**Files:**
- Create: `src/research_mcp/providers/aggregator.py`
- Test: `tests/unit/test_aggregator.py`

**Interfaces:**
- Consumes: `SearchResult`, `normalize_doi`, `normalize_title` (Task 4); `OPENALEX`, `ARXIV`, `PUBMED`, `CROSSREF`, `SEMANTIC_SCHOLAR` (Tasks 5–9, by those exact module-level names).
- Produces: `class AggregatedSearch(BaseModel)` (`results: list[SearchResult]`, `errors: dict[str, str]`); `async def search_all(query: str, sources: list[str], year_from: int | None, year_to: int | None, limit: int) -> AggregatedSearch`. Task 14 (`search_papers` tool) consumes `search_all` and `AggregatedSearch` by these exact names.

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import AsyncMock, patch

import pytest

from research_mcp.providers.aggregator import search_all
from research_mcp.providers.base import SearchResult


def _result(**overrides):
    base = dict(
        source_provider="openalex", external_id="1", doi="10.1/x", title="Same Paper",
        authors=["A"], year=2020, venue=None, abstract=None, citation_count=10,
        pdf_url=None, landing_page_url=None,
    )
    base.update(overrides)
    return SearchResult(**base)


@pytest.mark.asyncio
async def test_dedups_by_doi_keeping_first_seen():
    with patch("research_mcp.providers.aggregator.OPENALEX") as openalex, \
         patch("research_mcp.providers.aggregator.ARXIV") as arxiv:
        openalex.search = AsyncMock(return_value=[_result(source_provider="openalex")])
        arxiv.search = AsyncMock(return_value=[_result(source_provider="arxiv", external_id="2")])
        outcome = await search_all("same paper", ["openalex", "arxiv"], None, None, 10)
    assert len(outcome.results) == 1
    assert outcome.results[0].source_provider == "openalex"


@pytest.mark.asyncio
async def test_dedups_by_normalized_title_when_no_doi():
    with patch("research_mcp.providers.aggregator.OPENALEX") as openalex, \
         patch("research_mcp.providers.aggregator.ARXIV") as arxiv:
        openalex.search = AsyncMock(return_value=[_result(doi=None, title="Attention is all you need!")])
        arxiv.search = AsyncMock(return_value=[_result(doi=None, title="Attention Is All You Need", external_id="2")])
        outcome = await search_all("attention", ["openalex", "arxiv"], None, None, 10)
    assert len(outcome.results) == 1


@pytest.mark.asyncio
async def test_one_provider_failing_does_not_drop_the_others():
    with patch("research_mcp.providers.aggregator.OPENALEX") as openalex, \
         patch("research_mcp.providers.aggregator.ARXIV") as arxiv:
        openalex.search = AsyncMock(side_effect=RuntimeError("boom"))
        arxiv.search = AsyncMock(return_value=[_result(source_provider="arxiv")])
        outcome = await search_all("x", ["openalex", "arxiv"], None, None, 10)
    assert len(outcome.results) == 1
    assert "openalex" in outcome.errors
    assert "arxiv" not in outcome.errors


@pytest.mark.asyncio
async def test_truncates_merged_results_to_limit():
    with patch("research_mcp.providers.aggregator.OPENALEX") as openalex:
        openalex.search = AsyncMock(
            return_value=[_result(external_id=str(i), doi=f"10.1/{i}", citation_count=i) for i in range(5)]
        )
        outcome = await search_all("x", ["openalex"], None, None, 2)
    assert len(outcome.results) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_aggregator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_mcp.providers.aggregator'`

- [ ] **Step 3: Implement `aggregator.py`**

```python
def _providers() -> dict[str, LiteratureProvider]:
    # Built fresh on every call (not a module-level constant) so that tests patching
    # the module-level OPENALEX/ARXIV/etc. names (`patch("research_mcp.providers.aggregator.OPENALEX")`)
    # are observed here — a dict built once at import time would keep stale references
    # to the pre-patch objects.
    return {
        "openalex": OPENALEX,
        "semantic_scholar": SEMANTIC_SCHOLAR,
        "arxiv": ARXIV,
        "pubmed": PUBMED,
        "crossref": CROSSREF,
    }


async def search_all(query, sources, year_from, year_to, limit) -> AggregatedSearch:
    providers = _providers()
    chosen = [(name, providers[name]) for name in sources if name in providers]
    outcomes = await asyncio.gather(
        *(provider.search(query, year_from, year_to, limit) for _, provider in chosen),
        return_exceptions=True,
    )

    errors: dict[str, str] = {}
    merged: list[SearchResult] = []
    seen_dois: set[str] = set()
    seen_titles: set[str] = set()
    for (name, _), outcome in zip(chosen, outcomes, strict=True):
        if isinstance(outcome, Exception):
            errors[name] = str(outcome)
            continue
        for result in outcome:
            key_doi = normalize_doi(result.doi) if result.doi else None
            key_title = normalize_title(result.title)
            if key_doi and key_doi in seen_dois:
                continue
            if key_title in seen_titles:
                continue
            if key_doi:
                seen_dois.add(key_doi)
            seen_titles.add(key_title)
            merged.append(result)

    merged.sort(key=lambda r: r.citation_count, reverse=True)
    return AggregatedSearch(results=merged[:limit], errors=errors)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_aggregator.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/providers/aggregator.py tests/unit/test_aggregator.py
git commit -m "feat: add provider aggregator with dedup and fault isolation"
```

---

### Task 12: Register `search_papers` tool

**Files:**
- Modify: `src/research_mcp/server.py`
- Test: `tests/integration/test_search_papers_tool.py`

**Interfaces:**
- Consumes: `search_all`, `AggregatedSearch` (Task 11).
- Produces: `@mcp.tool() async def search_papers(query: str, sources: list[str] = ["openalex", "semantic_scholar", "arxiv"], year_from: int | None = None, year_to: int | None = None, limit: int = 10) -> dict` — `{"results": [...truncated-abstract dicts...], "errors": {...}}`. Abstracts longer than 300 characters are cut to 300 characters with a trailing `"..."`.

- [ ] **Step 1: Write the failing test**

```python
import sys
from unittest.mock import AsyncMock, patch

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from research_mcp.providers.aggregator import AggregatedSearch
from research_mcp.providers.base import SearchResult


@pytest.mark.asyncio
async def test_search_papers_truncates_long_abstracts(tmp_path, monkeypatch):
    long_abstract = "word " * 100
    fake_result = SearchResult(
        source_provider="openalex", external_id="1", doi="10.1/x", title="T",
        authors=["A"], year=2020, venue=None, abstract=long_abstract, citation_count=1,
        pdf_url=None, landing_page_url=None,
    )
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "research_mcp.server"],
        env={"RESEARCH_MCP_WORKSPACE": str(tmp_path / "workspaces"),
             "RESEARCH_MCP_DB_PATH": str(tmp_path / "research_mcp.db")},
    )
    with patch(
        "research_mcp.server.search_all",
        AsyncMock(return_value=AggregatedSearch(results=[fake_result], errors={})),
    ):
        with anyio.fail_after(15):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    response = await session.call_tool("search_papers", {"query": "anything"})
                    assert response.is_error is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/integration/test_search_papers_tool.py -v`
Expected: FAIL — `search_papers` is not a registered tool yet.

Note the `patch("research_mcp.server.search_all", ...)` target: since the integration test spawns `research_mcp.server` as a **subprocess**, this particular mock has no effect across the process boundary and the test instead exercises the real `search_all` against the real network — acceptable for Phase 1's integration test (it is intentionally redundant with, and simpler than, Task 15's dedicated live cross-provider test). Implementers should not spend time trying to make the subprocess honor an in-process `patch`; the assertion that matters here is `is_error is False` on a real call, proving the tool is wired end-to-end.

- [ ] **Step 3: Implement the tool in `server.py`**

```python
@mcp.tool()
async def search_papers(
    query: str,
    sources: list[str] = ["openalex", "semantic_scholar", "arxiv"],
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 10,
) -> dict:
    outcome = await search_all(query, sources, year_from, year_to, limit)
    results = []
    for r in outcome.results:
        item = r.model_dump()
        if item["abstract"] and len(item["abstract"]) > 300:
            item["abstract"] = item["abstract"][:300] + "..."
        results.append(item)
    return {"results": results, "errors": outcome.errors}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/integration/test_search_papers_tool.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/server.py tests/integration/test_search_papers_tool.py
git commit -m "feat: register search_papers tool with abstract truncation"
```

---

### Task 13: Register `get_citation_graph` tool

**Files:**
- Modify: `src/research_mcp/server.py`
- Test: `tests/unit/test_citation_graph_tool.py`

**Interfaces:**
- Consumes: `get_citations`, `get_references` (Task 9).
- Produces: `@mcp.tool() async def get_citation_graph(identifier: str, direction: Literal["references", "citations"] = "citations", limit: int = 15) -> list[dict]`. `identifier` is treated as a DOI (prefixed `DOI:` for the Semantic Scholar call, per that API's accepted id formats) — documented as this plan's call, since `AGENTS.md` names the tool's primary consumer path (Semantic Scholar) without pinning an identifier format.

Because this tool is a thin dispatch over Task 9's already-tested `get_citations`/`get_references`, it is tested directly as a Python function (not through a spawned subprocess) to keep the test fast and avoid re-testing Semantic Scholar's HTTP mapping a second time.

- [ ] **Step 1: Write the failing test**

```python
from unittest.mock import AsyncMock, patch

import pytest

from research_mcp.server import get_citation_graph
from research_mcp.providers.base import SearchResult


def _result():
    return SearchResult(
        source_provider="semantic_scholar", external_id="1", doi="10.1/x", title="T",
        authors=["A"], year=2020, venue=None, abstract=None, citation_count=1,
        pdf_url=None, landing_page_url=None,
    )


@pytest.mark.asyncio
async def test_citations_direction_calls_get_citations_with_doi_prefix():
    with patch("research_mcp.server.get_citations", AsyncMock(return_value=[_result()])) as mocked:
        results = await get_citation_graph("10.1038/x", "citations", 5)
    mocked.assert_awaited_once_with("DOI:10.1038/x", 5)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_references_direction_calls_get_references():
    with patch("research_mcp.server.get_references", AsyncMock(return_value=[_result()])) as mocked:
        await get_citation_graph("10.1038/x", "references", 5)
    mocked.assert_awaited_once_with("DOI:10.1038/x", 5)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_citation_graph_tool.py -v`
Expected: FAIL — `get_citation_graph` not defined in `research_mcp.server`.

- [ ] **Step 3: Implement the tool in `server.py`**

```python
@mcp.tool()
async def get_citation_graph(
    identifier: str,
    direction: Literal["references", "citations"] = "citations",
    limit: int = 15,
) -> list[dict]:
    s2_id = identifier if identifier.upper().startswith(("DOI:", "ARXIV:")) else f"DOI:{identifier}"
    fn = get_references if direction == "references" else get_citations
    try:
        results = await fn(s2_id, limit)
    except Exception as exc:
        raise ToolError(f"Could not fetch {direction} for {identifier!r}: {exc}") from exc
    return [r.model_dump() for r in results]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_citation_graph_tool.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/server.py tests/unit/test_citation_graph_tool.py
git commit -m "feat: register get_citation_graph tool"
```

---

### Task 14: Register `resolve_doi` tool

**Files:**
- Modify: `src/research_mcp/server.py`
- Test: `tests/unit/test_resolve_doi_tool.py`

**Interfaces:**
- Consumes: `get_by_doi`, `get_bibtex` (Task 8); `get_oa_location` (Task 10); `get_settings().email` (Phase 0).
- Produces: `@mcp.tool() async def resolve_doi(doi: str) -> dict` — `{"doi", "title", "authors", "year", "venue", "citation_count", "bibtex", "is_oa", "pdf_url", "landing_page_url"}`. Raises `ToolError` when Crossref has no record for the DOI. Unpaywall failing (including raising) degrades to `is_oa=False, pdf_url=None` rather than failing the whole tool call — this is the one place in Phase 1 the aggregator's `asyncio.gather` pattern doesn't apply (there's only one Unpaywall call, not many), so the plain fault-isolation idiom here is a `try/except` around that single call.

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import AsyncMock, patch

import pytest

from mcp.server.mcpserver.exceptions import ToolError
from research_mcp.server import resolve_doi
from research_mcp.providers.base import SearchResult, UnpaywallResult


def _crossref_result():
    return SearchResult(
        source_provider="crossref", external_id="10.1/x", doi="10.1/x", title="T",
        authors=["A"], year=2020, venue="V", abstract=None, citation_count=5,
        pdf_url=None, landing_page_url=None,
    )


@pytest.mark.asyncio
async def test_resolve_doi_merges_crossref_and_unpaywall():
    with patch("research_mcp.server.get_by_doi", AsyncMock(return_value=_crossref_result())), \
         patch("research_mcp.server.get_bibtex", AsyncMock(return_value="@article{x}")), \
         patch("research_mcp.server.get_oa_location", AsyncMock(
             return_value=UnpaywallResult(is_oa=True, pdf_url="https://x.test/p.pdf", landing_page_url=None)
         )):
        result = await resolve_doi("10.1/x")
    assert result["bibtex"] == "@article{x}"
    assert result["is_oa"] is True
    assert result["pdf_url"] == "https://x.test/p.pdf"


@pytest.mark.asyncio
async def test_resolve_doi_raises_tool_error_when_crossref_has_no_record():
    with patch("research_mcp.server.get_by_doi", AsyncMock(return_value=None)):
        with pytest.raises(ToolError):
            await resolve_doi("10.0000/missing")


@pytest.mark.asyncio
async def test_resolve_doi_degrades_gracefully_when_unpaywall_fails():
    with patch("research_mcp.server.get_by_doi", AsyncMock(return_value=_crossref_result())), \
         patch("research_mcp.server.get_bibtex", AsyncMock(return_value="@article{x}")), \
         patch("research_mcp.server.get_oa_location", AsyncMock(side_effect=RuntimeError("down"))):
        result = await resolve_doi("10.1/x")
    assert result["is_oa"] is False
    assert result["pdf_url"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_resolve_doi_tool.py -v`
Expected: FAIL — `resolve_doi` not defined in `research_mcp.server`.

- [ ] **Step 3: Implement the tool in `server.py`**

```python
@mcp.tool()
async def resolve_doi(doi: str) -> dict:
    metadata = await get_by_doi(doi)
    if metadata is None:
        raise ToolError(f"Crossref has no record for DOI {doi!r}")
    bibtex = await get_bibtex(doi)
    try:
        oa = await get_oa_location(doi, get_settings().email)
    except Exception:
        oa = None
    return {
        "doi": metadata.doi,
        "title": metadata.title,
        "authors": metadata.authors,
        "year": metadata.year,
        "venue": metadata.venue,
        "citation_count": metadata.citation_count,
        "bibtex": bibtex,
        "is_oa": oa.is_oa if oa else False,
        "pdf_url": oa.pdf_url if oa else None,
        "landing_page_url": oa.landing_page_url if oa else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_resolve_doi_tool.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/server.py tests/unit/test_resolve_doi_tool.py
git commit -m "feat: register resolve_doi tool"
```

---

### Task 15: Phase 1 closeout — live cross-provider integration test + quality gate

**Files:**
- Create: `tests/integration/test_live_cross_provider_search.py`
- Test: (this task's file is itself the test)

**Interfaces:**
- Consumes: `search_papers` tool (Task 12), via a real spawned server over stdio.

This is the one test in the whole plan that intentionally hits real external APIs — it is `AGENTS.md` §7 Fase 1's explicit exit criterion ("tes integrasi pencarian nyata lintas sumber tanpa duplikasi") and cannot be satisfied with mocks. Mark it so it's easy to skip in offline/CI environments without network access.

- [ ] **Step 1: Write the test**

```python
import os
import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.skipif(
    os.environ.get("RESEARCH_MCP_SKIP_LIVE_TESTS") == "1",
    reason="live network test; set RESEARCH_MCP_SKIP_LIVE_TESTS=0 to run",
)


@pytest.mark.asyncio
async def test_real_cross_source_search_has_no_duplicate_dois(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "research_mcp.server"],
        env={"RESEARCH_MCP_WORKSPACE": str(tmp_path / "workspaces"),
             "RESEARCH_MCP_DB_PATH": str(tmp_path / "research_mcp.db")},
    )
    with anyio.fail_after(30):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.call_tool(
                    "search_papers",
                    {"query": "attention is all you need transformer", "limit": 10},
                )
                assert response.is_error is False
    # Confirmed empirically during Task 3: `structured_content` is None for a tool
    # whose return type is a bare `dict` (as `search_papers` is) — the SDK only
    # populates it when it can build a real output schema from a more specific
    # type. `content[0].text` always carries the JSON-serialized return value
    # regardless, so parse that instead.
    import json

    payload = json.loads(response.content[0].text)
    dois = [r["doi"] for r in payload["results"] if r["doi"]]
    assert len(dois) == len(set(dois))
    assert len(payload["results"]) > 0
```

- [ ] **Step 2: Run it against the real network**

Run: `RESEARCH_MCP_SKIP_LIVE_TESTS=0 uv run pytest tests/integration/test_live_cross_provider_search.py -v`
Expected: PASS — at least one result, no duplicate DOIs. If a provider is down or rate-limited on the day this runs, `search_papers`'s `errors` map (already proven in Task 12) absorbs it; this test only needs the merged `results` list to be non-empty and DOI-unique, not every provider to have answered.

- [ ] **Step 3: Full Phase 1 quality gate**

Run: `uv run ruff check .`
Expected: no errors.

Run: `uv run ruff format --check .`
Expected: no diff needed.

Run: `uv run pytest -v` (mocked suite; the live test from Step 1 runs separately since it needs network)
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_live_cross_provider_search.py
git commit -m "test: add live cross-provider search integration test (Phase 1 exit criterion)"
```
