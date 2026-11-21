# Design: Fase 5 — WordCraft MCP Interoperability & Handoff Engine

**Status:** Draft, pending builder check
**Branch:** `feature/phase-5-wordcraft-bridge` (forked from `main` at `9351765`)
**Spec source:** AGENTS.md §3 (ArtCraft/WordCraft specifics), §6.1.F (`check_wordcraft_env`, `prepare_wordcraft_handoff`), §6.3 (`write_in_wordcraft`, `peer_review_in_wordcraft`), §7 "Fase 5", §5.2 rules 4–5.

## Intent and Why

Fase 4 made the draft publishable on its own (Pandoc → DOCX, Tectonic → PDF).
Fase 5 makes the same draft *handoff-ready* for a second, richer writer: the
WordCraft MCP server that lives in the user's AI client. Research MCP stays the
single source of truth for metadata, citations and drafts; WordCraft owns
typography, live preview, tracked changes and PDF export.

The phase delivers three things:

1. **Environment truth** — `check_wordcraft_env` answers, in one call, "is
   `wordcraft-cli` here, is a live desktop reachable, what confinement flags and
   config snippet should I use, and what is my fallback?" — instead of an agent
   guessing paths per OS.
2. **A verified handoff** — `prepare_wordcraft_handoff` runs the Zero-Hallucination
   gate first, then produces either a *Writing Blueprint* (live authoring), a
   *seed DOCX* to open in WordCraft, or a *`job.json`* for `wordcraft-cli run`.
3. **Two workflow prompts** — `write_in_wordcraft` and
   `peer_review_in_wordcraft` spell out the exact WordCraft tool sequence,
   including the sharp edges (track-changes `value`, `caret.docEnd`, delivery
   `outcome unknown`, `--connect` vs roots).

## Goals

- `check_wordcraft_env(project_id=None)` reports: CLI found/path/version, desktop
  app found/path, live-desktop port + control-key state, absolute workspace root,
  ready-to-paste `mcp` command with `--automation-read-root`/`--automation-write-root`,
  fallback binary availability (Pandoc/Tectonic), and actionable recommendations.
- `prepare_wordcraft_handoff(project_id, csl_style="apa", mode="live_authoring",
  strict_citations=True, output_name=None)` never hands off a draft with
  unresolvable citekeys when `strict_citations=True`.
- `live_authoring` returns a deterministic blueprint: Pandoc-Citeproc-rendered
  blocks, absolute asset paths, and an ordered list of WordCraft tool calls.
- `seed_docx` returns an absolute `.docx` path produced by the same
  Pandoc + stale-free bibliography path Fase 4 already uses.
- `batch_script` writes a `job.json` that `wordcraft-cli run --script` accepts
  (real key names: `file`, `template`, `commands`, `save`), with absolute paths
  and a size guard.

## Non-Goals

- Implementing the WordCraft MCP server or executing MCP calls to it — the AI
  client orchestrates two servers; we only detect and instruct.
- `--connect` automation. It cannot be combined with the confinement roots
  (WordCraft rejects it), and it is a live-desktop convenience we do not own.
- Zotero/RIS round-trips (`references.sources`, `.bib` import). Fase 5 formats
  citations through Citeproc; WordCraft's own bibliography field is offered as
  an optional, documented alternative.
- MCP Resources (`research://…`) and the remaining Fase 6 prompts — Fase 6.

## Anti-Goals

- A handoff that silently drops a chapter, an asset, or a citation.
- A blueprint full of invented command ids. Every command named in the output
  exists in WordCraft 0.18.x (`references.toc`, `references.bibliography`,
  `references.citationStyle`, `insert.picture`, `file.setAuthor`, `file.save`,
  `file.exportPdf`, `file.strictReport`, `caret.docEnd`, `styles.modify`).
- Detection that crashes the server when no WordCraft exists: every probe is
  best-effort and time-boxed.

## Constraints

- Same engineering rules as Fase 1–4: strict typing + Pydantic models where a
  payload shape is stable, path-traversal protection on every write, no stdout,
  `ruff`/`pytest` green per task.
- No new Python dependency. TCP probing uses `socket`; process probing uses
  `shutil.which`/`subprocess` with explicit timeouts.
- The WordCraft CLI is optional on a machine: all integration tests skip when
  `wordcraft-cli` is absent.
- Windows/macOS/Linux/FreeBSD path rules follow AGENTS.md §3.1 plus the real
  `crates/control-key` implementation (settings dir + `control-key.local-<port>`).

## Approaches Considered

**CLI discovery.**
- **A — `PATH` → env override → per-OS candidate list (chosen).** Predictable,
  testable with a temp dir, no subprocess needed to find the binary.
- **B — `mdfind`/registry/`flatpak list` probing.** OS-specific, slow, and adds
  a dependency on shell tools that may not exist. Rejected.

**Live-desktop detection.**
- **A — TCP connect to `127.0.0.1:7981` + control-key file check (chosen).**
  Cheap, no protocol knowledge, tells the agent both "the app is up" and "the
  bridge can authenticate".
- **B — speak the control protocol / start an MCP handshake.** Overkill and
  duplicates the bridge we are not implementing. Rejected.

**Blueprint shape.**
- **A — render the draft with Citeproc, parse it into typed blocks, and emit an
  ordered tool sequence (chosen).** The agent gets final citation text plus
  absolute asset paths; nothing to re-render and nothing to guess.
- **B — return the rendered Markdown path and let the agent re-read it.**
  Simpler, but loses the "which WordCraft call for this block" mapping that the
  handoff exists to provide. Rejected.

**Batch script.**
- **A — seed DOCX first, then `job.json` opens it, styles it, and saves DOCX+PDF
  (chosen).** Reuses the citation-gated Pandoc output; deterministic; small
  command list.
- **B — rebuild the document in WordCraft from `template: "sample"` with
  `text.insert` commands.** Duplicates Pandoc Citeproc and blows past the 4 MiB
  cap on a real thesis. Rejected.

## The How (my calls)

### 1. `exporters/wordcraft_bridge.py` — detection

- `detect_wordcraft_cli() -> Path | None`
  1. `RESEARCH_MCP_WORDCRAFT_CLI` (absolute path, must be a file and executable)
  2. `shutil.which("wordcraft-cli")`
  3. per-OS candidates, first existing wins:
     - macOS: `/Applications/WordCraft.app/Contents/MacOS/wordcraft-cli`,
       `/opt/homebrew/bin`, `/usr/local/bin`, `~/Applications/WordCraft.app/Contents/MacOS`,
       `~/.local/bin`, `~/bin`
     - Windows: `%LOCALAPPDATA%\Programs\ArtCraft\WordCraft`,
       `C:\Program Files\ArtCraft\WordCraft`, plus `%LOCALAPPDATA%\Programs\wordcraft-cli`
     - Linux/FreeBSD: `/usr/bin`, `/usr/local/bin`, `/snap/bin`, `~/.local/bin`,
       `~/.var/app/ai.storyteller.artcraft-launcher/data/artcraft-launcher/apps/wordcraft`
- `detect_wordcraft_desktop() -> Path | None` — `/Applications/WordCraft.app`,
  `~/Applications/WordCraft.app`, `%LOCALAPPDATA%\Programs\ArtCraft\WordCraft`,
  `/usr/bin/wordcraft`, AppImage under `~/Applications`, Flatpak export dir.
- `wordcraft_settings_dir() -> Path | None` — `WORDCRAFT_PROFILE_DIR`, else macOS
  `~/Library/Application Support/WordCraft`, Windows `%APPDATA%/WordCraft`, else
  `$XDG_CONFIG_HOME/wordcraft` or `~/.config/wordcraft`.
- `control_key_path(port) -> Path | None` — `<settings>/control-key.local-<port>`.
- `probe_port(host="127.0.0.1", port=7981, timeout=0.25) -> bool` — TCP connect.
- `cli_version(binary) -> str | None` — `wordcraft-cli --version`, 5 s timeout,
  first line; failures become `None`, never an exception.

### 2. `check_wordcraft_env(project_id=None)`

Returns a flat dict (JSON-safe, no Pydantic round-trip needed):

```python
{
  "wordcraft_cli": {"found": bool, "path": str | None, "version": str | None},
  "desktop_app": {"found": bool, "path": str | None},
  "live_desktop": {"port": 7981, "port_open": bool,
                   "control_key_path": str | None, "control_key_present": bool,
                   "ready": bool},
  "workspace_root": str,                # absolute, exists
  "confinement": {"read_root": str, "write_root": str,
                  "mcp_command": [str, ...]},   # ready-to-paste argv
  "fallback": {"pandoc": str | None, "tectonic": str | None},
  "recommendations": [str, ...],
}
```

- `ready = port_open and control_key_present`.
- `confinement.mcp_command` is
  `[cli_or_"wordcraft-cli", "mcp", "--author", "Research Agent",
    "--automation-read-root", root, "--automation-write-root", root]`.
- Recommendations are ordered from best to worst: use the live desktop when ready;
  else headless with roots; else install WordCraft; else fall back to
  `export_to_docx`.
- `project_id` adds `"project": {"id", "slug", "workspace_dir", "draft_chapters",
  "papers"}` and never fails the rest of the report if the project is unknown —
  it raises `ToolError` only when an explicit `project_id` does not exist (same
  contract as other tools).

### 3. `prepare_wordcraft_handoff(...)`

Shared preamble for every mode:

1. Load the project; resolve `project_dir`.
2. `sections = list_draft_sections`; `markdown = merge_draft_markdown(...)`;
   empty draft → `ToolError` mirroring `_draft_markdown_or_raise`.
3. Citation gate (`_citation_gate`); missing + `strict` → `ToolError` listing
   the citekeys.
4. `_refresh_bibliography` → absolute `references.bib` + `references.csl.json`.
5. `assets = sorted((project_dir / "assets").glob(...))` for `.png/.svg/.jpg/.jpeg/.webp`.

**`live_authoring`**

- Render through Citeproc to `exports/<stem>-handoff.md` with `render_markdown`
  (same flags as Fase 4: `--resource-path`, `reference-section-title=References`).
- Parse the rendered Markdown into blocks:
  - `heading` (`level`, `text`), `paragraph` (`text`), `table` (`header`, `rows`),
    `list` (`ordered`, `items`), `figure` (`alt`, `path` absolute),
    `code` (`language`, `text`).
  - Block kinds beyond these stay `paragraph` — never dropped.
  - Relative `assets/...` and `drafts/...` links are rewritten to absolute paths.
- Blueprint dict:

```python
{
  "document_title": project.name,
  "markdown_path": str,            # the rendered copy
  "blocks": [...],
  "bibliography": {"title": "References", "markdown": "..."},
  "assets": [absolute, ...],
  "tool_sequence": [...],          # concrete WordCraft calls
  "warnings": [...],
}
```

- `tool_sequence` items: `{"tool": "new_document", "arguments": {...}}` or
  `{"tool": "execute", "arguments": {"command": "...", "params": {...}}}`.
  Sequence: `new_document` → `file.setAuthor` → `styles.modify` (Normal,
  Heading1–3) → per-block writer calls (`type_text`, `insert_table`,
  `execute insert.picture`) → `execute references.citationStyle` (mapped
  APA→APA, IEEE→IEEE, Vancouver→`ISO 690 (numerical)`, Chicago→Chicago) →
  bibliography text → `caret.docEnd` → `file.strictReport` → `render_page` →
  `save_document` `.docx` → `save_document` `.pdf` with `strict: true`.
- Warnings: `caret` note when a generated list is inserted; "paths are absolute,
  keep the confinement roots at the workspace root"; "`outcome unknown` →
  `inspect_document` before repeating".

**`seed_docx`**

- Reuse a new shared helper `exporters/api`-level function `_render_docx_export`
  inside `server.py` (extracted from `export_to_docx` so both tools share one
  code path): bibliography refresh + `render_docx` + `polish_docx`.
- Return `seed_docx_path`, the follow-up tool sequence
  (`open_document` → `inspect_document` → `styles.modify` → `references.toc` →
  `caret.docEnd` → `file.strictReport` → `render_page` → `save_document` docx/pdf).

**`batch_script`**

- Produce the seed DOCX (same helper) at `exports/<stem>.docx`.
- Write `scripts/job.json`:

```json
{
  "file": "<abs seed docx>",
  "commands": [
    {"command": "file.setAuthor", "params": {"name": "Research Agent"}},
    {"command": "references.citationStyle", "params": {"style": "APA"}},
    {"command": "references.toc", "params": {"levels": 3, "title": "Contents"}},
    {"command": "caret.docEnd"}
  ],
  "save": ["<abs .docx>", "<abs .pdf>"]
}
```

- Guard: `len(json.dumps(...)) <= 4 MiB` (WordCraft's cap) and `save` ≤ 64 entries.
- Return `script_path` plus the exact run command
  `["wordcraft-cli", "run", "--script", "<abs>"]` and the CLI path if found.

All three return a `HandoffPayload`-shaped dict, extended with extra keys
(blueprint/asset/bibliography details). `HandoffPayload` in `models.py` gains
`handoff_markdown_path`, `assets`, `tool_sequence`, `run_command`, and `warnings`
so the model documents the real payload.

### 4. Prompts

Registered with `@mcp.prompt()`; each returns a single user message (string) with
the workflow. They are *static guidance + parameters*, no DB access:

- `write_in_wordcraft(project_id, csl_style="apa", target_filename="manuscript")`:
  1. `check_wordcraft_env(project_id)`; if CLI/live desktop missing → stop and
     use `prepare_wordcraft_handoff(mode="seed_docx")` or `export_to_docx`.
  2. `prepare_wordcraft_handoff(project_id, csl_style, "live_authoring")`; obey
     `cited_but_missing` — never write a document that mentions it.
  3. Execute `tool_sequence` verbatim; `file.setAuthor` + `review.trackChanges
     {"value": true}` only in revision mode.
  4. `file.strictReport` must say `"strict": "passed"`; `render_page` the first
     and last page; `save_document` to `target_filename.docx` and `.pdf`.
  5. On `outcome unknown` → `inspect_document` first; never repeat blindly.
- `peer_review_in_wordcraft(project_id, docx_path)`:
  `check_wordcraft_env` → `open_document` → `file.setAuthor` →
  `review.trackChanges {"value": true}` → compare claims against
  `search_local_library` / `read_paper_section` → insert comments and tracked
  edits → `inspect_document` → `save_document` to a `-reviewed` copy →
  summarize findings with citekeys.

## Test Plan

- Unit (`tests/unit/test_wordcraft_bridge.py`): CLI discovery (env override,
  PATH, candidate list, none), settings dir per OS via monkeypatched env/platform,
  control-key path, port probe against a real listening socket and a closed port,
  version probe with a stub script, blueprint parsing (headings, paragraphs,
  tables, lists, figures, code), absolute-path rewriting, job.json structure and
  4 MiB guard, citation-style mapping.
- Unit (`tests/unit/test_wordcraft_tools.py`): `check_wordcraft_env` shape and
  recommendations with stubbed probes; `prepare_wordcraft_handoff` for all three
  modes with a real project fixture and mocked Pandoc (unit) / real Pandoc
  (integration); strict-gate refusal; unknown project/mode/style errors.
- Unit (`tests/unit/test_wordcraft_prompts.py`): prompts registered, message
  mentions `file.strictReport`, `caret.docEnd`, `outcome unknown`, `--connect`
  incompatibility.
- Integration (`tests/integration/test_wordcraft_e2e.py`, `skipif` when
  `wordcraft-cli`/Pandoc missing): `seed_docx` produces a readable DOCX;
  `batch_script` runs through real `wordcraft-cli run --script` and emits both
  outputs; `live_authoring` blueprint file exists and contains Citeproc output.

## Risks / Open Items

- **WordCraft version drift.** Command ids used here are verified against the
  0.18.x catalog. `job.json` may need a `template` instead of `file` on older
  builds; the blueprint carries both notes.
- **Live desktop is unconfined.** `--connect` cannot use roots; documented in the
  env report so the user knows the trade-off.
- **No `wordcraft-cli` on the build machine yet.** Real end-to-end verification
  requires installing the official 0.18.1 macOS CLI (~103 MB); otherwise the
  integration test skips, as Fase 4's did without Pandoc.
