# Phase 4 — Drafting, Visualisasi & Standalone Exporters — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a project be written as ordered Markdown chapters, charted with matplotlib/seaborn into `assets/`, and exported to `.docx`, `.tex`+`.pdf`, or a Markdown bundle — all gated by the Fase 3 citation verifier.

**Architecture:** Draft content lives in `drafts/*.md` with metadata indexed in the existing `draft_sections` table; a restricted-`exec` chart runner writes figures to `assets/`; Pandoc (Citeproc) and Tectonic (both `shutil.which()`-detected) are driven by thin exporter modules that regenerate the bibliography and run `verify_citations` first.

**Tech Stack:** Python 3.11+, Pydantic v2, aiosqlite/SQLite, matplotlib/seaborn/numpy/pandas, python-docx, pandoc 3.14, tectonic 0.17.0, bibtexparser (existing).

**Spec:** `docs/superpowers/specs/2026-11-20-phase-4-drafting-exporters-design.md`

## Global Constraints

- Python 3.11+; strict typing; Pydantic v2 for public schemas; no `print()` to stdout (logging → stderr).
- New direct deps: `python-docx`, `matplotlib`, `seaborn`, `pandas`, and promote `numpy` to a direct dep (`uv add` each). `bibtexparser` already present.
- Path traversal: every workspace file write goes through `ingestion._safe_destination` (or the existing `server._write_file`), never a raw path. Atomic temp+replace on writes.
- External binaries via `shutil.which()`: `pandoc` and `tectonic` must be detected before use; on missing, raise `ToolError` with an install hint (`brew install pandoc tectonic`).
- Citation gate: `export_to_docx`/`export_to_latex` run Fase 3's `verify_citations` logic (`extract_citekeys` + `diff_citations`) first; `strict_citations=True` halts with the missing-citekey list.
- Chart sandbox: `exec` under a restricted namespace — only `plt`/`sns`/`np`/`pd` injected, `__builtins__` allowlisted (no `__import__`/`open`/`eval`/`exec`/`os`/`sys`/`subprocess`), `sys.stdout`/`stderr` redirected to a buffer, output locked to `assets/`.
- Templates ship as package data under `src/research_mcp/templates/` (build backend `uv_build` auto-includes them; load via `importlib.resources.files("research_mcp.templates")`).
- Quality gate every task: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest` stay green.
- `RESEARCH_MCP_SKIP_LIVE_TESTS=1` for full-suite runs (Fase 1 cross-provider live test is rate-limited on this egress; unrelated).

## Review Focus

The spec is a vision document; these inputs are most likely to bite a real user if unpinned, each with the owning task:

1. **Chart code that calls `open`/`os`/`import`** must fail cleanly (not read/write outside the project), with an actionable `ToolError`, not a crash or silent success → Task 3.
2. **Chart code that never calls `plt.savefig`** (only builds a figure) must still produce a saved `assets/` file at the requested DPI → Task 3.
3. **A `drafts/*.md` file with no `draft_sections` row** (hand-created) must still be included in exports, ordered after registered chapters → Task 4 (merge) / Task 5 (exports).
4. **`export_to_docx`/`export_to_latex` with a fake citekey in the draft** must halt (strict) and report the missing key, not emit a document with an unresolved citation → Task 5.
5. **Missing `pandoc`/`tectonic` binary** must yield an actionable install-hint `ToolError`, not a raw `FileNotFoundError` → Task 4.

---

### Task 1: dependencies + draft repository functions

**Files:**
- Modify: `pyproject.toml` (add deps)
- Modify: `src/research_mcp/db/repository.py` (add `upsert_draft_section`, `get_draft_section`, `list_draft_sections`)
- Test: `tests/unit/test_draft_repository.py`

**Interfaces:**
- Produces:
  - `upsert_draft_section(conn, project_id, filename, section_order, title, word_count) -> None` (INSERT … ON CONFLICT(project_id, filename) DO UPDATE, sets `updated_at` to now)
  - `get_draft_section(conn, project_id, filename) -> dict | None` (`{"filename","section_order","title","word_count","updated_at"}`)
  - `list_draft_sections(conn, project_id) -> list[dict]` (ordered by `section_order, filename`)

- [ ] **Step 1: Write the failing test** — `tests/unit/test_draft_repository.py` (async, project fixture): create project; `upsert_draft_section` then `get_draft_section` returns the row; upsert again with new `word_count` updates in place (same filename, no duplicate); `list_draft_sections` returns two sections ordered by `section_order`.
- [ ] **Step 2: Run to verify fails** — `pytest tests/unit/test_draft_repository.py -v` → FAIL (functions not defined).
- [ ] **Step 3: Implement the three functions** in `repository.py` — follow the existing `aiosqlite` patterns; `updated_at` from `datetime.now(UTC).isoformat()`.
- [ ] **Step 4: Run to verify passes**.
- [ ] **Step 5: `uv add python-docx matplotlib seaborn pandas`** and `uv add numpy` (promote to direct); confirm `pyproject.toml`/`uv.lock` updated.
- [ ] **Step 6: Full unit suite** — `RESEARCH_MCP_SKIP_LIVE_TESTS=1 uv run pytest tests/unit -q` → PASS.
- [ ] **Step 7: Commit** — `git commit -m "feat: add draft section repository functions and deps"`.

---

### Task 2: `writers/draft_manager.py` + draft tools

**Files:**
- Create: `src/research_mcp/writers/__init__.py`, `src/research_mcp/writers/draft_manager.py`
- Modify: `src/research_mcp/server.py` (two tools)
- Test: `tests/unit/test_draft_tools.py`

**Interfaces:**
- Consumes: `upsert_draft_section`, `get_draft_section`, `list_draft_sections` (Task 1); `_safe_destination`, `_write_file` (existing in `server.py`/`ingestion.py`).
- Produces:
  - `write_draft_section(conn, project_id, project_dir, filename, content, order) -> dict`
  - `read_draft_section(conn, project_id, project_dir, filename) -> dict`

- [ ] **Step 1: Write failing tests** — `tests/unit/test_draft_tools.py`: `save_draft_section` writes `drafts/<filename>` and records title (first `# ` heading) + word_count; a filename like `"../escape.md"` or `"a/b.md"` raises `ToolError`; `read_draft_section` returns content matching what was saved; second save of same filename updates word_count without a second file.
- [ ] **Step 2: Run to verify fails** — FAIL (tools not registered / functions missing).
- [ ] **Step 3: Implement `write_draft_section`/`read_draft_section` in `draft_manager.py`** — derive `title` from first ATX heading via regex `^#\s+(.+)$` (multiline); `word_count=len(content.split())`; `order` default `max(section_order)+1` from `list_draft_sections`, or `1` if empty; write file atomically then upsert row.
- [ ] **Step 4: Wire the two MCP tools `save_draft_section(project_id, filename, content, order=None)` / `read_draft_section(project_id, filename)` in `server.py`** — both follow the existing `get_project` + `ToolError` wrapper pattern.
- [ ] **Step 5: Run to verify passes**.
- [ ] **Step 6: Full gate** — `RESEARCH_MCP_SKIP_LIVE_TESTS=1 uv run pytest -q`; ruff clean.
- [ ] **Step 7: Commit** — `git commit -m "feat: add draft section save and read tools"`.

---

### Task 3: `writers/chart_runner.py` + `generate_chart` tool

**Files:**
- Create: `src/research_mcp/writers/chart_runner.py`
- Modify: `src/research_mcp/server.py` (one tool)
- Test: `tests/unit/test_chart_runner.py`

**Interfaces:**
- Produces: `run_chart(project_dir: Path, filename: str, python_code: str, dpi: int) -> list[dict]` returning `[{"path": str, "markdown": str}]`.

- [ ] **Step 1: Write failing tests** — `tests/unit/test_chart_runner.py`: (a) code building a figure via `plt.plot(...)` without `savefig` still yields one saved `assets/<filename>.png`; (b) code calling `open("/etc/passwd")` raises (blocked builtin) with a `ToolError`-shaped error; (c) code calling `__import__("os")` is blocked; (d) `filename="../../evil.png"` is coerced/safe and lands under `assets/`; (e) dpi=150 produces a file.
- [ ] **Step 2: Run to verify fails** — FAIL.
- [ ] **Step 3: Implement `run_chart` in `chart_runner.py`** — safe-filename coercion (strip dirs, default `.png`), `matplotlib.use("Agg")` (headless, no display), restricted namespace + builtins allowlist (expanded list from design §6b.4), `sys.stdout`/`stderr` → `StringIO` around `exec`, save `plt.get_fignums()` figures to `assets/`, `plt.close("all")`, return paths + `![name](assets/<name>)`.
- [ ] **Step 4: Wire `generate_chart(project_id, filename, python_code, dpi=300)` in `server.py`** — `ToolError` wrapping, output locked to `assets/`.
- [ ] **Step 5: Run to verify passes**.
- [ ] **Step 6: Full gate** — `RESEARCH_MCP_SKIP_LIVE_TESTS=1 uv run pytest -q`; ruff clean.
- [ ] **Step 7: Commit** — `git commit -m "feat: add sandboxed chart runner and generate_chart tool"`.

---

### Task 4: templates + pandoc/latex engines

**Files:**
- Create: `src/research_mcp/templates/{csl/{apa,ieee,vancouver,chicago}.csl, latex/{ieee,article_id}.tex, docx/default_academic.docx}` (generate via `pandoc --print-default-template latex` / `--print-default-data-file reference.docx`; CSL downloaded from the official styles repo)
- Create: `src/research_mcp/exporters/__init__.py`, `exporters/pandoc_engine.py`, `exporters/docx_polisher.py`, `exporters/latex_engine.py`
- Test: `tests/unit/test_export_engines.py`

**Interfaces:**
- Produces:
  - `detect_pandoc() -> str` / `detect_tectonic() -> str` (raise `ToolError` with install hint when missing)
  - `merge_draft_markdown(project_dir: Path, sections: list[dict]) -> str`
  - `run_pandoc(args: list[str]) -> subprocess.CompletedProcess`
  - `render_docx(markdown, bib_path, csl_path, reference_doc, output) -> Path`
  - `polish_docx(path, title) -> None`
  - `render_latex(markdown, template_path, bib_path, csl_path, output_tex) -> Path`
  - `compile_pdf(tex_path, exports_dir) -> Path`

- [ ] **Step 1: Generate templates** — commit the 4 CSL files, 2 `.tex` (derived from `pandoc --print-default-template latex` with `\documentclass` swapped), and `default_academic.docx` (from `--print-default-data-file reference.docx`).
- [ ] **Step 2: Write failing tests** — `tests/unit/test_export_engines.py`: `detect_pandoc`/`detect_tectonic` raise `ToolError` with "brew" when `shutil.which` is monkeypatched to `None`; `merge_draft_markdown` includes an unregistered `drafts/orphan.md` after registered chapters; `render_docx`/`render_latex` build the expected pandoc argv (assert via a monkeypatched `run_pandoc`).
- [ ] **Step 3: Implement the engines** — `pandoc_engine.py` (detect/merge/run/render_docx), `docx_polisher.py` (`polish_docx` sets `core_properties.title` only), `latex_engine.py` (detect_tectonic/render_latex/compile_pdf; `tectonic <tex> -o <exports_dir>`).
- [ ] **Step 4: Run to verify passes**.
- [ ] **Step 5: Full gate** — `RESEARCH_MCP_SKIP_LIVE_TESTS=1 uv run pytest -q`; ruff clean.
- [ ] **Step 6: Commit** — `git commit -m "feat: add templates and pandoc/tectonic export engines"`.

---

### Task 5: citation gate + three export tools

**Files:**
- Modify: `src/research_mcp/server.py` (gate helper + `export_to_docx`, `export_to_latex`, `export_to_markdown_bundle`)
- Test: `tests/unit/test_export_tools.py`

**Interfaces:**
- Consumes: engines (Task 4), `list_papers`/`build_bibtex_entry`/`build_csl_entry` (Fase 3), `extract_citekeys`/`diff_citations` (Fase 3), `list_draft_sections` (Task 1).

- [ ] **Step 1: Write failing tests** — `tests/unit/test_export_tools.py`: project with 2 drafts + 1 paper; `export_to_markdown_bundle` writes a single `.md` containing both chapters; `export_to_docx` with a draft citing a fake key raises `ToolError` (strict) and does not write a `.docx`; `export_to_docx` with valid citekeys (mock `run_pandoc`/`polish_docx`) writes `.docx` and returns `output_path`.
- [ ] **Step 2: Run to verify fails** — FAIL (tools missing).
- [ ] **Step 3: Implement the gate + tools** — `_citation_gate(conn, project_id, markdown, strict) -> list[str]`; each export regenerates `references.bib`/`.csl.json` from `list_papers` (via the Fase 3 builders) into the project workspace, merges drafts (Task 4), runs the gate, then drives the engine. Output contracts per design §8.
- [ ] **Step 4: Run to verify passes**.
- [ ] **Step 5: Full gate** — `RESEARCH_MCP_SKIP_LIVE_TESTS=1 uv run pytest -q`; ruff clean.
- [ ] **Step 6: Commit** — `git commit -m "feat: add citation-gated docx, latex and markdown export tools"`.

---

### Task 6: end-to-end real export (Fase 4 verification criterion)

**Files:**
- Test: `tests/integration/test_export_e2e.py`

**Interfaces:**
- Consumes: the three export tools (Task 5).

- [ ] **Step 1: Write the integration test** — build a project with 3 drafts (one containing a Markdown table), 3 papers (via mocked ingestion), 1 chart (via `generate_chart`), then `export_to_docx` and `export_to_latex(compile_pdf=True)` against the REAL pandoc/tectonic; assert the `.docx` and `.pdf` files exist and are non-empty. Mark the test `pytest.mark.skipif(shutil.which("pandoc") is None or shutil.which("tectonic") is None)`.
- [ ] **Step 2: Run it** — `RESEARCH_MCP_SKIP_LIVE_TESTS=0 uv run pytest tests/integration/test_export_e2e.py -v` → PASS (binaries are installed).
- [ ] **Step 3: Fix any integration mismatch** exposed.
- [ ] **Step 4: Full gate** — `RESEARCH_MCP_SKIP_LIVE_TESTS=1 uv run pytest -q`; ruff clean.
- [ ] **Step 5: Commit** — `git commit -m "test: verify docx and latex/pdf export end to end"`.
