# Design: Fase 4 — Drafting, Visualisasi, & Standalone Exporters (DOCX & LaTeX)

**Status:** Draft, pending builder check
**Branch:** `feature/phase-4-drafting-exporters` (forked from `main` at the Fase 3 merge commit)
**Spec source:** AGENTS.md §6.1.E (`save_draft_section`, `read_draft_section`, `generate_chart`), §6.1.G (`export_to_docx`, `export_to_latex`, `export_to_markdown_bundle`), §7 "Fase 4", §5.1 rule 5 (chart sandbox), §5.2 rule 4 (citation gate), §5.2 rule 5 (binary detection).

## Intent and Why

Fase 4 turns a project from "papers stored and cited" into "a draft that can be written and published". Three independent capabilities, delivered together because they share the same Markdown-source-of-truth:

1. **Drafting** — write/read chapter Markdown (`01_abstract.md`, `02_intro.md`, …) per project, with an ordered outline and word counts.
2. **Visualization** — generate 300-DPI scientific charts from user-supplied matplotlib/seaborn code, locked to the project's `assets/` folder.
3. **Standalone export** — merge the draft into one document and emit `.docx` (Pandoc Citeproc + python-docx polish), `.tex` + `.pdf` (template + Tectonic), or a single Markdown bundle with a formatted reference list.

## Goals

- `save_draft_section` / `read_draft_section`: content lives in `drafts/<filename>.md` on disk (already read by Fase 3's `verify_citations`), metadata lives in the existing `draft_sections` table (filename, order, title, word_count, updated_at) — no schema change needed.
- `generate_chart`: execute user visualization code against a restricted namespace containing only `matplotlib`/`seaborn`/`numpy`/`pandas`, then save every open figure to `assets/` at the requested DPI; never let the code choose its own output path.
- `export_to_docx` / `export_to_latex`: run `verify_citations` first (the Zero-Hallucination Gate, AGENTS.md §5.2 rule 4), then drive Pandoc/Tectonic, both detected via `shutil.which()` with a diagnostic error carrying install instructions (§5.2 rule 5).
- `export_to_markdown_bundle`: concatenate the ordered chapters into one Markdown and append a Pandoc-rendered reference list.

## Non-Goals

- WordCraft integration (`check_wordcraft_env`, `prepare_wordcraft_handoff`, live authoring) — that is Fase 5, which builds the `wordcraft_bridge.py` handoff on top of these exporters.
- A hard security boundary against a malicious local operator — the chart "sandbox" is a namespace restriction, not a container; an operator who owns the machine can run their own Python anyway. The sandbox's real job is to (a) keep visualization code from trivially reading/writing outside the project and (b) force the output into `assets/`.
- Recreating a full citation-styling engine — Pandoc Citeproc does the formatting; this phase only wires it.

## Anti-Goals

- Letting `generate_chart` write anywhere other than `assets/` (path traversal out of the project is an explicit failure).
- An exporter that skips the citation gate by default (`strict_citations=True` must halt and report missing citekeys).
- A draft whose on-disk content and DB metadata can silently diverge (every `save_draft_section` updates both atomically-ish: write file, then upsert row).

## Constraints

- Same as Fase 1–3: strict typing/Pydantic v2, path-traversal protection on every write, no `print()` to stdout, `ruff`/`pytest` green every task.
- External binaries Pandoc (3.14) and Tectonic (0.17.0) are now installed (user-approved Homebrew install); both satisfy AGENTS.md §8.
- New runtime deps: `python-docx`, `matplotlib`, `seaborn`, `numpy`, `pandas` (all Python-only, no system deps).
- Template files must ship as package data (see "Templates"), not be generated at import time.

## Approaches Considered

**Draft content: DB column vs files-on-disk.**
- **A — files on disk + DB metadata index (chosen).** `draft_sections` already has no content column and Fase 3's `verify_citations(markdown_text=None)` already globs `drafts/*.md`. Files are diffable, editable by hand, and what Pandoc already consumes. No schema migration.
- **B — add a `content` column to `draft_sections`.** Single store, but duplicates content that already lives in files, requires a migration, and diverges from what Fase 3 established. Rejected.

**Chart execution.**
- **A — `exec()` in a restricted namespace (chosen).** Inject only `plt`/`sns`/`np`/`pd` plus a small safe-builtins allowlist (no `__import__`, `open`, `eval`, `exec`, `os`), then save `plt.get_fignums()` figures to `assets/`. Matches AGENTS.md §5.1 rule 5 literally.
- **B — subprocess with a fixed preamble.** More isolation but same interpreter, more moving parts, harder to return figure paths. Rejected as over-engineered for a local-first tool.

**LaTeX compile.**
- **A — Pandoc markdown→tex with a bundled template, then `tectonic <file>.tex` (chosen).** Reuses Pandoc's markdown+citeproc handling, keeps the `.tex` template surface minimal.
- **B — hand-write the full `.tex` from the draft.** Rejected: reimplements Pandoc's citation/template work.

## The How (my calls)

### 1. Draft storage and ordering

- `save_draft_section(project_id, filename, content, order=None)`: validate `filename` is a plain `.md` name (no path separators, no traversal); write content to `drafts/<filename>` (atomic temp+replace, `_safe_destination`); derive `title` from the first ATX `# ` heading (or `None`); compute `word_count`; `order` defaults to `max(section_order)+1` when omitted; upsert into `draft_sections` on `UNIQUE(project_id, filename)`.
- `read_draft_section(project_id, filename)` returns `{"filename", "title", "order", "word_count", "content"}` — content read from disk, metadata from the row (content is authoritative on disk).
- New repository functions: `upsert_draft_section`, `get_draft_section`, `list_draft_sections` (ordered by `section_order, filename`).

### 2. Chart sandbox (`writers/chart_runner.py`)

- `run_chart(project_dir: Path, filename: str, python_code: str, dpi: int) -> list[dict]`.
- Validate/coerce `filename` to a safe basename inside `assets/`; default extension `.png` if none; reject `..`/abs path.
- Namespace injected: `{"plt": matplotlib.pyplot, "sns": seaborn, "np": numpy, "pd": pandas}` plus `{"__builtins__": {allowlisted builtins}}` (allowlist: `len`, `range`, `enumerate`, `zip`, `min`, `max`, `sum`, `abs`, `round`, `str`, `int`, `float`, `bool`, `list`, `dict`, `tuple`, `set`, `print`, `repr`).
- After `exec`, iterate `plt.get_fignums()`; save each figure to `assets/` (single figure → `<filename>`; multiple → `<stem>-1.<ext>`, `<stem>-2.<ext>`, …), then `plt.close('all')`.
- Return `[{"path": str, "markdown": "![alt](assets/<name>)"}]`. Raise a `ToolError` with the code's traceback if `exec` raises, so the caller sees what went wrong.

### 3. Pandoc engine (`exporters/pandoc_engine.py`)

- `detect_pandoc() -> str` — `shutil.which("pandoc")`, else raise a diagnostic with `brew install pandoc` hint.
- `merge_draft_markdown(project_dir) -> str` — read `drafts/*.md` ordered by `section_order` (via `list_draft_sections`), join with `\n\n`.
- `run_pandoc(args: list[str]) -> subprocess.CompletedProcess` — thin `subprocess.run` wrapper returning captured stderr for actionable errors.
- `render_docx(markdown, bib_path, csl_path, reference_doc, output) -> Path` — `pandoc <md> -o <out.docx> --citeproc --bibliography <bib> --csl <csl> --reference-doc <ref>`.

### 4. DOCX polisher (`exporters/docx_polisher.py`)

- `polish_docx(path, title) -> None` — open with `python-docx`, set `core_properties.title` (and `subject`/`author` if derivable); no layout changes (Pandoc's reference-doc owns styles). Best-effort: any failure here is non-fatal to the export.

### 5. LaTeX engine (`exporters/latex_engine.py`)

- `detect_tectonic() -> str` — `shutil.which("tectonic")`, else diagnostic with `brew install tectonic`.
- `render_latex(markdown, template_path, bib_path, csl_path, output_tex) -> Path` — `pandoc <md> -o <out.tex> --standalone --citeproc --bibliography <bib> --csl <csl> --template <template>`.
- `compile_pdf(tex_path, output_pdf) -> Path` — `tectonic <tex> -o <exports_dir>` (Tectonic auto-fetches any needed TeX packages on first run).

### 6. Templates (package data)

Bundled under `src/research_mcp/templates/`, shipped via `[tool.uv]`/`setuptools` package-data (or `importlib.resources`), never generated at import time:

- `csl/`: `apa.csl`, `ieee.csl`, `vancouver.csl`, `chicago.csl` — downloaded once from the official CSL styles repo (citation-style-language/styles) at implementation time and committed.
- `latex/`: `ieee.tex`, `article_id.tex` — minimal Pandoc LaTeX templates (written as text; `ieee.tex` sets `documentclass` IEEEtran, `article_id.tex` is a generic article).
- `docx/`: `default_academic.docx` — generated once from `pandoc --print-default-data-file reference.docx` and committed. (`thesis_id.docx` is named in the tree but not required by any tool; omitted.)

`export_to_docx(template_name="default_academic")` and `export_to_latex(template_name="ieee")` resolve these by name from the bundled templates; an unknown name raises a `ToolError` listing available templates.

### 6b. Builder-check resolutions (post-review, my calls)

A builder-check pass verified the schema/deps/template claims (all correct;
`numpy` is already transitive but must be promoted to a direct dep) and raised
three questions plus several break-risks. All are implementation-correctness
with a clear right answer, so I resolved them directly rather than opening a
round:

1. **Exporters are self-contained.** `export_to_docx`/`export_to_latex` do not
   trust a pre-existing on-disk `references.bib`/`.csl.json`; each regenerates
   both from `list_papers` + Fase 3's `build_bibtex_entry`/`build_csl_entry`
   (the same builders `generate_bibliography` uses) immediately before the
   Pandoc/Tectonic call, writing them fresh under the project workspace. This
   removes any stale-bibliography risk and keeps the Zero-Hallucination gate
   meaningful.
2. **Orphan draft files are not silently dropped.** `merge_draft_markdown`
   globs `drafts/*.md` (same source Fase 3's `verify_citations` sees); files
   with a `draft_sections` row are ordered by `section_order` then filename;
   files with no row (hand-created) are appended after them, ordered by
   filename. Nothing on disk that gets citation-verified is ever excluded from
   an export.
3. **LaTeX templates are derived, not hand-minimal.** A hand-written minimal
   `.tex` would omit Pandoc's conditional package blocks (`graphicx`,
   `booktabs`, `hyperref`, `csquotes`, `amsmath`, …) and fail `tectonic`.
   `ieee.tex`/`article_id.tex` are therefore generated from
   `pandoc --print-default-template latex` with only `\documentclass` swapped
   (`IEEEtran` for ieee, `article` for article_id) and committed. IEEEtran is
   fetchable by Tectonic's TeX Live bundle, so the class swap is safe.
4. **Chart allowlist expanded.** The example list in §2 was too narrow and
   would break `sorted`/`isinstance`/`type`/`any`/`all`/`Exception`/`class`.
   Final allowlist adds those harmless names but still excludes `__import__`,
   `open`, `eval`, `exec`, `os`, `sys`, `subprocess`. `sys.stdout`/`sys.stderr`
   are redirected to a buffer around the `exec` (surfaced in errors) so a stray
   user `print()` cannot corrupt the MCP stdio frame.
5. **Markdown bundle reference list.** `export_to_markdown_bundle` runs
   `pandoc --citeproc -t markdown` so in-text `[@key]` renders and a
   `# References` section is appended (fenced citeproc divs), rather than
   leaving raw citation syntax.
6. **Template-set scope.** `springer.tex` and `thesis_id.docx` are
   intentionally omitted: the Fase 4 tool spec (§6.1.G) and checklist only
   name `ieee`/`article_id` (`.tex`) and `default_academic` (`.docx`); the
   AGENTS.md directory tree lists aspirational names not consumed by any tool
   this phase.
7. **Packaging wording.** The build backend is `uv_build`, which auto-includes
   `templates/**` (verified via `uv build`); no `[tool.setuptools]`/package-data
   config is needed or wanted. Templates load via
   `importlib.resources.files("research_mcp.templates")`.
8. **Task 6 integration corrections** (found by running the real binaries, not
   by mocked tests):
   * Every BibTeX field emitted by `build_bibtex_entry` is now brace-quoted,
     including `year` and `doi`. Pandoc 3's BibTeX reader rejects bare values
     such as `doi = 10.1234/x,` ("unexpected '.'"). Fase 3's tests used
     `bibtexparser`, which tolerates them, so this only surfaced at export time.
   * All three renderers pass `-M reference-section-title=References` so the
     `-t markdown` bundle gets a real `# References` heading (otherwise
     Citeproc emits a bare fenced `refs` div with no title).
   * `ieee.tex` uses `\documentclass[10pt,journal,onecolumn]{IEEEtran}`.
     IEEEtran's default `journal` mode is two-column and Pandoc always renders
     pipe tables as `longtable`, which hard-errors with "longtable not in
     1-column mode" under Tectonic/XeTeX. `onecolumn` keeps IEEE typography and
     makes tables compile; it is an IEEEtran-supported class option.
   * Pandoc `--resource-path <project_dir>` resolves `assets/...` links; LaTeX
     additionally receives an injected `\graphicspath` because TeX resolves
     image paths relative to the `.tex` file in `exports/`.

### 7. Citation gate (shared by exporters)

A small `_run_citation_gate(conn, project_id, markdown, strict)` helper reuses Fase 3's `extract_citekeys` + `diff_citations`: if `strict_citations` and `cited_but_missing` is non-empty, raise `ToolError` naming the missing citekeys before any Pandoc/Tectonic call. Non-strict mode proceeds and returns the missing list as a warning.

### 8. Tool output contracts

- `export_to_docx` → `{"output_path": str, "format": "docx", "csl_style": ..., "cited_but_missing": [...], "warnings": [...]}`.
- `export_to_latex` → `{"output_path": str, "tex_path": str, "pdf_path": str | None, "format": "latex", ...}` (`pdf_path` present iff `compile_pdf`).
- `export_to_markdown_bundle` → `{"output_path": str, "format": "markdown", ...}`.
- `generate_chart` → `{"assets": [{"path", "markdown"}], "dpi": ...}`.
- `save_draft_section` / `read_draft_section` as §1.

## What's Left to the Builder

- Exact safe-builtins allowlist contents for the chart sandbox.
- Exact pandoc/tectonic subprocess flag ordering and stderr capture.
- Whether templates are loaded via `importlib.resources` or a `Path(__file__).parent`-relative lookup (both acceptable; pick one and be consistent).
- Test fixtures: a fake `pandoc`/`tectonic` via `shutil.which` monkeypatch for unit tests, plus one real end-to-end export test (guarded/skippable when binaries are absent) for the Fase 4 verification criterion.
