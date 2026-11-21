# Fase 5 — WordCraft Interoperability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect a local WordCraft install, expose `check_wordcraft_env`, and turn a citation-verified draft into a WordCraft handoff (live blueprint, seed DOCX, or `job.json`), plus the two workflow prompts.

**Architecture:** One new module `exporters/wordcraft_bridge.py` holds every pure function (path detection, TCP probe, block parsing, blueprint and job builders). `server.py` keeps the thin MCP tools that add DB access, the citation gate and the shared DOCX export path. No new dependency; every OS-specific path goes through a parameter that tests can override.

**Tech Stack:** Python 3.11+, `mcp` FastMCP-style `MCPServer` (`@mcp.tool`, `@mcp.prompt`), Pydantic v2, `socket`, `shutil`, `subprocess`, pytest/pytest-asyncio, Pandoc (already required by Fase 4).

**Spec:** `docs/superpowers/specs/2026-11-21-phase-5-wordcraft-bridge-design.md`

## Global Constraints

- No new runtime dependency; standard library only for detection (`socket`, `shutil`, `subprocess`, `json`, `sys`, `os`).
- Every function that reads the environment takes `env: Mapping[str, str] | None = None` and `platform: str | None = None`; `None` means `os.environ` / `sys.platform`.
- Never write outside the project workspace: reuse `_safe_destination` for `exports/` and `scripts/` targets.
- No `print()`; MCP tool failures raise `mcp.server.mcpserver.exceptions.ToolError` with cause + fix.
- WordCraft command ids used verbatim: `file.setAuthor`, `styles.modify`, `para.heading1|2|3`, `para.set`, `para.bullets`, `para.numbering`, `insert.picture`, `insert_table`, `references.toc`, `references.bibliography`, `references.citationStyle`, `caret.docEnd`, `file.strictReport`, `file.exportPdf`, `file.save`, `file.open`, `file.new`.
- `job.json` keys are exactly `file`, `template`, `commands`, `save`; bytes ≤ 4 MiB; `save` ≤ 64 entries.
- Control-key file is `<settings>/control-key.local-<port>`; settings dir is `WORDCRAFT_PROFILE_DIR` or the OS settings folder.
- `ruff check .` and `ruff format --check .` must pass after every task.

## Review Focus

- A machine with no WordCraft at all: `check_wordcraft_env` must still return a useful report instead of raising.
- A draft with an unresolvable citekey and `strict_citations=True`: no handoff artefact may be written.
- Asset links in the draft (`assets/chart.png`) must arrive absolute and exist-checked, or the blueprint says so.
- A `job.json` larger than 4 MiB (huge thesis): must fail with a clear message, not be written.
- Windows/Linux hosts: settings dir and CLI candidates must follow the OS rules, not the macOS ones.

---

### Task 1: Detection primitives

**Files:**
- Create: `src/research_mcp/exporters/wordcraft_bridge.py`
- Test: `tests/unit/test_wordcraft_detection.py`

**Interfaces:**
- Produces:
  - `DEFAULT_PORT: int = 7981`, `WORDCRAFT_AUTHOR: str = "Research Agent"`
  - `wordcraft_settings_dir(env=None, platform=None) -> Path | None`
  - `control_key_path(port: int = DEFAULT_PORT, env=None, platform=None) -> Path | None`
  - `detect_wordcraft_cli(env=None, platform=None) -> Path | None`
  - `detect_wordcraft_desktop(platform=None, env=None) -> Path | None`
  - `probe_port(host: str = "127.0.0.1", port: int = DEFAULT_PORT, timeout: float = 0.25) -> bool`
  - `cli_version(binary: Path) -> str | None`
  - `build_mcp_command(cli: Path | None, workspace_root: Path, author: str = WORDCRAFT_AUTHOR) -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_wordcraft_detection.py
from pathlib import Path

from research_mcp.exporters import wordcraft_bridge as wb

def test_settings_dir_macos(tmp_path):
    env = {"HOME": str(tmp_path)}
    assert wb.wordcraft_settings_dir(env, "darwin") == tmp_path / "Library/Application Support/WordCraft"

def test_settings_dir_linux_xdg(tmp_path):
    env = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    assert wb.wordcraft_settings_dir(env, "linux") == tmp_path / "cfg" / "wordcraft"

def test_settings_dir_linux_default(tmp_path):
    env = {"HOME": str(tmp_path)}
    assert wb.wordcraft_settings_dir(env, "linux") == tmp_path / ".config" / "wordcraft"

def test_settings_dir_windows(tmp_path):
    env = {"APPDATA": str(tmp_path / "AppData")}
    assert wb.wordcraft_settings_dir(env, "win32") == tmp_path / "AppData" / "WordCraft"

def test_settings_dir_profile_override_wins(tmp_path):
    env = {"HOME": str(tmp_path), "WORDCRAFT_PROFILE_DIR": str(tmp_path / "profile")}
    assert wb.wordcraft_settings_dir(env, "darwin") == tmp_path / "profile"

def test_control_key_path(tmp_path):
    env = {"HOME": str(tmp_path)}
    assert wb.control_key_path(7981, env, "darwin") == (
        tmp_path / "Library/Application Support/WordCraft" / "control-key.local-7981"
    )

def test_detect_cli_from_env_override(tmp_path):
    binary = tmp_path / "wordcraft-cli"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    assert wb.detect_wordcraft_cli({"RESEARCH_MCP_WORDCRAFT_CLI": str(binary)}, "linux") == binary

def test_detect_cli_from_path(tmp_path, monkeypatch):
    binary = tmp_path / "wordcraft-cli"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert wb.detect_wordcraft_cli({}, "linux") == binary

def test_detect_cli_from_known_candidate(tmp_path):
    home = tmp_path / "home"
    candidate = home / ".local/bin/wordcraft-cli"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("#!/bin/sh\n")
    candidate.chmod(0o755)
    found = wb.detect_wordcraft_cli({"HOME": str(home), "PATH": ""}, "linux")
    assert found == candidate

def test_detect_cli_missing(tmp_path):
    assert wb.detect_wordcraft_cli({"HOME": str(tmp_path), "PATH": ""}, "linux") is None

def test_detect_desktop_macos(tmp_path):
    app = tmp_path / "Applications/WordCraft.app"
    app.mkdir(parents=True)
    assert wb.detect_wordcraft_desktop("darwin", {"HOME": str(tmp_path)}) == app

def test_probe_port_true_and_false():
    import socket
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert wb.probe_port("127.0.0.1", port) is True
    finally:
        server.close()
    assert wb.probe_port("127.0.0.1", port, timeout=0.1) is False

def test_cli_version_reads_first_line(tmp_path):
    binary = tmp_path / "wordcraft-cli"
    binary.write_text("#!/bin/sh\necho 'wordcraft-cli 0.18.1'\n")
    binary.chmod(0o755)
    assert wb.cli_version(binary) == "wordcraft-cli 0.18.1"

def test_cli_version_missing_binary(tmp_path):
    assert wb.cli_version(tmp_path / "nope") is None

def test_build_mcp_command():
    cmd = wb.build_mcp_command(Path("/x/wordcraft-cli"), Path("/work"), "Research Agent")
    assert cmd == [
        "/x/wordcraft-cli", "mcp", "--author", "Research Agent",
        "--automation-read-root", "/work", "--automation-write-root", "/work",
    ]

def test_build_mcp_command_without_cli():
    assert wb.build_mcp_command(None, Path("/work"))[0] == "wordcraft-cli"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_wordcraft_detection.py -v`
Expected: `ModuleNotFoundError` / `AttributeError` for `wordcraft_bridge`.

- [ ] **Step 3: Implement the module**

`_cli_candidates(platform, home, env) -> list[Path]` is a module-level helper returning, in order:
- macOS: `/Applications/WordCraft.app/Contents/MacOS/wordcraft-cli`, `/opt/homebrew/bin/wordcraft-cli`, `/usr/local/bin/wordcraft-cli`, `home/Applications/WordCraft.app/Contents/MacOS/wordcraft-cli`, `home/.local/bin/wordcraft-cli`, `home/bin/wordcraft-cli`
- `win32`: `env["LOCALAPPDATA"]/Programs/ArtCraft/WordCraft/wordcraft-cli.exe`, `env["LOCALAPPDATA"]/Programs/ArtCraft/WordCraft/wordcraft-cli`, `C:/Program Files/ArtCraft/WordCraft/wordcraft-cli.exe`
- else: `/usr/bin/wordcraft-cli`, `/usr/local/bin/wordcraft-cli`, `/snap/bin/wordcraft-cli`, `home/.local/bin/wordcraft-cli`, `home/.var/app/ai.storyteller.artcraft-launcher/data/artcraft-launcher/apps/wordcraft/wordcraft-cli`

`detect_wordcraft_cli`: env override (must be an existing file) → `shutil.which("wordcraft-cli", path=env.get("PATH"))` → candidates that are `is_file()`. Use `Path(env.get("PATH", ""))` only as the `path` argument; empty PATH means no PATH hit.

`detect_wordcraft_desktop`: macOS `/Applications/WordCraft.app` then `home/Applications/WordCraft.app`; `win32` `%LOCALAPPDATA%/Programs/ArtCraft/WordCraft`; else `/usr/bin/wordcraft`, `home/Applications/WordCraft.AppImage`, the Flatpak export dir.

`cli_version`: `subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=5)`; on any `OSError`/`SubprocessError` return `None`; return first non-empty stdout line or `None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_wordcraft_detection.py -v` → all PASS; then `uv run ruff check . && uv run ruff format --check .`.

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/exporters/wordcraft_bridge.py tests/unit/test_wordcraft_detection.py
git commit -m "feat: detect wordcraft-cli, desktop app and live control key"
```

---

### Task 2: Markdown blocks and the Writing Blueprint

**Files:**
- Modify: `src/research_mcp/exporters/wordcraft_bridge.py`
- Test: `tests/unit/test_wordcraft_blueprint.py`

**Interfaces:**
- Produces:
  - `citation_style_for(csl_style: str) -> str` — `apa→APA`, `ieee→IEEE`, `vancouver→ISO 690 (numerical)`, `chicago→Chicago`; anything else raises `ToolError`.
  - `parse_markdown_blocks(markdown: str, project_dir: Path) -> list[dict]`
  - `build_blueprint(project_name: str, markdown_path: Path, markdown: str, assets: list[Path], csl_style: str, exports_dir: Path, output_stem: str) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_wordcraft_blueprint.py
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.exporters import wordcraft_bridge as wb

def test_citation_style_mapping():
    assert wb.citation_style_for("apa") == "APA"
    assert wb.citation_style_for("ieee") == "IEEE"
    assert wb.citation_style_for("vancouver") == "ISO 690 (numerical)"
    assert wb.citation_style_for("chicago") == "Chicago"
    with pytest.raises(ToolError):
        wb.citation_style_for("harvard")

def test_parse_blocks_kinds(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/chart.png").write_bytes(b"png")
    markdown = (
        "# Introduction\n\n"
        "Body text with [@doe2024valid].\n\n"
        "| Month | Cases |\n|---|---|\n| Jan | 12 |\n\n"
        "- one\n- two\n\n"
        "![Chart](assets/chart.png)\n\n"
        "```python\nprint(1)\n```\n"
    )
    blocks = wb.parse_markdown_blocks(markdown, tmp_path)
    assert [b["kind"] for b in blocks] == ["heading", "paragraph", "table", "list", "figure", "code"]
    assert blocks[0] == {"kind": "heading", "level": 1, "text": "Introduction"}
    assert blocks[2]["header"] == ["Month", "Cases"]
    assert blocks[2]["rows"] == [["Jan", "12"]]
    assert blocks[3]["ordered"] is False and blocks[3]["items"] == ["one", "two"]
    assert blocks[4]["path"] == str(tmp_path / "assets/chart.png")
    assert blocks[5]["language"] == "python"

def test_relative_asset_without_file_is_kept_and_warned(tmp_path):
    markdown = "![Missing](assets/nope.png)\n"
    blocks = wb.parse_markdown_blocks(markdown, tmp_path)
    assert blocks[0]["path"] == str(tmp_path / "assets/nope.png")
    assert blocks[0]["exists"] is False

def test_build_blueprint_sequence(tmp_path):
    (tmp_path / "assets").mkdir()
    image = tmp_path / "assets/chart.png"
    image.write_bytes(b"png")
    markdown = "# Intro\n\nText.\n\n![Chart](assets/chart.png)\n"
    blueprint = wb.build_blueprint(
        "My Thesis", tmp_path / "exports/handoff.md", markdown,
        [image], "apa", tmp_path / "exports", "manuscript",
    )
    tools = [step["tool"] for step in blueprint["tool_sequence"]]
    commands = [step["arguments"].get("command") for step in blueprint["tool_sequence"]]
    assert tools[0] == "new_document"
    assert commands[1] == "file.setAuthor"
    assert "references.citationStyle" in commands
    assert "insert.picture" in commands
    assert tools[-2] == "save_document"
    assert commands[-1] == "file.exportPdf"
    assert blueprint["assets"] == [str(image)]
    assert blueprint["warnings"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_wordcraft_blueprint.py -v` → FAIL (`AttributeError`).

- [ ] **Step 3: Implement parsing and the builder**

`parse_markdown_blocks` walks lines with a small state machine: fenced code first, then table (a line starting `|` followed by a separator line), then list (`^\s*(?:[-*+]|\d+[.)])\s+`), then heading (`^#{1,6}\s+`), then a single-image paragraph (`^!\[(?P<alt>[^\]]*)\]\((?P<path>[^)\s]+)(?:\s+"[^"]*")?\)$`), else paragraph until a blank line. Figure paths are resolved with `Path(project_dir, path)`; `exists` records whether the file is there. Image links that are already absolute stay as they are.

`build_blueprint` emits, in order:
1. `{"tool": "new_document", "arguments": {"template": "blank"}}`
2. `file.setAuthor` with `WORDCRAFT_AUTHOR`
3. `styles.modify` for `Normal`, `Heading1`, `Heading2`, `Heading3` (Times New Roman; 12/16/14/12 pt; 1.5 line spacing on Normal)
4. `references.citationStyle` with `citation_style_for(csl_style)`
5. one writer step per block: heading → `type_text {"text": text}` then `para.headingN` (levels 4–6: `para.set {"props": {"style": "Heading4"}}`); paragraph → `type_text {"text": text, "paragraphs": True}`; table → `insert_table {"values": [header, *rows], "header": True}`; list → `type_text` of the items joined by `\n` with `paragraphs: True` then `para.bullets`/`para.numbering`; figure → `insert.picture {"path": path, "alt": alt}`; code → `type_text` with `paragraphs: True`
6. bibliography (added by the caller through `blueprint["bibliography"]`, appended as `type_text` + `caret.docEnd`)
7. `file.strictReport` → `render_page {"page": 1}` → `save_document {"path": exports_dir/output_stem.docx}` → `file.exportPdf {"path": exports_dir/output_stem.pdf, "strict": True}`

`warnings` starts empty; add one entry per figure with `exists is False`.
`bibliography` is `{"title": "References", "markdown": ""}` — the tool fills it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_wordcraft_blueprint.py -v` → PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: build a citeproc writing blueprint for wordcraft"
```

---

### Task 3: `job.json` builder

**Files:**
- Modify: `src/research_mcp/exporters/wordcraft_bridge.py`
- Test: `tests/unit/test_wordcraft_job.py`

**Interfaces:**
- Produces:
  - `build_job_script(seed_docx: Path, csl_style: str, docx_out: Path, pdf_out: Path, author: str = WORDCRAFT_AUTHOR, toc_title: str = "Contents") -> dict`
  - `serialize_job_script(script: dict) -> bytes` (raises `ToolError` over 4 MiB)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_wordcraft_job.py
import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.exporters import wordcraft_bridge as wb

def test_job_script_shape(tmp_path):
    script = wb.build_job_script(
        tmp_path / "exports/seed.docx", "apa",
        tmp_path / "exports/out.docx", tmp_path / "exports/out.pdf",
    )
    assert script["file"] == str(tmp_path / "exports/seed.docx")
    assert script["save"] == [str(tmp_path / "exports/out.docx"), str(tmp_path / "exports/out.pdf")]
    ids = [c["command"] for c in script["commands"]]
    assert ids[0] == "file.setAuthor"
    assert "references.citationStyle" in ids
    assert "references.toc" in ids
    assert ids[-1] == "caret.docEnd"
    assert script["commands"][1]["params"]["style"] == "APA"

def test_job_script_round_trips(tmp_path):
    script = wb.build_job_script(tmp_path / "a.docx", "ieee", tmp_path / "b.docx", tmp_path / "b.pdf")
    payload = wb.serialize_job_script(script)
    assert json.loads(payload) == script

def test_job_script_rejects_oversize(tmp_path):
    script = wb.build_job_script(tmp_path / "a.docx", "apa", tmp_path / "b.docx", tmp_path / "b.pdf")
    script["commands"].append({"command": "text.insert", "params": {"text": "x" * (4 * 1024 * 1024)}})
    with pytest.raises(ToolError, match="4 MiB"):
        wb.serialize_job_script(script)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_wordcraft_job.py -v` → FAIL.

- [ ] **Step 3: Implement both functions**

Commands list: `file.setAuthor {name}` → `references.citationStyle {style}` → `references.toc {"levels": 3, "title": toc_title}` → `caret.docEnd`. `serialize_job_script` = `json.dumps(script, indent=2).encode("utf-8")` with the 4 MiB guard (`ToolError` naming the size and WordCraft's cap) and a `save` length check against 64.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_wordcraft_job.py -v` → PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: generate wordcraft-cli job scripts"
```

---

### Task 4: `check_wordcraft_env` tool

**Files:**
- Modify: `src/research_mcp/server.py` (new tool near the export tools), `src/research_mcp/models.py` (extend `HandoffPayload`)
- Test: `tests/unit/test_wordcraft_env_tool.py`

**Interfaces:**
- Consumes: Task 1 functions, `get_settings()`, `repository.get_project`.
- Produces:
  - `HandoffPayload` gains `handoff_markdown_path: str | None`, `assets: list[str]`, `tool_sequence: list[dict]`, `run_command: list[str]`, `warnings: list[str]`
  - `async def check_wordcraft_env(project_id: str | None = None) -> dict` with the shape in the spec §2.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_wordcraft_env_tool.py
import pytest
from research_mcp import server
from research_mcp.exporters import wordcraft_bridge as wb

@pytest.mark.asyncio
async def test_env_report_without_wordcraft(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setattr(wb, "detect_wordcraft_cli", lambda *a, **k: None)
    monkeypatch.setattr(wb, "detect_wordcraft_desktop", lambda *a, **k: None)
    monkeypatch.setattr(wb, "probe_port", lambda *a, **k: False)
    monkeypatch.setattr(wb, "control_key_path", lambda *a, **k: tmp_path / "control-key.local-7981")
    report = await server.check_wordcraft_env()
    assert report["wordcraft_cli"]["found"] is False
    assert report["live_desktop"]["ready"] is False
    assert report["confinement"]["mcp_command"][1] == "mcp"
    assert any("export_to_docx" in r for r in report["recommendations"])

@pytest.mark.asyncio
async def test_env_report_ready_live_desktop(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "ws"))
    key = tmp_path / "control-key.local-7981"
    key.write_text("token")
    monkeypatch.setattr(wb, "detect_wordcraft_cli", lambda *a, **k: None)
    monkeypatch.setattr(wb, "probe_port", lambda *a, **k: True)
    monkeypatch.setattr(wb, "control_key_path", lambda *a, **k: key)
    report = await server.check_wordcraft_env()
    assert report["live_desktop"]["ready"] is True
    assert any("--connect" in r for r in report["recommendations"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_wordcraft_env_tool.py -v` → FAIL (no `check_wordcraft_env`).

- [ ] **Step 3: Implement the tool**

Build the report from the bridge functions plus `detect_pandoc`/`detect_tectonic` in `try/except ToolError` (`None` on failure). `workspace_root = str(get_settings().workspace_dir)`. With `project_id`, add a `project` section (`id`, `slug`, `workspace_dir`, `draft_chapters` from `list_draft_sections`, `papers` from `list_papers`) and raise `ToolError` when the project does not exist. Recommendations, in order: live desktop ready → "Open WordCraft with `--control 7981` and use `--connect`; roots cannot be combined with `--connect`."; CLI found → "Use the headless MCP server with these roots: …"; neither → "Install WordCraft … or use `export_to_docx` / `prepare_wordcraft_handoff(mode='seed_docx')`."

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_wordcraft_env_tool.py -v` → PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: report wordcraft environment and confinement roots"
```

---

### Task 5: `prepare_wordcraft_handoff`

**Files:**
- Modify: `src/research_mcp/server.py`
- Test: `tests/unit/test_wordcraft_handoff_tool.py`

**Interfaces:**
- Consumes: `_citation_gate`, `_refresh_bibliography`, `_export_target`, `_draft_markdown_or_raise`, `render_markdown`, `render_docx`, `polish_docx`, `resolve_csl`, `resolve_docx_template`, `bundled_template`, Task 2/3 builders.
- Produces:
  - `_render_docx_export(conn, project, project_dir, output_name, csl_style, template_name, polish) -> tuple[Path, list[str]]` (extracted from `export_to_docx`; that tool becomes a thin wrapper)
  - `async def prepare_wordcraft_handoff(project_id: str, csl_style: str = "apa", mode: Literal["live_authoring", "seed_docx", "batch_script"] = "live_authoring", strict_citations: bool = True, output_name: str = "manuscript") -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_wordcraft_handoff_tool.py
# Reuse the project fixture pattern from tests/unit/test_export_tools.py.
# Mock render_markdown/render_docx to copy input and record args.

@pytest.mark.asyncio
async def test_handoff_refuses_unknown_citekey(project_with_bad_draft, monkeypatch):
    with pytest.raises(ToolError, match="ghost2099"):
        await server.prepare_wordcraft_handoff(project_with_bad_draft.id)

@pytest.mark.asyncio
async def test_live_authoring_returns_blueprint(project_with_drafts, monkeypatch):
    monkeypatch.setattr(server, "render_markdown", fake_markdown_writer)
    result = await server.prepare_wordcraft_handoff(project_with_drafts.id)
    assert result["mode"] == "live_authoring"
    assert Path(result["handoff_markdown_path"]).is_file()
    assert result["blueprint"]["blocks"][0]["kind"] == "heading"
    assert result["tool_sequence"][0]["tool"] == "new_document"

@pytest.mark.asyncio
async def test_seed_docx_writes_docx(project_with_drafts, monkeypatch):
    monkeypatch.setattr(server, "render_docx", fake_docx_writer)
    result = await server.prepare_wordcraft_handoff(project_with_drafts.id, mode="seed_docx")
    assert result["seed_docx_path"].endswith("manuscript.docx")
    assert Path(result["seed_docx_path"]).is_file()

@pytest.mark.asyncio
async def test_batch_script_writes_job_json(project_with_drafts, monkeypatch):
    monkeypatch.setattr(server, "render_docx", fake_docx_writer)
    result = await server.prepare_wordcraft_handoff(project_with_drafts.id, mode="batch_script")
    job = json.loads(Path(result["script_path"]).read_text())
    assert job["save"][0].endswith(".docx") and job["save"][1].endswith(".pdf")
    assert result["run_command"][:3] == ["wordcraft-cli", "run", "--script"]

@pytest.mark.asyncio
async def test_unknown_mode_rejected(project_with_drafts):
    with pytest.raises(ToolError, match="mode"):
        await server.prepare_wordcraft_handoff(project_with_drafts.id, mode="magic")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_wordcraft_handoff_tool.py -v` → FAIL.

- [ ] **Step 3: Implement**

1. Refactor `export_to_docx`: move its body into `_render_docx_export` and keep the same return shape (existing tests must stay green).
2. Shared preamble as in spec §3; write the handoff Markdown to `exports/<stem>-handoff.md` via `_safe_destination`.
3. `live_authoring`: `render_markdown` → read back → `parse_markdown_blocks` → `build_blueprint`; inject `bibliography` (title + rendered reference section extracted from the end of the rendered Markdown: the text after the last heading whose text is `References`, else empty) and append its `type_text`/`caret.docEnd` steps before `file.strictReport`.
4. `seed_docx` / `batch_script`: `_render_docx_export` into `exports/<stem>.docx`; `batch_script` additionally writes `scripts/job.json` and returns `run_command` using the detected CLI path when present.
5. Return: common keys `project_id`, `mode`, `csl_style`, `cited_but_missing`, `warnings`, plus mode-specific keys (`handoff_markdown_path`/`blueprint`/`assets`, `seed_docx_path`, `script_path`/`run_command`). Unknown mode/CSL style → `ToolError` listing valid values.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_wordcraft_handoff_tool.py tests/unit/test_export_tools.py -v` → PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: hand a verified draft to wordcraft in three modes"
```

---

### Task 6: Workflow prompts

**Files:**
- Modify: `src/research_mcp/server.py`
- Test: `tests/unit/test_wordcraft_prompts.py`

**Interfaces:**
- Produces: `@mcp.prompt()` `write_in_wordcraft(project_id: str, csl_style: str = "apa", target_filename: str = "manuscript") -> str` and `peer_review_in_wordcraft(project_id: str, docx_path: str) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_wordcraft_prompts.py
import pytest
from mcp.server.mcpserver import MCPServer

from research_mcp import server

@pytest.mark.asyncio
async def test_prompts_registered():
    names = [p.name for p in await server.mcp.list_prompts()]
    assert "write_in_wordcraft" in names and "peer_review_in_wordcraft" in names

@pytest.mark.asyncio
async def test_write_prompt_mentions_safety_rules():
    text = server.write_in_wordcraft("p1")
    for needle in ["check_wordcraft_env", "prepare_wordcraft_handoff", "file.strictReport",
                   "caret.docEnd", "outcome unknown", "inspect_document", "--connect"]:
        assert needle in text

@pytest.mark.asyncio
async def test_peer_review_prompt_mentions_track_changes():
    text = server.peer_review_in_wordcraft("p1", "/tmp/doc.docx")
    assert 'review.trackChanges {"value": true}' in text
    assert "search_local_library" in text
```

- [ ] **Step 2: Run tests to verify they fail** → FAIL.

- [ ] **Step 3: Implement both prompts** returning one string message each, following spec §4's step list verbatim and naming the real tool ids.

- [ ] **Step 4: Run tests to verify they pass**; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add wordcraft writing and peer-review prompts"
```

---

### Task 7: End-to-end verification

**Files:**
- Create: `tests/integration/test_wordcraft_e2e.py`
- Modify: `docs/superpowers/specs/2026-11-21-phase-5-wordcraft-bridge-design.md` (verification notes only, if reality differs)

**Interfaces:**
- Consumes: everything from Tasks 1–6.

- [ ] **Step 1: Write the integration test**

`skipif(shutil.which("wordcraft-cli") is None or shutil.which("pandoc") is None)`. Build a project fixture (one paper + one draft chapter with a citation and a generated chart), call `prepare_wordcraft_handoff(mode="batch_script")`, then `subprocess.run(result["run_command"], timeout=120)`. Assert exit 0, `.docx` and `.pdf` exist and are non-empty, and `wordcraft-cli convert out.docx out.pdf --strict` exits 0 (strict glyph check).

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/integration/test_wordcraft_e2e.py -v` → PASS when the CLI is installed, SKIP otherwise.

- [ ] **Step 3: Full suite + lint**

Run: `RESEARCH_MCP_SKIP_LIVE_TESTS=1 uv run pytest -q` → all pass; `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: verify wordcraft handoff end to end"
```
