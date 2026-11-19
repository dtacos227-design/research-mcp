# Phase 0 — Project Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `research-mcp` package skeleton (uv project, config, Pydantic models, and a bare MCP stdio server) so that `uv run research-mcp` completes a real MCP client handshake with nothing but valid JSON-RPC on stdout.

**Architecture:** A single installable `uv` package (`src/research_mcp/`) exposing a `research-mcp` console script. `config.py` owns environment-driven settings and stderr-only logging; `models.py` holds the Pydantic v2 schemas the rest of the system will reuse; `server.py` constructs the `MCPServer` instance and the `main()` entry point. No tools/resources/prompts are registered yet — that starts in Phase 1.

**Tech Stack:** Python 3.11+, `uv` (build backend `uv_build`), `mcp>=2.3.0` (`from mcp.server.mcpserver import MCPServer` — the SDK renamed `FastMCP` to `MCPServer` in the 2.x line that `uv add mcp` resolves today), `pydantic>=2.9`, `pydantic-settings>=2.6`, `pytest` + `pytest-asyncio` + `anyio` for the stdio integration test, `ruff`.

**Spec:** `AGENTS.md` (repo root) — specifically §2.1 (tech stack), §2.4 (directory structure), §4.1 (CLI-safety rules), §7 Fase 0 (scope + verification criteria).

## Global Constraints

- Python 3.11+, packaged with `uv` (`AGENTS.md` §2.1, §7).
- Console entry point exactly `[project.scripts] research-mcp = "research_mcp.server:main"` (`AGENTS.md` §2.1, §4.1.3).
- Never `print()` to stdout; stdout is reserved for MCP JSON-RPC frames. All logs go through `logging` with a handler bound to `sys.stderr` (`AGENTS.md` §4.1.1).
- Never depend on CWD for paths; workspace root defaults to `~/.research-mcp/workspaces`, overridable via env var `RESEARCH_MCP_WORKSPACE` (`AGENTS.md` §4.1.2).
- All public functions and MCP tool parameters use Python 3.11+ type hints and Pydantic v2 models (`AGENTS.md` §5.1.1).
- `RESEARCH_MCP_EMAIL` is the env var later used in the `User-Agent: ResearchMCP/0.1 (mailto:<email>)` header for OpenAlex/Crossref/Unpaywall (`AGENTS.md` §5.1.3) — Phase 0 only needs to expose it as an optional setting; no HTTP calls happen yet.
- Every phase must pass `ruff check .`, `ruff format --check .`, and `pytest` before moving to the next phase (`AGENTS.md` §5.3).
- Phase 0 acceptance: `uv run research-mcp` runs cleanly with zero non-JSON-RPC bytes on stdout, detected normally by an MCP client (`AGENTS.md` §7 Fase 0).

## Review Focus

- `RESEARCH_MCP_WORKSPACE` set to a relative path — the spec forbids CWD-dependent paths, so `Settings` must resolve it to an absolute path instead of keeping it relative. Covered in Task 2.
- `RESEARCH_MCP_EMAIL` unset (normal before Phase 1 wires providers) — config must not raise; `email` stays `None`. Covered in Task 2.
- `configure_logging()` called more than once (module re-import, test runner re-entry) must never duplicate stderr handlers and must never attach a stdout handler. Covered in Task 2.
- A `HandoffPayload`/`ExportResult` built with a `mode`/`format` value outside its `Literal` (e.g. a typo'd workflow key) must raise `ValidationError` rather than silently flowing into the WordCraft bridge built in Phase 5. Covered in Task 3.
- Running the real entry point through an actual MCP client handshake must yield a valid `initialize` response with nothing else on stdout — the single acceptance criterion Phase 0 exists for, and the one no unit test alone proves. Covered in Task 4.

---

### Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/research_mcp/__init__.py`
- Create: `.env.example`
- Modify: `.gitignore`

**Interfaces:**
- Produces: an importable `research_mcp` package and a `research-mcp` console script name (wired to `research_mcp.server:main`, implemented in Task 4).

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "research-mcp"
version = "0.1.0"
description = "Research MCP: 100% free end-to-end scientific research MCP server"
requires-python = ">=3.11"
dependencies = [
    "mcp>=2.3.0",
    "httpx>=0.28",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "tenacity>=9.0",
    "aiosqlite>=0.20",
]

[project.scripts]
research-mcp = "research_mcp.server:main"

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "anyio>=4.6",
    "respx>=0.21",
    "ruff>=0.7",
]

[build-system]
requires = ["uv_build>=0.12.9,<0.13.0"]
build-backend = "uv_build"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create `src/research_mcp/__init__.py`** — empty file, just a package marker.

- [ ] **Step 3: Create `.env.example`**

```
RESEARCH_MCP_EMAIL=you@example.com
RESEARCH_MCP_WORKSPACE=
RESEARCH_MCP_LOG_LEVEL=INFO
```

- [ ] **Step 4: Append Python-specific entries to `.gitignore`**

```
__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/
*.egg-info/
dist/
build/
.env
```

- [ ] **Step 5: Verify the project installs and the package imports**

Run: `uv sync && uv run python -c "import research_mcp; print('ok')"`
Expected: dependency resolution succeeds, prints `ok`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/research_mcp/__init__.py .env.example .gitignore uv.lock
git commit -m "chore: scaffold research-mcp uv project"
```

---

### Task 2: `config.py` — Settings & stderr-only logging

**Files:**
- Create: `src/research_mcp/config.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: nothing (first real module).
- Produces: `Settings` (a `pydantic_settings.BaseSettings` subclass with fields `workspace_dir: Path`, `email: str | None`, `log_level: str`), `get_settings() -> Settings`, `configure_logging(level: str = "INFO") -> None`. Task 4 consumes `get_settings` and `configure_logging` exactly as named here.

- [ ] **Step 1: Write the failing tests**

```python
import logging
import sys
from pathlib import Path

import pytest

from research_mcp.config import Settings, configure_logging, get_settings


def test_default_workspace_dir_is_home_research_mcp_workspaces(monkeypatch):
    monkeypatch.delenv("RESEARCH_MCP_WORKSPACE", raising=False)
    settings = Settings()
    assert settings.workspace_dir == Path.home() / ".research-mcp" / "workspaces"


def test_workspace_dir_override_from_env_is_resolved_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", "relative/workspace")
    settings = Settings()
    assert settings.workspace_dir.is_absolute()
    assert settings.workspace_dir == (tmp_path / "relative/workspace").resolve()


def test_email_defaults_to_none_when_unset(monkeypatch):
    monkeypatch.delenv("RESEARCH_MCP_EMAIL", raising=False)
    settings = Settings()
    assert settings.email is None


def test_email_read_from_env(monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_EMAIL", "rezalkaryanto@gmail.com")
    settings = Settings()
    assert settings.email == "rezalkaryanto@gmail.com"


def test_log_level_defaults_to_info(monkeypatch):
    monkeypatch.delenv("RESEARCH_MCP_LOG_LEVEL", raising=False)
    settings = Settings()
    assert settings.log_level == "INFO"


def test_get_settings_returns_settings_instance():
    assert isinstance(get_settings(), Settings)


def test_configure_logging_attaches_only_stderr_handlers():
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    configure_logging("DEBUG")
    assert len(root.handlers) >= 1
    for handler in root.handlers:
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is sys.stderr


def test_configure_logging_is_idempotent():
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    configure_logging("INFO")
    first_count = len(root.handlers)
    configure_logging("INFO")
    assert len(root.handlers) == first_count
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_mcp.config'`

- [ ] **Step 3: Implement `config.py`**

`Settings` fields each use `pydantic.Field(validation_alias=...)` to bind to the exact env var names from the spec (`RESEARCH_MCP_WORKSPACE`, `RESEARCH_MCP_EMAIL`, `RESEARCH_MCP_LOG_LEVEL`), since the field names themselves (`workspace_dir`, `email`, `log_level`) don't match those env var names and a plain `env_prefix` would require `RESEARCH_MCP_WORKSPACE_DIR` instead. Give `workspace_dir` a `field_validator(mode="after")` that returns `Path(v).expanduser().resolve()`, and set `model_config = SettingsConfigDict(validate_default=True)` so the default also gets resolved. `get_settings()` just returns `Settings()` — no caching needed yet. `configure_logging(level)` checks whether `logging.getLogger()` already has a `StreamHandler` bound to `sys.stderr`; if not, it adds one (formatted) and sets the root level; if one is already present it only updates the level, so repeated calls never duplicate handlers.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/config.py tests/unit/test_config.py
git commit -m "feat: add Settings and stderr-only logging config"
```

---

### Task 3: `models.py` — Pydantic v2 schemas

**Files:**
- Create: `src/research_mcp/models.py`
- Test: `tests/unit/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ProjectModel`, `PaperModel`, `PaperSectionModel`, `DraftSectionModel`, `HandoffPayload` (field `mode: Literal["live_authoring", "seed_docx", "batch_script"]`), `ExportResult` (field `format: Literal["docx", "latex", "pdf", "markdown"]`). Field names mirror the SQLite columns in `AGENTS.md` §2.3 exactly (`PaperModel.authors` is the in-memory `list[str]` form of the stored `authors_json` column — the DB layer in Phase 1 does the JSON (de)serialization, not this model).

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from pydantic import ValidationError

from research_mcp.models import (
    DraftSectionModel,
    ExportResult,
    HandoffPayload,
    PaperModel,
    PaperSectionModel,
    ProjectModel,
)


def test_project_model_accepts_valid_data():
    project = ProjectModel(
        id="p1", slug="my-project", name="My Project",
        created_at="2026-11-20T00:00:00Z", updated_at="2026-11-20T00:00:00Z",
    )
    assert project.description == ""


def test_project_model_requires_name():
    with pytest.raises(ValidationError):
        ProjectModel(id="p1", slug="my-project", created_at="x", updated_at="x")


def test_paper_model_accepts_valid_data():
    paper = PaperModel(
        id="pa1", project_id="p1", citekey="vaswani2017attention",
        title="Attention Is All You Need", authors=["Ashish Vaswani"],
        created_at="2026-11-20T00:00:00Z",
    )
    assert paper.citation_count == 0
    assert paper.doi is None


def test_paper_section_model_accepts_valid_data():
    section = PaperSectionModel(paper_id="pa1", section_order=1, heading="Abstract", content="...")
    assert section.word_count == 0


def test_draft_section_model_accepts_valid_data():
    draft = DraftSectionModel(
        project_id="p1", filename="01_abstract.md", section_order=1,
        updated_at="2026-11-20T00:00:00Z",
    )
    assert draft.title is None


def test_handoff_payload_rejects_invalid_mode():
    with pytest.raises(ValidationError):
        HandoffPayload(project_id="p1", mode="live_authoring_typo")


def test_handoff_payload_accepts_valid_mode():
    payload = HandoffPayload(project_id="p1", mode="seed_docx")
    assert payload.csl_style == "apa"


def test_export_result_rejects_invalid_format():
    with pytest.raises(ValidationError):
        ExportResult(success=True, format="docx-typo")


def test_export_result_accepts_valid_format():
    result = ExportResult(success=True, format="docx", output_path="/tmp/out.docx")
    assert result.warnings == []
    assert result.errors == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_mcp.models'`

- [ ] **Step 3: Implement `models.py`**

Plain `pydantic.BaseModel` subclasses with the fields the tests exercise, matching `AGENTS.md` §2.3's column list per table (`ProjectModel`, `PaperModel`, `PaperSectionModel`, `DraftSectionModel`), plus `HandoffPayload` and `ExportResult` per §2.4's module description and the tool signatures in §6.1 items 18–21. `warnings`/`errors` on `ExportResult` use `Field(default_factory=list)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/models.py tests/unit/test_models.py
git commit -m "feat: add Pydantic v2 schemas for projects, papers, drafts, and exports"
```

---

### Task 4: `server.py` — MCP stdio entry point

**Files:**
- Create: `src/research_mcp/server.py`
- Test: `tests/integration/test_server_stdio.py`

**Interfaces:**
- Consumes: `research_mcp.config.get_settings() -> Settings`, `research_mcp.config.configure_logging(level: str) -> None` (Task 2).
- Produces: module-level `mcp` (an `MCPServer` instance named `"research-mcp"`, with no tools/resources/prompts registered — Phase 1+ adds those), and `main() -> None`, the function `pyproject.toml`'s `research-mcp` console script points at.

- [ ] **Step 1: Write the failing integration test**

```python
import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_research_mcp_server_completes_initialize_handshake_over_stdio():
    params = StdioServerParameters(command=sys.executable, args=["-m", "research_mcp.server"])
    with anyio.fail_after(15):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                result = await session.initialize()
                assert result.server_info.name == "research-mcp"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/integration/test_server_stdio.py -v`
Expected: FAIL — the subprocess has no `research_mcp.server` module to run, so the handshake never completes (`anyio.fail_after` timeout or a client-side exception).

- [ ] **Step 3: Implement `server.py`**

```python
from mcp.server.mcpserver import MCPServer

from research_mcp.config import configure_logging, get_settings

mcp = MCPServer("research-mcp")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/integration/test_server_stdio.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/server.py tests/integration/test_server_stdio.py
git commit -m "feat: add MCPServer stdio entry point"
```

---

### Task 5: Phase 0 Closeout

**Files:** none created; this task only runs verification commands from `AGENTS.md` §5.3 and §7 Fase 0.

- [ ] **Step 1: Lint**

Run: `uv run ruff check .`
Expected: no errors.

- [ ] **Step 2: Format check**

Run: `uv run ruff format --check .`
Expected: no diff needed.

- [ ] **Step 3: Full test suite**

Run: `uv run pytest -v`
Expected: all tests pass (Tasks 2–4's tests, 18 total).

- [ ] **Step 4: Manual smoke test of the real console script**

Run: `uv run research-mcp` in one terminal, confirm it blocks silently on stdin (no stdout output, no traceback) — exactly what `AGENTS.md` §7 Fase 0 asks for — then interrupt it (Ctrl-C). This is the same behavior the automated test in Task 4 already proves over a real handshake; this step is a human-visible sanity check, not a new mechanism.

- [ ] **Step 5: Commit** (only if Steps 1–3 required fixes; otherwise this task produces no diff)
