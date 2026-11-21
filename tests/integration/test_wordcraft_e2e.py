"""End-to-end WordCraft handoff test.

Requires:
- `wordcraft-cli` on PATH or in the ArtCraft Launcher install location
- `pandoc` on PATH

Both tests skip when either binary is absent.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import create_project, save_paper_and_sections
from research_mcp.exporters.wordcraft_bridge import detect_wordcraft_cli
from research_mcp.models import PaperModel
from research_mcp.server import prepare_wordcraft_handoff, save_draft_section

SKIP_NO_WORDCRAFT = pytest.mark.skipif(
    detect_wordcraft_cli() is None,
    reason="wordcraft-cli not installed",
)
SKIP_NO_PANDOC = pytest.mark.skipif(
    shutil.which("pandoc") is None,
    reason="pandoc not installed",
)


@pytest_asyncio.fixture
async def e2e_project(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspaces"
    db_path = tmp_path / "research_mcp.db"
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(workspace_root))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(db_path))

    settings = get_settings()
    conn = await get_connection(settings.db_path)
    project = await create_project(conn, settings.workspace_dir, "E2E WordCraft")
    paper = PaperModel(
        id="e2e-p1",
        project_id=project.id,
        citekey="smith2023neural",
        doi="10.1/e2e",
        title="Neural Advances",
        authors=["Alice Smith"],
        authors_structured=[{"given": "Alice", "family": "Smith", "literal": None}],
        year=2023,
        venue="Science",
        item_type="journal-article",
        created_at="2023-01-01T00:00:00Z",
    )
    await save_paper_and_sections(conn, project.id, paper, sections=None)
    await conn.close()

    await save_draft_section(
        project.id,
        "01_intro.md",
        "# Introduction\n\nNeural networks have advanced significantly [@smith2023neural].\n",
    )
    yield project, settings.workspace_dir / project.slug


@SKIP_NO_PANDOC
@pytest.mark.asyncio
async def test_seed_docx_produces_readable_file(e2e_project):
    project, project_dir = e2e_project
    result = await prepare_wordcraft_handoff(project.id, mode="seed_docx")
    seed = Path(result["seed_docx_path"])
    assert seed.is_file()
    assert seed.stat().st_size > 100


@SKIP_NO_PANDOC
@SKIP_NO_WORDCRAFT
@pytest.mark.asyncio
async def test_batch_script_runs_and_produces_outputs(e2e_project):
    project, project_dir = e2e_project
    result = await prepare_wordcraft_handoff(project.id, mode="batch_script")
    run_command = result["run_command"]
    proc = subprocess.run(run_command, capture_output=True, timeout=120)
    assert proc.returncode == 0, f"wordcraft-cli exited {proc.returncode}: {proc.stderr.decode()}"

    exports_dir = project_dir / "exports"
    docx = exports_dir / "manuscript.docx"
    pdf = exports_dir / "manuscript.pdf"
    assert docx.is_file() and docx.stat().st_size > 100
    assert pdf.is_file() and pdf.stat().st_size > 100


@SKIP_NO_PANDOC
@pytest.mark.asyncio
async def test_live_authoring_blueprint_file_contains_citeproc_output(e2e_project):
    project, project_dir = e2e_project
    result = await prepare_wordcraft_handoff(project.id, mode="live_authoring")
    md_path = Path(result["handoff_markdown_path"])
    assert md_path.is_file()
    text = md_path.read_text(encoding="utf-8")
    assert "Smith" in text
