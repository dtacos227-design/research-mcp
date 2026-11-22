import json
from pathlib import Path

import pytest
import pytest_asyncio
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import create_project, save_paper_and_sections
from research_mcp.models import PaperModel
from research_mcp.server import prepare_wordcraft_handoff, save_draft_section


async def _make_project(*, bad_citation: bool, chart: bool = False):
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    project = await create_project(conn, settings.workspace_dir, "Handoff Project")
    paper = PaperModel(
        id="p-1",
        project_id=project.id,
        citekey="doe2024valid",
        doi="10.1/valid",
        title="Valid Paper",
        authors=["Jane Doe"],
        authors_structured=[{"given": "Jane", "family": "Doe", "literal": None}],
        year=2024,
        venue="Nature",
        item_type="journal-article",
        created_at="2024-01-01T00:00:00Z",
    )
    await save_paper_and_sections(conn, project.id, paper, sections=None)
    await conn.close()

    project_dir = settings.workspace_dir / project.slug
    if chart:
        (project_dir / "assets" / "chart.png").write_bytes(b"png")

    await save_draft_section(project.id, "01_abstract.md", "# Abstract\n\nAbs body.")
    cite = "ghost2099" if bad_citation else "doe2024valid"
    await save_draft_section(
        project.id,
        "02_intro.md",
        f"# Introduction\n\nIntro body [@{cite}].\n\n| Month | Cases |\n|---|---|\n| Jan | 12 |\n",
    )
    return project, project_dir


@pytest_asyncio.fixture
async def project_with_drafts(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    return await _make_project(bad_citation=False, chart=True)


@pytest_asyncio.fixture
async def project_with_bad_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    return await _make_project(bad_citation=True)


def _fake_markdown_renderer(recorded, payload=None):
    def fake(markdown, bib_path, csl_path, output, resource_path=None):
        recorded.append(
            {
                "markdown": markdown,
                "bib": bib_path,
                "csl": csl_path,
                "output": output,
            }
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        rendered = payload if payload is not None else markdown
        output.write_text(rendered, encoding="utf-8")
        return output

    return fake


def _skip_polish(monkeypatch):
    from research_mcp import server

    monkeypatch.setattr(server, "polish_docx", lambda *args, **kwargs: None)


def _skip_docx_formatting(monkeypatch):
    from research_mcp import server

    monkeypatch.setattr(server, "apply_docx_formatting", lambda *args, **kwargs: None)


def _fake_docx_renderer(recorded, payload=b"docx"):
    def fake(markdown, bib_path, csl_path, reference_doc, output, resource_path=None):
        recorded.append({"markdown": markdown, "output": output})
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payload)
        return output

    return fake


@pytest.mark.asyncio
async def test_handoff_refuses_unknown_citekey(project_with_bad_draft, monkeypatch):
    from research_mcp import server

    project, _project_dir = project_with_bad_draft
    monkeypatch.setattr(server, "render_markdown", _fake_markdown_renderer([]), raising=False)
    with pytest.raises(ToolError, match="ghost2099"):
        await prepare_wordcraft_handoff(project.id)


@pytest.mark.asyncio
async def test_handoff_allows_unknown_citekey_when_not_strict(project_with_bad_draft, monkeypatch):
    from research_mcp import server

    project, _project_dir = project_with_bad_draft
    monkeypatch.setattr(server, "render_markdown", _fake_markdown_renderer([]))
    result = await prepare_wordcraft_handoff(
        project.id, strict_citations=False, mode="live_authoring"
    )
    assert result["cited_but_missing"] == ["ghost2099"]


@pytest.mark.asyncio
async def test_live_authoring_returns_blueprint(project_with_drafts, monkeypatch):
    from research_mcp import server

    project, project_dir = project_with_drafts
    rendered = (
        "# Abstract\n\nAbs body.\n\n"
        "# Introduction\n\nIntro body (Doe, 2024).\n\n"
        "| Month | Cases |\n|---|---|\n| Jan | 12 |\n\n"
        "![Chart](assets/chart.png)\n\n"
        "# References\n\nDoe, J. (2024). Valid Paper. *Nature*.\n"
    )
    monkeypatch.setattr(server, "render_markdown", _fake_markdown_renderer([], rendered))

    result = await prepare_wordcraft_handoff(project.id)

    assert result["mode"] == "live_authoring"
    assert result["csl_style"] == "apa"
    assert result["cited_but_missing"] == []
    markdown_path = Path(result["handoff_markdown_path"])
    assert markdown_path.is_file()
    assert markdown_path.is_relative_to(project_dir)
    blueprint = result["blueprint"]
    assert blueprint["blocks"][0]["kind"] == "heading"
    assert [block["kind"] for block in blueprint["blocks"]] == [
        "heading",
        "paragraph",
        "heading",
        "paragraph",
        "table",
        "figure",
    ]
    assert blueprint["assets"] == [str(project_dir / "assets" / "chart.png")]
    assert blueprint["bibliography"]["title"] == "References"
    assert "Doe, J. (2024)" in blueprint["bibliography"]["markdown"]
    sequence = result["tool_sequence"]
    assert sequence[0]["tool"] == "new_document"
    commands = [step["arguments"].get("command") for step in sequence]
    assert "file.strictReport" in commands
    assert commands[-1] == "file.exportPdf"
    saved = [step["arguments"]["path"] for step in sequence if step["tool"] == "save_document"]
    assert saved == [str(project_dir / "exports" / "manuscript.docx")]


@pytest.mark.asyncio
async def test_live_authoring_keeps_figures_of_missing_assets(project_with_drafts, monkeypatch):
    from research_mcp import server

    project, _project_dir = project_with_drafts
    rendered = "# Intro\n\n![Gone](assets/gone.png)\n"
    monkeypatch.setattr(server, "render_markdown", _fake_markdown_renderer([], rendered))

    result = await prepare_wordcraft_handoff(project.id)

    assert result["blueprint"]["blocks"][1]["exists"] is False
    assert any("gone.png" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_blueprint_injects_bibliography_steps(project_with_drafts, monkeypatch):
    from research_mcp import server

    project, _project_dir = project_with_drafts
    rendered = "# Intro\n\nBody.\n\n# References\n\nDoe, J. (2024). Valid Paper.\n"
    monkeypatch.setattr(server, "render_markdown", _fake_markdown_renderer([], rendered))

    result = await prepare_wordcraft_handoff(project.id)
    sequence = result["tool_sequence"]
    texts = [step["arguments"].get("text") for step in sequence]
    commands = [step["arguments"].get("command") for step in sequence]

    assert "References" in texts
    heading_at = texts.index("References")
    typed = sequence[heading_at + 2]["arguments"]
    assert typed["text"] == "Doe, J. (2024). Valid Paper."
    assert typed["paragraphs"] is True
    caret_at = commands.index("caret.docEnd")
    assert caret_at > heading_at
    assert commands.index("file.strictReport") > caret_at


@pytest.mark.asyncio
async def test_seed_docx_writes_docx(project_with_drafts, monkeypatch):
    from research_mcp import server

    project, project_dir = project_with_drafts
    recorded = []
    monkeypatch.setattr(server, "render_docx", _fake_docx_renderer(recorded))
    _skip_polish(monkeypatch)
    _skip_docx_formatting(monkeypatch)

    result = await prepare_wordcraft_handoff(project.id, mode="seed_docx")

    seed = Path(result["seed_docx_path"])
    assert seed == project_dir / "exports" / "manuscript.docx"
    assert seed.is_file()
    assert result["blueprint"] is None
    assert result["tool_sequence"][0]["tool"] == "open_document"
    assert result["tool_sequence"][0]["arguments"]["path"] == str(seed)


@pytest.mark.asyncio
async def test_batch_script_writes_job_json(project_with_drafts, monkeypatch):
    from research_mcp import server

    project, project_dir = project_with_drafts
    monkeypatch.setattr(server, "render_docx", _fake_docx_renderer([]))
    _skip_polish(monkeypatch)
    _skip_docx_formatting(monkeypatch)
    monkeypatch.setattr(server.wordcraft_bridge, "detect_wordcraft_cli", lambda *a, **k: None)

    result = await prepare_wordcraft_handoff(project.id, mode="batch_script")

    script_path = Path(result["script_path"])
    assert script_path == project_dir / "scripts" / "job.json"
    job = json.loads(script_path.read_text(encoding="utf-8"))
    assert job["file"] == result["seed_docx_path"]
    assert job["save"] == [
        str(project_dir / "exports" / "manuscript.docx"),
        str(project_dir / "exports" / "manuscript.pdf"),
    ]
    assert result["run_command"] == ["wordcraft-cli", "run", "--script", str(script_path)]


@pytest.mark.asyncio
async def test_batch_script_uses_detected_cli(project_with_drafts, monkeypatch):
    from research_mcp import server

    project, _project_dir = project_with_drafts
    monkeypatch.setattr(server, "render_docx", _fake_docx_renderer([]))
    _skip_polish(monkeypatch)
    _skip_docx_formatting(monkeypatch)
    monkeypatch.setattr(
        server.wordcraft_bridge, "detect_wordcraft_cli", lambda *a, **k: Path("/x/wordcraft-cli")
    )

    result = await prepare_wordcraft_handoff(project.id, mode="batch_script")

    assert result["run_command"][0] == "/x/wordcraft-cli"


@pytest.mark.asyncio
async def test_unknown_mode_rejected(project_with_drafts, monkeypatch):
    from research_mcp import server

    project, _project_dir = project_with_drafts
    monkeypatch.setattr(server, "render_markdown", _fake_markdown_renderer([]))
    with pytest.raises(ToolError, match="mode"):
        await prepare_wordcraft_handoff(project.id, mode="magic")


@pytest.mark.asyncio
async def test_unknown_csl_style_rejected(project_with_drafts, monkeypatch):
    from research_mcp import server

    project, _project_dir = project_with_drafts
    monkeypatch.setattr(server, "render_markdown", _fake_markdown_renderer([]))
    with pytest.raises(ToolError, match="CSL style"):
        await prepare_wordcraft_handoff(project.id, csl_style="harvard")


@pytest.mark.asyncio
async def test_unknown_project_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    with pytest.raises(ToolError, match="not found"):
        await prepare_wordcraft_handoff("nope")


@pytest.mark.asyncio
async def test_empty_draft_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    project = await create_project(conn, settings.workspace_dir, "Empty")
    await conn.close()
    with pytest.raises(ToolError, match="no draft chapters"):
        await prepare_wordcraft_handoff(project.id)


@pytest.mark.parametrize(
    "bad_name",
    ["../evil", "sub/name", ".", "..", "", "   "],
)
def test_handoff_stem_rejects_traversal(bad_name):
    from research_mcp.server import _handoff_stem

    with pytest.raises(ToolError, match="plain file name"):
        _handoff_stem(bad_name)


@pytest.mark.parametrize(
    ("input_name", "expected"),
    [
        ("manuscript", "manuscript"),
        ("manuscript.docx", "manuscript"),
        ("manuscript.pdf", "manuscript"),
        ("thesis.tex", "thesis"),
        ("report.md", "report"),
    ],
)
def test_handoff_stem_strips_extension(input_name, expected):
    from research_mcp.server import _handoff_stem

    assert _handoff_stem(input_name) == expected
