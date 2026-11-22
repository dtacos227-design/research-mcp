"""Style discovery tools, the `research://styles` resource and style-aware handoff."""

import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp import server
from research_mcp.config import get_settings
from research_mcp.db import repository
from research_mcp.db.database import get_connection
from research_mcp.db.repository import save_paper_and_sections
from research_mcp.models import PaperModel, ResearchBrief


def _paper(project_id: str) -> PaperModel:
    return PaperModel(
        id="p-style",
        project_id=project_id,
        citekey="doe2024style",
        doi="10.1/style",
        title="Style Paper",
        authors=["Jane Doe"],
        authors_structured=[{"given": "Jane", "family": "Doe", "literal": None}],
        year=2024,
        venue="Nature",
        item_type="journal-article",
        created_at="2024-01-01T00:00:00Z",
    )


async def _bootstrap(
    tmp_path,
    monkeypatch,
    *,
    style_id: str = "article_imrad",
    with_brief: bool = True,
    with_paper: bool = True,
):
    """Create an isolated workspace project, optionally briefing and papering it."""
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        project = await repository.create_project(conn, settings.workspace_dir, "Style Project")
        if with_brief:
            brief = ResearchBrief(
                style_id=style_id,
                title="T",
                research_question="Q",
                citation="apa",
            )
            await repository.set_project_brief(conn, project.id, brief.model_dump_json(), style_id)
        if with_paper:
            await save_paper_and_sections(conn, project.id, _paper(project.id), sections=None)
    finally:
        await conn.close()
    return project, settings.workspace_dir / project.slug


def _numbering_warnings(warnings: list[str]) -> list[str]:
    return [warning for warning in warnings if "numbering" in warning.lower()]


def _fake_markdown_renderer(recorded):
    def fake(markdown, bib_path, csl_path, output, resource_path=None):
        recorded.append(markdown)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
        return output

    return fake


def _fake_docx_renderer(recorded):
    def fake(markdown, bib_path, csl_path, reference_doc, output, resource_path=None, toc=False):
        recorded.append(markdown)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"docx")
        return output

    return fake


def _skip_docx_side_effects(monkeypatch):
    monkeypatch.setattr(server, "apply_docx_formatting", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "polish_docx", lambda *args, **kwargs: None)


@pytest.mark.asyncio
async def test_list_and_get_style_tools():
    listing = await server.list_research_styles()
    assert {s["id"] for s in listing["styles"]} >= {"skripsi_s1", "article_imrad"}
    detail = await server.get_research_style("skripsi_s1")
    assert detail["style"]["formatting"]["line_spacing"] == 2.0


@pytest.mark.asyncio
async def test_get_unknown_style_tool_raises():
    with pytest.raises(ToolError, match="Unknown research style"):
        await server.get_research_style("no_such_style")


@pytest.mark.asyncio
async def test_styles_resource_registered_and_returns_json():
    uris = [resource.uri for resource in await server.mcp.list_resources()]
    assert "research://styles" in uris

    data = json.loads(await server.resource_styles())

    assert isinstance(data, list)
    assert {style["id"] for style in data} >= {"skripsi_s1", "article_imrad"}
    assert all("label" in style and "sections" in style for style in data)


@pytest.mark.asyncio
async def test_handoff_blocked_without_brief(tmp_path, monkeypatch):
    project, project_dir = await _bootstrap(
        tmp_path, monkeypatch, with_brief=False, with_paper=False
    )

    result = await server.prepare_wordcraft_handoff(project.id, mode="live_authoring")

    assert result.get("status") == "precondition_failed"
    assert result["style_id"]
    assert result["failed"]
    assert result["remediation"]
    assert not any((project_dir / "exports").iterdir())


@pytest.mark.asyncio
async def test_live_handoff_uses_the_style_outline_without_false_numbering_warning(
    tmp_path, monkeypatch
):
    project, project_dir = await _bootstrap(tmp_path, monkeypatch, style_id="article_imrad")
    await server.save_draft_section(
        project.id, "01_introduction.md", "# Introduction\n\nBody [@doe2024style]."
    )
    await server.save_draft_section(project.id, "99_extra.md", "# Extra\n\nMore.")
    recorded: list[str] = []
    monkeypatch.setattr(server, "render_markdown", _fake_markdown_renderer(recorded))

    result = await server.prepare_wordcraft_handoff(project.id)

    assert result["style_id"] == "article_imrad"
    assert result["csl_style"] == "apa"
    assert result["mode"] == "live_authoring"
    assert recorded[0].index("# Introduction") < recorded[0].index("# Extra")
    assert any("99_extra.md" in warning for warning in result["warnings"])
    # article_imrad keeps draft headings verbatim, so it must not warn about numbering.
    assert _numbering_warnings(result["warnings"]) == []
    commands = [step["arguments"].get("command") for step in result["tool_sequence"]]
    assert "layout.margins" in commands
    assert Path(result["handoff_markdown_path"]).is_relative_to(project_dir)


@pytest.mark.asyncio
async def test_seed_handoff_uses_the_style_and_warns_once(tmp_path, monkeypatch):
    project, _project_dir = await _bootstrap(tmp_path, monkeypatch, style_id="skripsi_s1")
    await server.save_draft_section(
        project.id, "10_bab1_pendahuluan.md", "# Pendahuluan\n\nIsi bab satu."
    )
    recorded: list[str] = []
    monkeypatch.setattr(server, "render_docx", _fake_docx_renderer(recorded))
    _skip_docx_side_effects(monkeypatch)

    result = await server.prepare_wordcraft_handoff(project.id, mode="seed_docx")

    assert result["style_id"] == "skripsi_s1"
    assert "# BAB I Pendahuluan" in recorded[0]
    assert len(_numbering_warnings(result["warnings"])) == 1
    commands = [step["arguments"].get("command") for step in result["tool_sequence"]]
    assert "layout.margins" in commands


@pytest.mark.asyncio
async def test_batch_handoff_uses_the_effective_csl_and_warns_once(tmp_path, monkeypatch):
    project, project_dir = await _bootstrap(tmp_path, monkeypatch, style_id="skripsi_s1")
    await server.save_draft_section(
        project.id, "10_bab1_pendahuluan.md", "# Pendahuluan\n\nIsi bab satu."
    )
    monkeypatch.setattr(server, "render_docx", _fake_docx_renderer([]))
    _skip_docx_side_effects(monkeypatch)
    monkeypatch.setattr(server.wordcraft_bridge, "detect_wordcraft_cli", lambda *a, **k: None)

    result = await server.prepare_wordcraft_handoff(
        project.id, csl_style="ieee", mode="batch_script"
    )

    assert result["style_id"] == "skripsi_s1"
    assert result["csl_style"] == "ieee"
    job = json.loads((project_dir / "scripts" / "job.json").read_text(encoding="utf-8"))
    assert job["commands"][1]["params"]["style"] == "IEEE"
    assert len(_numbering_warnings(result["warnings"])) == 1
