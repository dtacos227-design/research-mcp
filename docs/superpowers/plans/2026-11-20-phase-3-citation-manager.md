# Phase 3 — Citation Manager & Anti-Hallucination Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Research MCP a deterministic per-project bibliography (`references.bib` / `references.csl.json`) built from each project's saved papers, and a citation verifier that flags hallucinated/typo `[@citekey]` references and uncited papers.

**Architecture:** Two additive schema columns (`item_type`, `authors_structured_json`) carry per-paper publication-type and structured author-name data from the providers that already expose it; `citations/bibtex.py` gains `.bib`/CSL builders that consume that data (preferring re-keyed `bibtex_raw` for `.bib`); a new `citations/verifier.py` parses Pandoc citation syntax and diffs against the library; two new MCP tools (`generate_bibliography`, `verify_citations`) wrap them.

**Tech Stack:** Python 3.11+, Pydantic v2, aiosqlite/SQLite, bibtexparser, MCP (`MCPServer`), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-11-20-phase-3-citation-manager-design.md`

## Global Constraints

- Python 3.11+; strict typing; Pydantic v2 for all public schemas; no `print()` to stdout (logging → stderr only).
- `bibtexparser` is the only new runtime dependency (add via `uv add bibtexparser`).
- Path traversal: every workspace file write goes through `ingestion._safe_destination` (or the repository's equivalent), never a raw path.
- `papers` schema changes are additive only: `ALTER TABLE ADD COLUMN` guarded by the existing `_migrate_legacy_schema` `PRAGMA table_info` pattern; `CREATE TABLE IF NOT EXISTS` also updated.
- Quality gate every task: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest` stay green before the next task.
- Citekey format stays `[a-z][a-z0-9]*`(+ letter suffix) from `generate_citekey` (Fase 2) — unchanged.
- `RESEARCH_MCP_SKIP_LIVE_TESTS=1` is set for full-suite runs in this plan (the Fase 1 cross-provider live test is rate-limited on this egress IP; unrelated to Fase 3).

## Review Focus

The spec is a vision document; these are the inputs most likely to bite a real user if left unpinned, each with the task that owns its test:

1. **Bare `@key` at end of sentence** (`...by @smith2020.`) must not swallow the trailing period into the key → Task 4.
2. **Email / `@handle` mid-text** (`foo@bar.com`, `see @alice's work`) must never be parsed as a citation → Task 4.
3. **Review articles with multi-type lists** (PubMed `["Journal Article","Review"]`, S2 `["JournalArticle","Review"]`) must classify as `review`, not `journal-article` (priority scan, not API order) → Task 1.
4. **Compound family names** ("van der Berg") from Crossref/PubMed must produce `family: "van der Berg"`, not split on the last token → Task 2.
5. **`.bib` entry ID must equal the paper's citekey** (never Crossref's internal key), or `[@citekey]` won't resolve → Task 5.

---

### Task 1: `item_type` field + provider mapping

**Files:**
- Modify: `src/research_mcp/providers/base.py` (add `SearchResult.item_type`, `normalize_item_type`)
- Modify: `src/research_mcp/providers/openalex.py`, `crossref.py`, `semantic_scholar.py`, `pubmed.py`, `arxiv.py`
- Test: `tests/unit/test_item_type.py`

**Interfaces:**
- Produces: `SearchResult.item_type: str = "other"`; `normalize_item_type(source_provider: str, raw_type: str) -> str` in `research_mcp.providers.base` (closed vocabulary: `journal-article`, `proceedings-article`, `book-chapter`, `preprint`, `dataset`, `report`, `review`, `other`).

- [ ] **Step 1: Write the failing test** — `tests/unit/test_item_type.py`:

```python
import pytest
from research_mcp.providers.base import normalize_item_type

@pytest.mark.parametrize(("provider", "raw", "expected"), [
    ("openalex", "article", "journal-article"),
    ("openalex", "preprint", "preprint"),
    ("openalex", "book-chapter", "book-chapter"),
    ("openalex", "dataset", "dataset"),
    ("openalex", "report", "report"),
    ("openalex", "review", "review"),
    ("openalex", "weird-unknown", "other"),
    ("crossref", "journal-article", "journal-article"),
    ("crossref", "proceedings-article", "proceedings-article"),
    ("crossref", "book-chapter", "book-chapter"),
    ("crossref", "dataset", "dataset"),
    ("crossref", "report", "report"),
    ("crossref", "monograph", "report"),
    ("crossref", "posted-content", "preprint"),
    ("crossref", "preprint", "preprint"),
    ("crossref", "something-else", "other"),
    ("semantic_scholar", "JournalArticle", "journal-article"),
    ("semantic_scholar", "Conference", "proceedings-article"),
    ("semantic_scholar", "Review", "review"),
    ("semantic_scholar", "Dataset", "dataset"),
    ("semantic_scholar", "Editorial", "other"),
    ("pubmed", "Journal Article", "journal-article"),
    ("pubmed", "Review", "review"),
    ("pubmed", "Meta-Analysis", "other"),
])
def test_normalize_item_type(provider, raw, expected):
    assert normalize_item_type(provider, raw) == expected
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/unit/test_item_type.py -v` → FAIL (ImportError: `normalize_item_type`).
- [ ] **Step 3: Implement `normalize_item_type` and `SearchResult.item_type` in `base.py`** — one shared table; per-provider raw-value mapping exactly as the spec §1 table.
- [ ] **Step 4: Run to verify passes** — `pytest tests/unit/test_item_type.py -v` → PASS.
- [ ] **Step 5: Add per-provider mapping at each `_to_result`/`_map_item`/`_map_paper` construction** — `SearchResult(..., item_type=normalize_item_type(provider, raw))` with the correct raw field per spec §1; Semantic Scholar appends `,publicationTypes` to `PAPER_FIELDS`; arXiv sets `item_type="preprint"` literally. For the two multi-valued providers, the *mapper* first selects one raw string by priority scan (spec §6b.2: `review` > `proceedings-article`/`Conference` > `journal-article` > first other recognized > none→`other`), then passes that single string to `normalize_item_type` — the shared function itself takes a single raw string, not a list.
- [ ] **Step 6: Provider unit tests** — add to `tests/unit/test_item_type.py` a test per provider that builds a `SearchResult` via the provider's mapper from a minimal raw dict/Element and asserts the mapped `item_type` (OpenAlex `{"type": "preprint"}`→`preprint`; Crossref `{"type": "proceedings-article"}`→`proceedings-article`; S2 `publicationTypes=["Review","JournalArticle"]`→`review`; arXiv entry→`preprint`; PubMed `PublicationTypeList` with `Review`→`review`).
- [ ] **Step 7: Run full unit suite** — `RESEARCH_MCP_SKIP_LIVE_TESTS=1 uv run pytest tests/unit -q` → PASS.
- [ ] **Step 8: Commit** — `git add -A && git commit -m "feat: add item_type publication type normalization"`.

---

### Task 2: structured author names + schema migration

**Files:**
- Modify: `src/research_mcp/providers/base.py` (add `AuthorName` model, `SearchResult.authors_structured`)
- Modify: `src/research_mcp/providers/crossref.py`, `pubmed.py` (populate structured names)
- Modify: `src/research_mcp/models.py` (`PaperModel.authors_structured`)
- Modify: `src/research_mcp/db/database.py` (`authors_structured_json` column + migration)
- Modify: `src/research_mcp/db/repository.py` (SELECT/INSERT/UPDATE column)
- Modify: `src/research_mcp/parsers/ingestion.py` (`_make_paper` threads it through)
- Test: `tests/unit/test_author_names.py`

**Interfaces:**
- Consumes: `SearchResult` (Task 1).
- Produces: `AuthorName = {given: str|None, family: str|None, literal: str|None}` (Pydantic, in `base.py`); `SearchResult.authors_structured: list[AuthorName] | None = None`; `PaperModel.authors_structured: list[dict] | None = None` (JSON-serialized via `model_dump`/`json.dumps`).

- [ ] **Step 1: Write the failing test** — `tests/unit/test_author_names.py`:

```python
from research_mcp.providers.base import AuthorName

def test_author_name_roundtrip():
    a = AuthorName(given="Ludwig", family="van der Berg")
    d = a.model_dump()
    assert d == {"given": "Ludwig", "family": "van der Berg", "literal": None}
    assert AuthorName.model_validate(d) == a
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/unit/test_author_names.py -v` → FAIL (ImportError).
- [ ] **Step 3: Implement `AuthorName` and `SearchResult.authors_structured`** in `base.py`.
- [ ] **Step 4: Run to verify passes**.
- [ ] **Step 5: Populate structured names in `crossref.py` and `pubmed.py`** — Crossref: `given`/`family` from each author object, family-only → `literal`; PubMed: `ForeName`→`given`, `LastName`→`family`, `CollectiveName`→`literal`.
- [ ] **Step 6: Provider tests** — Crossref `{"author":[{"given":"Ludwig","family":"van der Berg"}]}` → `authors_structured == [AuthorName(given="Ludwig", family="van der Berg")]`; PubMed `CollectiveName` → `[AuthorName(literal="...")]`.
- [ ] **Step 7: Schema + model + repository + ingestion threading** — add `authors_structured_json` to `SCHEMA_SQL`, `_migrate_legacy_schema`, `PaperModel`, `_get_paper`/`save_paper_and_sections` SELECT/INSERT/UPDATE column lists, and `_make_paper` (serialize `metadata.authors_structured` to `list[dict]` on the model; deserialize back in `_get_paper`). Serialization: `[a.model_dump() for a in authors_structured]` if not None else None; deserialization: `[AuthorName.model_validate(d) for d in json.loads(...)]` if not None else None.
- [ ] **Step 8: Round-trip test** — in `tests/unit/test_author_names.py`, save a paper with `authors_structured=[AuthorName(given="Ludwig", family="van der Berg")]` via `save_paper_and_sections`, read it back via `get_paper_by_doi`, assert structured names survive.

  ```python
  @pytest.mark.asyncio
  async def test_structured_authors_survive_repository_roundtrip(db):
      ...
      saved = await save_paper_and_sections(conn, project.id, paper, sections=None)
      got = await get_paper_by_doi(conn, project.id, "10.1/x")
      assert got.authors_structured == [{"given": "Ludwig", "family": "van der Berg", "literal": None}]
  ```
- [ ] **Step 9: Run full unit suite** — PASS.
- [ ] **Step 10: Commit** — `git commit -m "feat: store structured author names from Crossref and PubMed"`.

---

### Task 3: BibTeX/CSL-JSON builders (`citations/bibtex.py`)

**Files:**
- Modify: `src/research_mcp/citations/bibtex.py`
- Modify: `pyproject.toml` (add `bibtexparser`)
- Test: `tests/unit/test_bibliography_builders.py`

**Interfaces:**
- Consumes: `PaperModel` (with `item_type`, `authors_structured`, `bibtex_raw`, `csl_json`), `AuthorName`, `normalize_item_type` vocabulary.
- Produces:
  - `item_type_to_bibtex(item_type: str) -> str` (`@article`/`@inproceedings`/`@incollection`/`@misc`/`@techreport`)
  - `item_type_to_csl(item_type: str) -> str` (`article-journal`/`paper-conference`/`chapter`/`article`/`dataset`/`report`/`review`/`document`)
  - `build_bibtex_entry(paper: PaperModel) -> str` (single entry, ID = paper.citekey)
  - `build_csl_entry(paper: PaperModel) -> dict` (CSL JSON object)

- [ ] **Step 1: Write failing tests** — `tests/unit/test_bibliography_builders.py` covering: `item_type_to_bibtex`/`item_type_to_csl` full 8-row mapping; `build_bibtex_entry` for a paper **without** `bibtex_raw` emits `@article{citekey, title = {…}, author = {Family, Given}, …}`; for a paper **with** `bibtex_raw`, the entry is the parsed Crossref entry with its ID replaced by `citekey`; `build_csl_entry` emits `{"id": citekey, "type": "article-journal", "author": [{"family": "van der Berg", "given": "Ludwig"}], "title": ...}`.
- [ ] **Step 2: Run to verify they fail** — FAIL (missing symbols).
- [ ] **Step 3: Implement the four functions** in `citations/bibtex.py` — re-key logic uses `bibtexparser` (`bibtexparser.loads`), rewrites entry `.key = paper.citekey`, and only normalizes `doi`/`title` if missing; fallback builder uses `authors_structured` when present else the heuristic split (§3), and applies §4 brace-stripping + outer-`{}` wrapping to field values.
- [ ] **Step 4: Run to verify passes**.
- [ ] **Step 5: `uv add bibtexparser`** and confirm `uv.lock`/`pyproject.toml` updated.
- [ ] **Step 6: Run full unit suite** — PASS.
- [ ] **Step 7: Commit** — `git commit -m "feat: add BibTeX and CSL-JSON bibliography builders"`.

---

### Task 4: `citations/verifier.py` (citation parsing + diff)

**Files:**
- Create: `src/research_mcp/citations/verifier.py`
- Test: `tests/unit/test_verifier.py`

**Interfaces:**
- Consumes: none (pure functions).
- Produces:
  - `extract_citekeys(text: str) -> list[str]` (ordered, deduped)
  - `diff_citations(referenced: list[str], library: set[str]) -> tuple[list[str], list[str], list[str]]` returning `(cited_but_missing, cited_and_found, unreferenced_library)`.

- [ ] **Step 1: Write failing tests** — `tests/unit/test_verifier.py`:
  - `extract_citekeys("[@smith2020] and [@doe2021; @roe2022] plus @jones2023.")` → `["smith2020","doe2021","roe2022","jones2023"]`
  - bare `@jones2023.` at sentence end → key `jones2023` (trailing punctuation stripped)
  - `[-@smith2020]` → `smith2020`
  - `foo@bar.com`, `see @alice's work` → no citekeys extracted
  - `diff_citations(["smith2020","fake9999"], {"smith2020","doe2021"})` → `(["fake9999"], ["smith2020"], ["doe2021"])`
- [ ] **Step 2: Run to verify they fail** — FAIL.
- [ ] **Step 3: Implement `extract_citekeys` and `diff_citations`** — regex per spec §5 (Pandoc key char class; bare-form requires preceding start/whitespace/`[-;` and strips trailing `.,;:!?`); `diff_citations` computes set differences, returns sorted lists.
- [ ] **Step 4: Run to verify passes**.
- [ ] **Step 5: Commit** — `git commit -m "feat: add Pandoc citation verifier"`.

---

### Task 5: `generate_bibliography` + `verify_citations` tools

**Files:**
- Modify: `src/research_mcp/server.py`
- Modify: `src/research_mcp/db/repository.py` (add `list_papers` + `set_paper_csl_json` if not present)
- Test: `tests/unit/test_bibliography_tools.py`

**Interfaces:**
- Consumes: builders (Task 3), verifier (Task 4), `PaperModel`, `get_settings`, `repository`.
- Produces: MCP tools `generate_bibliography(project_id, formats=["bibtex","csl_json"])` and `verify_citations(project_id, markdown_text=None)` with the §7/§8 output contracts.

- [ ] **Step 1: Write failing tests** — `tests/unit/test_bibliography_tools.py` (async, project fixture): create project + save 2 papers (one with `bibtex_raw`), call `generate_bibliography`, assert `references.bib` exists and every entry's ID ∈ {paper citekeys}; call `verify_citations` with `markdown_text="[@valid]"` → `cited_but_missing==[]` and the other paper listed in `uncited_papers`; `verify_citations` with a fake key → `cited_but_missing==["fake9999"]`.
- [ ] **Step 2: Run to verify they fail** — FAIL (tools not registered).
- [ ] **Step 3: Implement the two tools + `repository.list_papers(conn, project_id) -> list[PaperModel]`** — file writes via `_safe_destination` to `workspace/<slug>/references.bib` / `references.csl.json` (atomic temp+replace); `verify_citations` with `markdown_text=None` scans `drafts/*.md` (§6); `formats` validated (ToolError on unknown); `csl_json` format also updates each paper's `csl_json` column (§6b.1).
- [ ] **Step 4: Run to verify passes**.
- [ ] **Step 5: Run full gate** — `RESEARCH_MCP_SKIP_LIVE_TESTS=1 uv run pytest -q`; `uv run ruff check .`; `uv run ruff format --check .`.
- [ ] **Step 6: Commit** — `git commit -m "feat: add generate_bibliography and verify_citations tools"`.

---

### Task 6: End-to-end bibliography + verification gate

**Files:**
- Test: `tests/integration/test_bibliography_e2e.py`

**Interfaces:**
- Consumes: the two tools (Task 5).

- [ ] **Step 1: Write the integration test** — save an arXiv paper (mocked `search_all`), run `generate_bibliography`, parse `references.bib` with `bibtexparser`, assert exactly one entry and its ID equals the saved citekey; run `verify_citations` over a draft referencing that citekey → `cited_but_missing==[]` and `uncited_papers==[]`.
- [ ] **Step 2: Run to verify it fails** (before wiring is complete) or passes once green.
- [ ] **Step 3: Fix any integration mismatch exposed**.
- [ ] **Step 4: Run full gate** — `RESEARCH_MCP_SKIP_LIVE_TESTS=1 uv run pytest -q`; ruff clean.
- [ ] **Step 5: Commit** — `git commit -m "test: verify bibliography and citation verification end to end"`.
