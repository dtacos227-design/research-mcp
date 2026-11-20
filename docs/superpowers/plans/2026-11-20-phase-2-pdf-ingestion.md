# Phase 2 — PDF Ingestion, Parsing & Local Full-Text Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import or legally download research PDFs into a project, parse them to sectioned Markdown, index sections in SQLite FTS5, and expose the five Phase 2 MCP tools.

**Architecture:** Add a local Markdown sectionizer and a `pymupdf4llm` extractor. The repository persists paper metadata and sections transactionally, using the existing FTS triggers; project files are content-addressed and kept under the project workspace. Five thin server tools resolve metadata, ingest/re-ingest papers, list/read sections, and search locally with short snippets.

**Tech Stack:** Python 3.11+, `pymupdf4llm` (PyMuPDF), `aiosqlite`/SQLite FTS5, existing `httpx`, `pydantic`, `pytest`, `pytest-asyncio`, `respx`, `ruff`.

**Spec:** `AGENTS.md` §2.1, §2.3, §2.4, §4.1, §5.1, §5.2, §5.3, §6.1.C and §7 Fase 2; decisions recorded in `docs/superpowers/specs/2026-11-20-phase-2-pdf-ingestion-design.md`.

## Global Constraints

- Add `pymupdf4llm` as the primary PDF parser (`AGENTS.md` §2.1, §7 Fase 2).
- The database schema remains the exact Phase 1 schema in `AGENTS.md` §2.3; FTS5 remains synced by the existing insert/update/delete triggers.
- All public functions and MCP parameters use Python type hints and Pydantic v2-compatible types (`AGENTS.md` §5.1.1).
- All destination paths must be resolved and confined to the project directory (`target.resolve().is_relative_to(project_dir.resolve())`, `AGENTS.md` §5.1.4).
- Remote PDF downloads: HTTPS only (including each redirect; reject HTTPS→HTTP downgrade), at most 5 redirects, 30-second timeout, maximum 100 MiB; stream to a temp file, require a successful response and `%PDF-` signature before finalizing.
- Only download PDFs with evidence of open access: Unpaywall `is_oa=True` plus a PDF URL; an arXiv PDF URL; or Semantic Scholar's explicit `openAccessPdf` result. Do not treat arbitrary Crossref/OpenAlex PDF links as legal OA.
- If metadata is available but no verified legal PDF URL is available, save metadata and return `download_status="unavailable"` plus a warning; never invent a PDF URL.
- Every user-facing tool failure becomes an actionable `ToolError`; expected “no legal PDF” and metadata-only paths return structured status rather than raising.
- Never return the full PDF or entire parsed document from search tools. Section reads return only the requested section; FTS results include snippets capped at 300 characters.
- Re-ingesting an existing project DOI updates its record while retaining its paper ID and citekey. Local no-DOI imports use a deterministic content hash for identity so repeat imports do not create duplicate papers.
- Citekey format follows `authorlastnameYYYYfirstword`, with `unknown`/`0000` for missing author/year and suffixes `a`, `b`, … on collisions (`AGENTS.md` §5.2.3). The shared helper is introduced in this phase because `papers.citekey` is NOT NULL; Phase 3 will extend the same module for bibliography generation.
- Phase gate: `uv run ruff check .`, `uv run ruff format --check .`, full `uv run pytest -v`, and a live open-access PDF ingestion/search test that exercises download, parsing, SQLite storage, and FTS retrieval.

## Review Focus

- A malicious or malformed URL, oversized response, non-PDF body, or interrupted download must not leave a final-looking partial PDF in the project. Task 3 tests HTTPS-only, size cap, status/signature rejection, and temp-file cleanup.
- A local PDF without DOI/embedded author/year must still import deterministically without fabricated metadata; repeat import must reuse the same paper/citekey. Tasks 2 and 4 test fallback metadata and idempotency.
- A re-ingest must replace a paper's sections without stale FTS hits and must preserve the existing paper ID/citekey for a known DOI. Task 4 tests re-ingest plus FTS old-term/new-term behavior.
- FTS syntax characters (`"`, `*`, `OR`, parentheses, punctuation-only queries) must not produce raw SQLite errors or accidentally broaden results. Task 6 tests safe tokenization and an empty-token `ToolError`.
- A DOI or provider identifier with no OA PDF must retain metadata and report why no PDF was downloaded; no unauthorized Crossref link may be fetched. Task 5 tests metadata-only and legal-source selection behavior.

---

### Task 1: Deterministic citekey utility

**Files:**
- Create: `src/research_mcp/citations/__init__.py`
- Create: `src/research_mcp/citations/bibtex.py`
- Test: `tests/unit/test_citekey.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `generate_citekey(authors: list[str], year: int | None, title: str, existing: set[str] | None = None) -> str`. Task 4 uses this exact helper; Phase 3 extends the same module.

- [ ] **Step 1: Write the failing tests**

Test `test_generate_citekey_uses_first_author_year_and_first_title_word` with `["Ashish Vaswani"]`, `2017`, `"Attention Is All You Need"` → `"vaswani2017attention"`.
Test `test_generate_citekey_normalizes_unicode_and_punctuation` using a name/title containing punctuation and accents; result contains only lowercase ASCII letters/digits.
Test `test_generate_citekey_uses_unknown_and_zero_year_when_missing` with empty authors and `year=None` → prefix `"unknown0000"`.
Test `test_generate_citekey_adds_letter_suffix_on_collision` with base key already in `existing` → suffix `"a"`; with base and `basea` present → suffix `"b"`.

- [ ] **Step 2: Run `uv run pytest tests/unit/test_citekey.py -v`**

Expected: FAIL with import error for `research_mcp.citations.bibtex`.

- [ ] **Step 3: Implement `generate_citekey`**

Use the first author's last whitespace-delimited token, normalized to lowercase ASCII alphanumeric; use `unknown` if absent. Use a four-digit year (`0000` when absent), the first alphanumeric title token (`paper` if absent), and append `a`, `b`, … only when the base key is already in `existing`.

- [ ] **Step 4: Re-run the focused tests**

Run: `uv run pytest tests/unit/test_citekey.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

`git add src/research_mcp/citations tests/unit/test_citekey.py && git commit -m "feat: add deterministic citekey helper for paper ingestion"`

---

### Task 2: `parsers/sectionizer.py`

**Files:**
- Create: `src/research_mcp/parsers/__init__.py`
- Create: `src/research_mcp/parsers/sectionizer.py`
- Test: `tests/unit/test_sectionizer.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SectionData` (typed model with `section_order: int`, `heading: str`, `content: str`, `word_count: int`) and `sectionize_markdown(markdown: str) -> list[SectionData]`. Task 3 consumes `sectionize_markdown`.

- [ ] **Step 1: Write failing tests**

Add tests:
- `test_sectionize_splits_markdown_headings_in_order`: `# Abstract` / `## Methods` → two ordered sections with heading text excluding `#`.
- `test_sectionize_keeps_preamble_as_document_section`: non-empty text before the first heading becomes a first `"Document"` section.
- `test_sectionize_returns_full_text_section_when_no_headings`: plain Markdown → one `"Full Text"` section.
- `test_sectionize_empty_markdown_returns_empty_list`.
- `test_word_count_counts_whitespace_separated_words`.

- [ ] **Step 2: Run `uv run pytest tests/unit/test_sectionizer.py -v`**

Expected: FAIL with import error for `research_mcp.parsers.sectionizer`.

- [ ] **Step 3: Implement `sectionize_markdown(markdown: str) -> list[SectionData]`**

Recognize ATX headings (`^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$`), preserve Markdown body text, strip only outer whitespace, number sections from 1, and count words with `len(content.split())`. Do not split on page separators.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/unit/test_sectionizer.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

`git add src/research_mcp/parsers/__init__.py src/research_mcp/parsers/sectionizer.py tests/unit/test_sectionizer.py && git commit -m "feat: split parsed Markdown into ordered paper sections"`

---

### Task 3: `parsers/pdf_extractor.py` — safe PDF verification, copy/download, parsing

**Files:**
- Modify: `pyproject.toml` (add runtime dependency `pymupdf4llm>=0.0.17`)
- Modify: `uv.lock`
- Create: `src/research_mcp/parsers/pdf_extractor.py`
- Test: `tests/unit/test_pdf_extractor.py`

**Interfaces:**
- Consumes: `SectionData`, `sectionize_markdown` (Task 2); `httpx`, `get_settings()` email not required for PDF fetch.
- Produces: `MAX_PDF_BYTES = 100 * 1024 * 1024`; `PdfMetadata` (typed model with `title: str | None`, `author: str | None`, `year: int | None`); `async def download_pdf(url: str, destination: Path, project_dir: Path) -> Path`; `def import_pdf(source: Path, destination: Path, project_dir: Path) -> Path`; `def extract_markdown(pdf_path: Path, project_dir: Path) -> str`; `def extract_pdf_metadata(pdf_path: Path, project_dir: Path) -> PdfMetadata`. Every destination and parsed input path must resolve inside the project workspace. Task 5 consumes these exact signatures.

- [ ] **Step 1: Write failing tests**

Use a generated minimal PDF fixture (PyMuPDF `fitz`) for import/extraction and `respx` for download. Cover:
- `test_import_pdf_copies_valid_pdf_into_project`.
- `test_import_pdf_rejects_non_pdf_and_does_not_leave_output`.
- `test_import_pdf_rejects_path_escape`.
- `test_download_pdf_streams_valid_pdf_and_uses_https_only` (mock a valid `%PDF-` response; assert the returned path).
- `test_download_pdf_rejects_http_url`.
- `test_download_pdf_follows_only_https_redirects`.
- `test_download_pdf_rejects_redirect_downgrade_or_more_than_five_redirects`.
- `test_download_pdf_rejects_oversized_content_and_cleans_temp_file`.
- `test_download_pdf_rejects_non_pdf_body_and_cleans_temp_file`.
- `test_extract_markdown_uses_pymupdf4llm_page_chunks_and_joins_text` (patch `pymupdf4llm.to_markdown` to return page chunks; assert concatenated Markdown).
- `test_extract_pdf_metadata_reads_title_author_and_year_from_document_metadata`.
- `test_extract_pdf_metadata_returns_none_for_missing_author_and_year`.

- [ ] **Step 2: Run `uv run pytest tests/unit/test_pdf_extractor.py -v`**

Expected: FAIL with import error for `research_mcp.parsers.pdf_extractor`.

- [ ] **Step 3: Add `pymupdf4llm>=0.0.17` and implement extractor**

Run `uv add "pymupdf4llm>=0.0.17"` to update lock. For remote requests use `httpx.AsyncClient(timeout=30.0, follow_redirects=False)` and manually follow at most 5 redirects, requiring every target URL to remain HTTPS; stream into a `.part` file, reject non-HTTPS, HTTP errors, content over 100 MiB, and invalid `%PDF-` signature; remove temp files on every failure. For local import, resolve the source and destination, verify source is a regular PDF <=100 MiB, copy to a temp path, verify signature, then atomically replace destination. Parse with `pymupdf4llm.to_markdown(str(pdf_path), page_chunks=True, show_progress=False)` and concatenate each chunk's `"text"` with blank lines; do not write images or embed base64. Extract PDF metadata from PyMuPDF's `Document.metadata`; derive year only from a valid `CreationDate` timestamp, otherwise return `None`.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/unit/test_pdf_extractor.py -v`
Expected: PASS (13 test cases, including 2 redirect-limit cases).

- [ ] **Step 5: Commit**

`git add pyproject.toml uv.lock src/research_mcp/parsers/pdf_extractor.py tests/unit/test_pdf_extractor.py && git commit -m "feat: add safe PDF download, import and Markdown extraction"`

---

### Task 4: Repository persistence for papers and sections

**Files:**
- Modify: `src/research_mcp/db/repository.py`
- Test: `tests/unit/test_paper_repository.py`

**Interfaces:**
- Consumes: existing `get_connection`, `ProjectNotFoundError`, `PaperModel`, `SectionData` (Tasks 1-3/Phase 0).
- Produces:
  - `async def get_paper_by_doi(conn, project_id: str, doi: str) -> PaperModel | None`
  - `async def get_paper_by_citekey(conn, project_id: str, citekey: str) -> PaperModel | None`
  - `async def get_paper_by_pdf_path(conn, project_id: str, pdf_path: str) -> PaperModel | None`
  - `async def save_paper_and_sections(conn, project_id: str, paper: PaperModel, sections: list[SectionData] | None) -> PaperModel` (`None` means metadata-only update and preserves any existing sections; a list replaces sections transactionally, including `[]` to clear them)
  - `async def list_sections(conn, project_id: str, citekey: str) -> list[dict]`
  - `async def get_section(conn, project_id: str, citekey: str, section_name: str) -> dict` (raises `ProjectNotFoundError` only for missing project; define and use `PaperNotFoundError` and `SectionNotFoundError` for the other not-found conditions).
  - `async def search_sections(conn, project_id: str, query: str, limit: int) -> list[dict]`
  Task 5/6 consume these exact names.

- [ ] **Step 1: Write failing tests against a real temporary SQLite database**

Cover:
- inserting a PaperModel plus sections and reading back the fields;
- updating a DOI record retains its original paper id/citekey/created_at and replaces sections;
- metadata-only update (`sections=None`, `pdf_path=None`) preserves existing PDF path and sections;
- no-DOI local record can be found by deterministic `pdf_path`;
- list sections returns heading/order/word_count;
- section read is case/whitespace normalized and reports missing/ambiguous clearly;
- re-ingest removes old FTS terms and makes new terms searchable;
- search results are project-scoped and include citekey, heading, and <=300-character snippet.

- [ ] **Step 2: Run `uv run pytest tests/unit/test_paper_repository.py -v`**

Expected: FAIL because repository paper/section operations are not defined.

- [ ] **Step 3: Implement repository methods**

Serialize `authors` with `json.dumps` and deserialize from `authors_json`; `csl_json` remains a JSON string. On an existing DOI (or matching deterministic local `pdf_path`), retain the stored paper ID, citekey, and `created_at`; update bibliographic fields. If incoming `pdf_path` is `None`, preserve the previous path. Update sections only when `sections` is not `None`; delete old sections before inserting the new list so FTS triggers update consistently. Use one DB transaction for metadata and section changes. For `search_sections`, tokenize with Unicode `re.findall(r"\w+", query)`, quote each token, join with `AND`, bind the MATCH expression as a SQL parameter, scope on `project_id`, order by `bm25(paper_sections_fts)`, and use `snippet(...)` capped at 300 characters. A query yielding no tokens raises `ValueError("Search query must contain at least one word")`.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/unit/test_paper_repository.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

`git add src/research_mcp/db/repository.py tests/unit/test_paper_repository.py && git commit -m "feat: persist papers, sections and scoped FTS search"`

---

### Task 5: Paper identifier resolution, OA policy, and ingestion orchestration

**Files:**
- Create: `src/research_mcp/parsers/ingestion.py`
- Test: `tests/unit/test_ingestion.py`

**Interfaces:**
- Consumes: `search_all`, `get_by_doi`, `get_bibtex`, `get_oa_location`, `download_pdf`, `import_pdf`, `extract_markdown`, `sectionize_markdown`, `generate_citekey`, repository functions from Task 4.
- Produces:
  - `async def resolve_identifier(identifier: str) -> SearchResult`
  - `async def ingest_remote_paper(conn, project_id: str, workspace: Path, identifier: str, download: bool) -> dict`
  - `async def ingest_local_pdf(conn, project_id: str, workspace: Path, file_path: Path, doi: str | None) -> dict`
  Task 7 MCP tools consume these exact interfaces.

- [ ] **Step 1: Write failing mocked orchestration tests**

Cover:
- DOI resolves through Crossref and requests an Unpaywall URL only when `is_oa=True` and `pdf_url` exists.
- Unpaywall 404/no-OA yields metadata-only saved result with `download_status="unavailable"` and warning, not a guessed download.
- arXiv identifier resolves only to exact `external_id` and its arXiv PDF is eligible for download.
- DOI/identifier not found raises an actionable domain error.
- `download=False` saves metadata but performs no PDF request or extraction.
- local import without DOI extracts metadata title/author/year when present; missing metadata uses filename title, empty authors, and `year=None`, with deterministic citekey.
- `test_reingest_same_doi_preserves_id_citekey_replaces_sections`.
- `test_repeat_local_import_same_hash_reuses_paper`.
- `test_metadata_only_reingest_preserves_existing_pdf_and_sections`.

- [ ] **Step 2: Run `uv run pytest tests/unit/test_ingestion.py -v`**

Expected: FAIL with module import error.

- [ ] **Step 3: Implement ingestion orchestration**

For non-DOI identifiers, call `search_all` with all five searchable providers and a bounded limit, then require exact `external_id` match. For DOI identifiers, obtain authoritative Crossref metadata. Only allow auto-download for Unpaywall positive OA PDF, arXiv PDF, or Semantic Scholar `openAccessPdf`; otherwise persist metadata-only with `sections=None` so an existing parsed library is not erased. DOI/provider artifacts use `pdfs/{paper_id}-{sha256}.pdf` and `parsed/{paper_id}-{sha256}.md`; a local no-DOI import uses `pdfs/local-{sha256}.pdf` and `parsed/local-{sha256}.md`, allowing lookup of an already-imported identical file before creating a new paper id. Pass every destination through project-root containment validation. For local PDF metadata, use provided DOI's Crossref data when available; otherwise use `extract_pdf_metadata` and filename fallback without inventing author/year. Remote ingestion returns `download_status` (`downloaded`, `skipped`, `unavailable`); local import returns `import_status=\"imported\"` and `download_status=\"not_applicable\"`. Both return warnings, paper fields, and section count.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/unit/test_ingestion.py -v`
Expected: PASS (10 test cases; the local-metadata test has two parameter values).

- [ ] **Step 5: Commit**

`git add src/research_mcp/parsers/ingestion.py tests/unit/test_ingestion.py && git commit -m "feat: orchestrate legal OA download and local PDF ingestion"`

---

### Task 6: Local section and FTS MCP tools

**Files:**
- Modify: `src/research_mcp/server.py`
- Create: `tests/unit/test_local_library_tools.py`

**Interfaces:**
- Consumes: Task 4 repository methods `list_sections`, `get_section`, `search_sections`; Phase 1 `get_connection` and `repository.get_project`.
- Produces: directly callable and MCP-registered:
  - `list_paper_sections(project_id: str, citekey: str) -> list[dict]`
  - `read_paper_section(project_id: str, citekey: str, section_name: str) -> dict`
  - `search_local_library(project_id: str, query: str, limit: int = 10) -> list[dict]`
  Wrap all expected lookup/query failures in actionable `ToolError`.

- [ ] **Step 1: Write failing direct-call tests**

With a real temp DB and patchable connection factory, add 8 tests: `test_list_paper_sections_returns_heading_order_and_word_count`, `test_read_paper_section_matches_case_and_whitespace`, parametrized `test_missing_project_paper_or_section_returns_tool_error` (three missing-object cases), `test_search_local_library_is_project_scoped`, `test_search_local_library_rejects_punctuation_only_query_as_tool_error`, and `test_search_local_library_caps_snippets_at_300_chars`.

- [ ] **Step 2: Run `uv run pytest tests/unit/test_local_library_tools.py -v`**

Expected: FAIL because these tools are not registered.

- [ ] **Step 3: Add the three tools to `server.py`**

Use per-call DB connection and `finally: await conn.close()`. Do not alter existing Phase 1 tool behavior or signatures.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/unit/test_local_library_tools.py -v`
Expected: PASS (8 test cases, including 3 parametrized not-found cases).

- [ ] **Step 5: Commit**

`git add src/research_mcp/server.py tests/unit/test_local_library_tools.py && git commit -m "feat: add local section reading and FTS search tools"`

---

### Task 7: Remote/local ingestion MCP tools

**Files:**
- Modify: `src/research_mcp/server.py`
- Test: `tests/integration/test_ingestion_tools.py`

**Interfaces:**
- Consumes: Task 5 `ingest_remote_paper`, `ingest_local_pdf`; Phase 1 repository/project connection.
- Produces:
  - `save_paper_to_project(project_id: str, identifier: str, download_pdf: bool = True) -> dict`
  - `import_local_pdf(project_id: str, file_path: str, doi: str | None = None) -> dict`

- [ ] **Step 1: Write failing integration tests**

Use a real spawned MCP stdio server with isolated workspace/db and a generated local PDF fixture. Verify local PDF import creates project artifacts, paper metadata, section rows and FTS-searchable terms; an input file outside the workspace is copied into a destination that remains within the project and the source is unchanged; invalid DOI remains a warning while the supplied local PDF can still be imported. Test `save_paper_to_project(download_pdf=False)` directly with the identifier resolver stubbed, asserting metadata is stored without PDF files or download/extraction calls. Errors are returned as ToolError text, not raw exception.

- [ ] **Step 2: Run `uv run pytest tests/integration/test_ingestion_tools.py -v`**

Expected: FAIL because ingestion tools are not registered.

- [ ] **Step 3: Register both tools in `server.py`**

Verify project existence first. Convert expected domain errors to `ToolError`; return only metadata, status, warning, artifact paths, and section count — never full extracted Markdown.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/integration/test_ingestion_tools.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

`git add src/research_mcp/server.py tests/integration/test_ingestion_tools.py && git commit -m "feat: add save and local PDF import tools"`

---

### Task 8: Fase 2 live verification and quality gate

**Files:**
- Create: `tests/integration/test_live_pdf_ingestion.py`

**Interfaces:**
- Consumes: `save_paper_to_project`, `search_local_library` through real MCP stdio process.

- [ ] **Step 1: Write a live OA ingestion test**

Use a known stable OA arXiv paper with its exact arXiv ID, `save_paper_to_project(..., download_pdf=True)`, assert `download_status == "downloaded"`, section count > 0, and an extracted phrase is findable via `search_local_library`. Mark skip with `RESEARCH_MCP_SKIP_LIVE_TESTS=1`; default run must execute the live test.

- [ ] **Step 2: Run live test**

Run: `RESEARCH_MCP_SKIP_LIVE_TESTS=0 uv run pytest tests/integration/test_live_pdf_ingestion.py -v`
Expected: PASS against the real arXiv PDF endpoint.

- [ ] **Step 3: Run phase gate**

Run: `uv run ruff check .`
Expected: no errors.

Run: `uv run ruff format --check .`
Expected: no formatting required.

Run: `uv run pytest -v`
Expected: all mocked tests and the live ingestion test pass.

- [ ] **Step 4: Commit**

`git add tests/integration/test_live_pdf_ingestion.py && git commit -m "test: verify live OA PDF ingestion through local FTS"`
