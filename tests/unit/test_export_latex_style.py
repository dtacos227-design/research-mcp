"""Style-aware LaTeX export: style-driven template, metadata and warnings."""

import pytest

from research_mcp import server
from research_mcp.config import get_settings
from research_mcp.db import repository
from research_mcp.db.database import get_connection
from research_mcp.db.repository import save_paper_and_sections
from research_mcp.models import PaperModel, ResearchBrief
from research_mcp.styles.registry import get_style


def _paper(project_id: str) -> PaperModel:
    return PaperModel(
        id="p-latex",
        project_id=project_id,
        citekey="doe2024latex",
        doi="10.1/latex",
        title="LaTeX Paper",
        authors=["Jane Doe"],
        authors_structured=[{"given": "Jane", "family": "Doe", "literal": None}],
        year=2024,
        venue="Nature",
        item_type="journal-article",
        created_at="2024-01-01T00:00:00Z",
    )


async def _seed_ready_project(tmp_path, monkeypatch, *, style: str = "ieee_paper"):
    """Create a briefed, papered project ready to pass the export gate."""
    workspace_root = tmp_path / "workspaces"
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(workspace_root))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        project = await repository.create_project(conn, settings.workspace_dir, "LaTeX Export")
        brief = ResearchBrief(
            style_id=style,
            title="T",
            research_question="Q",
            citation=get_style(style).citation,
        )
        await repository.set_project_brief(conn, project.id, brief.model_dump_json(), style)
        await save_paper_and_sections(conn, project.id, _paper(project.id), sections=None)
    finally:
        await conn.close()
    return project, workspace_root


def _fake_render_latex(recorded):
    def fake(
        markdown,
        template_path,
        bib_path,
        csl_path,
        output_tex,
        resource_path=None,
        metadata=None,
    ):
        recorded.append(
            {
                "markdown": markdown,
                "template": template_path,
                "csl": csl_path,
                "resource_path": resource_path,
                "metadata": metadata,
            }
        )
        output_tex.parent.mkdir(parents=True, exist_ok=True)
        output_tex.write_text("\\documentclass{article}")
        return output_tex

    return fake


@pytest.mark.asyncio
async def test_latex_export_warns_on_two_column(tmp_path, monkeypatch):
    pid, _workspace_root = await _seed_ready_project(tmp_path, monkeypatch, style="ieee_paper")
    await server.save_draft_section(pid.id, "01_introduction.md", "# Introduction\n\nBody.")
    recorded: list[dict] = []
    monkeypatch.setattr(server.latex_engine, "render_latex", _fake_render_latex(recorded))

    result = await server.export_to_latex(pid.id, "paper", compile_pdf=False)

    assert result["style_id"] == "ieee_paper"
    assert result["format"] == "latex"
    assert any("two" in w.lower() or "column" in w.lower() for w in result.get("warnings", []))


@pytest.mark.asyncio
async def test_latex_export_uses_style_template_csl_and_formatting(tmp_path, monkeypatch):
    """With no explicit template, the style's latex template, CSL and formatting win."""
    pid, workspace_root = await _seed_ready_project(tmp_path, monkeypatch, style="ieee_paper")
    await server.save_draft_section(pid.id, "01_introduction.md", "# Introduction\n\nBody.")
    await server.save_draft_section(pid.id, "99_notes.md", "# Notes\n\nExtra material.")
    recorded: list[dict] = []
    monkeypatch.setattr(server.latex_engine, "render_latex", _fake_render_latex(recorded))

    result = await server.export_to_latex(pid.id, "paper", compile_pdf=False)

    assert result["tex_path"] == str(workspace_root / pid.slug / "exports" / "paper.tex")
    assert result["pdf_path"] is None
    assert result["output_path"] == result["tex_path"]
    assert result["template_name"] == "ieee"
    assert result["csl_style"] == "ieee"
    assert str(recorded[0]["template"]).endswith("ieee.tex")
    assert str(recorded[0]["csl"]).endswith("ieee.csl")
    assert recorded[0]["resource_path"] == workspace_root / pid.slug
    metadata = recorded[0]["metadata"]
    assert metadata["geometry"].startswith("top=1.9cm")
    assert metadata["fontsize"] == "10pt"
    assert "# Introduction" in recorded[0]["markdown"]
    assert "Exported draft(s) not in the style outline: 99_notes.md" in result["warnings"]
