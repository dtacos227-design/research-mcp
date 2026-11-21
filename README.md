# Research MCP

A **Model Context Protocol (MCP) server** for end-to-end scientific research — 100% free, local-first, and compatible with every MCP-capable CLI (**Claude Code, OpenCode, Codex, Agy**).

Search 250 M+ papers across six academic databases, ingest and deep-read PDFs, manage citations with an anti-hallucination gate, draft chapters in Markdown, generate publication-quality figures, and export to `.docx`, `.tex`/`.pdf`, or directly into **WordCraft MCP** — all without paying for a single API subscription.

---

## Features

| Area | What it does |
|------|-------------|
| **Literature Discovery** | Parallel search across OpenAlex, Semantic Scholar, arXiv, PubMed, Crossref, Unpaywall; deduplication, DOI validation, citation-graph traversal |
| **PDF Ingestion** | Auto-download legal open-access PDFs, section-by-section Markdown extraction, full-text search via SQLite FTS5 |
| **Citation Management** | Deterministic `citekey` generation, `.bib` + `.csl.json` export, Pandoc Citeproc formatting (APA, IEEE, Vancouver, Chicago, MLA) |
| **Drafting** | Per-chapter Markdown drafts with `[@citekey]` syntax, anti-hallucination `verify_citations` gate |
| **Visualisation** | Matplotlib/Seaborn chart runner locked to `assets/`, 300 DPI PNG/SVG |
| **Publishing** | WordCraft MCP (3 modes), standalone Pandoc `.docx`, Tectonic LaTeX `.pdf` |

---

## Prerequisites

| Tool | Required | Notes |
|------|----------|-------|
| Python 3.11+ | Yes | |
| [`uv`](https://docs.astral.sh/uv/) | Yes | Package manager and runner |
| [Pandoc](https://pandoc.org/installing.html) ≥ 3.0 | Yes | Citation rendering and DOCX export |
| [Tectonic](https://tectonic-typesetting.github.io/) | Optional | LaTeX → PDF; auto-downloads TeX packages |
| [WordCraft](https://github.com/storytold/wordcraft) (`wordcraft-cli`) | Optional | Advanced `.docx` authoring via MCP |

---

## Installation

```bash
git clone https://github.com/your-org/research-mcp.git
cd research-mcp
uv sync
```

Verify the server starts:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0"}}}' \
  | timeout 5 uv run research-mcp 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['result']['serverInfo']['name'])"
# → research-mcp
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RESEARCH_MCP_WORKSPACE` | `~/.research-mcp/workspaces` | Root directory for all project data |
| `RESEARCH_MCP_EMAIL` | *(none)* | Added to `User-Agent` header for Polite Pool APIs (OpenAlex, Crossref, Unpaywall) — strongly recommended |
| `RESEARCH_MCP_DB_PATH` | `<workspace>/../research_mcp.db` | Override SQLite database path |
| `RESEARCH_MCP_LOG_LEVEL` | `WARNING` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) — logs go to stderr only |

---

## MCP Client Configuration

### Claude Code

```bash
# Research MCP
claude mcp add research-mcp \
  -e RESEARCH_MCP_EMAIL="rezalkaryanto@gmail.com" \
  -e RESEARCH_MCP_WORKSPACE="$HOME/.research-mcp/workspaces" \
  -- uv --directory /path/to/research-mcp run research-mcp

# WordCraft MCP — headless mode (confined to shared workspace)
claude mcp add wordcraft \
  -- wordcraft-cli mcp \
     --author "Research Agent" \
     --automation-read-root "$HOME/.research-mcp/workspaces" \
     --automation-write-root "$HOME/.research-mcp/workspaces"

# WordCraft MCP — live desktop mode (run `wordcraft --control 7981` first)
# NOTE: --connect must NOT be combined with --automation-*-root flags
claude mcp add wordcraft-app -- wordcraft-cli mcp --connect 127.0.0.1:7981
```

### OpenCode (`~/.config/opencode/opencode.json`)

```json
{
  "mcp": {
    "research-mcp": {
      "type": "local",
      "command": ["uv", "--directory", "/path/to/research-mcp", "run", "research-mcp"],
      "environment": {
        "RESEARCH_MCP_EMAIL": "rezalkaryanto@gmail.com",
        "RESEARCH_MCP_WORKSPACE": "/home/user/.research-mcp/workspaces"
      },
      "enabled": true
    },
    "wordcraft": {
      "type": "local",
      "command": [
        "wordcraft-cli", "mcp",
        "--author", "Research Agent",
        "--automation-read-root", "/home/user/.research-mcp/workspaces",
        "--automation-write-root", "/home/user/.research-mcp/workspaces"
      ],
      "enabled": true
    }
  }
}
```

### Codex CLI (`~/.codex/config.toml`)

```toml
[mcp_servers.research-mcp]
command = "uv"
args = ["--directory", "/path/to/research-mcp", "run", "research-mcp"]

[mcp_servers.research-mcp.env]
RESEARCH_MCP_EMAIL = "rezalkaryanto@gmail.com"
RESEARCH_MCP_WORKSPACE = "/home/user/.research-mcp/workspaces"

[mcp_servers.wordcraft]
command = "wordcraft-cli"
args = [
  "mcp",
  "--author", "Research Agent",
  "--automation-read-root", "/home/user/.research-mcp/workspaces",
  "--automation-write-root", "/home/user/.research-mcp/workspaces"
]
```

### Agy / Generic JSON (`mcpServers`)

```json
{
  "mcpServers": {
    "research-mcp": {
      "command": "uv",
      "args": ["--directory", "/path/to/research-mcp", "run", "research-mcp"],
      "env": {
        "RESEARCH_MCP_EMAIL": "rezalkaryanto@gmail.com",
        "RESEARCH_MCP_WORKSPACE": "/home/user/.research-mcp/workspaces"
      }
    },
    "wordcraft": {
      "command": "wordcraft-cli",
      "args": [
        "mcp",
        "--author", "Research Agent",
        "--automation-read-root", "/home/user/.research-mcp/workspaces",
        "--automation-write-root", "/home/user/.research-mcp/workspaces"
      ]
    }
  }
}
```

---

## Tools Reference

| # | Tool | Description |
|---|------|-------------|
| 1 | `create_project` | Create a new isolated research project |
| 2 | `list_projects` | List all projects with paper/draft counts |
| 3 | `get_project_status` | Full project status: paths, citations, drafts, assets, exports |
| 4 | `search_papers` | Parallel multi-source search with deduplication |
| 5 | `get_citation_graph` | Forward/backward citation graph traversal |
| 6 | `resolve_doi` | Metadata + open-access PDF check for a DOI |
| 7 | `save_paper_to_project` | Save paper + download/parse PDF into project |
| 8 | `import_local_pdf` | Import a local PDF (e.g. from institution access) |
| 9 | `list_paper_sections` | List parsed sections of a saved paper |
| 10 | `read_paper_section` | Read one section without loading the full PDF |
| 11 | `search_local_library` | Full-text search across all project PDFs (FTS5) |
| 12 | `generate_bibliography` | Build `references.bib` and `references.csl.json` |
| 13 | `verify_citations` | Anti-hallucination gate: audit `[@citekey]` in draft text |
| 14 | `save_draft_section` | Save/update a Markdown draft chapter |
| 15 | `read_draft_section` | Read a draft chapter |
| 16 | `generate_chart` | Run matplotlib/seaborn code → 300 DPI PNG/SVG in `assets/` |
| 17 | `check_wordcraft_env` | Detect WordCraft CLI install and live desktop status |
| 18 | `prepare_wordcraft_handoff` | Package verified draft for WordCraft (3 modes) |
| 19 | `export_to_docx` | Export to `.docx` via Pandoc + python-docx |
| 20 | `export_to_latex` | Export to `.tex` and optionally compile `.pdf` via Tectonic |
| 21 | `export_to_markdown_bundle` | Combine all chapters into one Markdown file |

---

## Resources Reference

| URI | Description |
|-----|-------------|
| `research://projects` | JSON list of all research projects |
| `research://templates` | Available CSL, DOCX, and LaTeX templates |
| `research://project/{project_id}/bibliography` | Contents of `references.bib` |
| `research://project/{project_id}/csl-json` | Contents of `references.csl.json` |
| `research://project/{project_id}/outline` | Draft chapter outline (Markdown table) |

---

## Prompts Reference

| Prompt | Parameters | Description |
|--------|------------|-------------|
| `systematic_literature_review` | `topic`, `year_from` | End-to-end SLR workflow: search → parse → draft → export |
| `draft_latex_paper` | `project_id`, `template_name` | IMRaD draft → LaTeX → Tectonic PDF |
| `write_in_wordcraft` | `project_id`, `csl_style`, `target_filename` | Research MCP → WordCraft MCP writing workflow |
| `peer_review_in_wordcraft` | `project_id`, `docx_path` | Audit document in WordCraft with tracked changes |

---

## WordCraft Integration

Research MCP integrates with [WordCraft MCP](https://github.com/storytold/wordcraft) for publication-quality document authoring. Three modes are available via `prepare_wordcraft_handoff`:

| Mode | Description |
|------|-------------|
| `live_authoring` | Returns a Writing Blueprint; AI writes directly into WordCraft via MCP tools |
| `seed_docx` | Exports a Pandoc `.docx` seed; WordCraft opens it for styling, TOC, and PDF export |
| `batch_script` | Generates `scripts/job.json` for deterministic `wordcraft-cli run --script` export |

**Key rules:**
- `--connect` must **not** be combined with `--automation-read-root`/`--automation-write-root`
- Always call `review.trackChanges` with `{"value": true}` (not as a toggle)
- Call `caret.docEnd` after inserting bibliography/TOC
- On `outcome unknown`: inspect with `inspect_document` before retrying

See [AGENTS.md §3](AGENTS.md#3-detail-riset--integrasi-ekosistem-artcraft-launcher--wordcraft-mcp) for the full integration specification.

---

## Development

```bash
# Run all tests
RESEARCH_MCP_SKIP_LIVE_TESTS=1 uv run pytest -q

# Run lint + format check
uv run ruff check .
uv run ruff format --check .

# Fix formatting
uv run ruff format .

# Run a specific test
uv run pytest tests/unit/test_resources.py -v
```

The test suite has three skip categories:
- `RESEARCH_MCP_SKIP_LIVE_TESTS=1` — skips live network integration tests
- Tests that require `wordcraft-cli` to be installed
- Pre-existing flaky network tests (run individually to confirm)

---

## Project Layout

```
research-mcp/
├── src/research_mcp/
│   ├── server.py          # MCP tools, resources, and prompts
│   ├── config.py          # Settings and logging
│   ├── models.py          # Pydantic v2 schemas
│   ├── db/                # SQLite + FTS5 (repository, migrations)
│   ├── providers/         # OpenAlex, Semantic Scholar, arXiv, PubMed, Crossref, Unpaywall
│   ├── parsers/           # PDF download, extraction, section splitting
│   ├── citations/         # BibTeX/CSL-JSON generation, citation verification
│   ├── writers/           # Draft management, chart runner
│   ├── exporters/         # Pandoc, python-docx, Tectonic, WordCraft bridge
│   └── templates/         # Bundled CSL, DOCX, and LaTeX templates
└── tests/
    ├── unit/              # Unit tests with mocked HTTP (respx)
    └── integration/       # Live-network integration tests
```
