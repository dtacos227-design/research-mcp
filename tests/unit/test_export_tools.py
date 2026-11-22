import pytest
import pytest_asyncio
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import create_project, save_paper_and_sections, set_project_brief
from research_mcp.models import PaperModel, ResearchBrief
from research_mcp.server import (
    export_to_docx,
    export_to_latex,
    export_to_markdown_bundle,
    save_draft_section,
)
from research_mcp.styles.registry import get_style


@pytest_asyncio.fixture
async def project_with_drafts(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspaces"
    db_path = tmp_path / "research_mcp.db"
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(workspace_root))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(db_path))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    project = await create_project(conn, settings.workspace_dir, "Export Project")
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
    brief = ResearchBrief(
        style_id="article_imrad",
        title="Export Project",
        research_question="Does the export merge drafts in order?",
        citation="apa",
    )
    await set_project_brief(conn, project.id, brief.model_dump_json(), "article_imrad")
    await conn.close()

    await save_draft_section(project.id, "01_abstract.md", "# Abstract\n\nAbs body.")
    await save_draft_section(
        project.id, "02_intro.md", "# Introduction\n\nIntro body [@doe2024valid]."
    )
    return project, workspace_root


def _fake_render_engine(recorded, payload=b"rendered"):
    def fake(markdown, *args, **kwargs):
        output = args[-1]
        recorded.append(
            {
                "markdown": markdown,
                "bib": args[0],
                "output": output,
                "resource_path": kwargs.get("resource_path"),
            }
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payload)
        return output

    return fake


@pytest.mark.asyncio
async def test_markdown_bundle_merges_all_chapters(project_with_drafts, monkeypatch):
    project, workspace_root = project_with_drafts
    recorded = []
    from research_mcp import server

    monkeypatch.setattr(server, "render_markdown", _fake_render_engine(recorded))

    result = await export_to_markdown_bundle(project.id, "paper")

    output = workspace_root / project.slug / "exports" / "paper.md"
    assert result["output_path"] == str(output)
    assert result["format"] == "markdown"
    merged = recorded[0]["markdown"]
    assert "Abs body." in merged
    assert "Intro body [@doe2024valid]." in merged
    assert merged.index("Abs body.") < merged.index("Intro body")


@pytest.mark.asyncio
async def test_docx_export_blocks_unknown_citekey(project_with_drafts, monkeypatch):
    project, workspace_root = project_with_drafts
    await save_draft_section(project.id, "03_fake.md", "# Fake\n\nSee [@ghost2020fake].")
    recorded = []
    from research_mcp import server

    monkeypatch.setattr(server, "render_docx", _fake_render_engine(recorded))

    with pytest.raises(ToolError) as excinfo:
        await export_to_docx(project.id, "thesis")

    assert "ghost2020fake" in str(excinfo.value)
    assert recorded == []
    assert not (workspace_root / project.slug / "exports" / "thesis.docx").exists()


@pytest.mark.asyncio
async def test_docx_export_writes_docx_and_refreshes_bibliography(project_with_drafts, monkeypatch):
    project, workspace_root = project_with_drafts
    recorded = []
    polished = []
    formatted = []
    from research_mcp import server

    monkeypatch.setattr(server, "render_docx", _fake_render_engine(recorded))
    monkeypatch.setattr(server, "polish_docx", lambda path, title: polished.append((path, title)))
    monkeypatch.setattr(
        server,
        "apply_docx_formatting",
        lambda path, fmt: formatted.append((path, fmt)),
    )

    result = await export_to_docx(project.id, "thesis")

    output = workspace_root / project.slug / "exports" / "thesis.docx"
    assert output.exists()
    assert result["output_path"] == str(output)
    assert result["format"] == "docx"
    assert result["style_id"] == "article_imrad"
    assert result["csl_style"] == "apa"
    assert result["cited_but_missing"] == []
    # The fixture's 01_abstract/02_intro predate the style outline, so R10 keeps
    # them in the export but honestly reports them as extras.
    assert result["warnings"] == [
        "Exported draft(s) not in the style outline: 01_abstract.md, 02_intro.md"
    ]
    assert polished == [(output, project.name)]
    assert formatted == [(output, get_style("article_imrad").formatting)]
    assert recorded[0]["resource_path"] == workspace_root / project.slug
    bib_text = (workspace_root / project.slug / "references.bib").read_text()
    assert "doe2024valid" in bib_text
    csl_text = (workspace_root / project.slug / "references.csl.json").read_text()
    assert "Valid Paper" in csl_text


@pytest.mark.asyncio
async def test_non_strict_export_reports_missing_citekeys(project_with_drafts, monkeypatch):
    project, _ = project_with_drafts
    await save_draft_section(project.id, "03_fake.md", "# Fake\n\nSee [@ghost2020fake].")
    recorded = []
    from research_mcp import server

    monkeypatch.setattr(server, "render_docx", _fake_render_engine(recorded))
    monkeypatch.setattr(server, "polish_docx", lambda path, title: None)
    monkeypatch.setattr(server, "apply_docx_formatting", lambda path, fmt: None)

    result = await export_to_docx(project.id, "thesis", strict_citations=False)

    assert result["cited_but_missing"] == ["ghost2020fake"]
    assert result["warnings"]
    assert len(recorded) == 1


@pytest.mark.asyncio
async def test_latex_export_reports_tex_and_pdf_paths(project_with_drafts, monkeypatch):
    project, workspace_root = project_with_drafts
    recorded = []
    from research_mcp import server

    def fake_render_latex(
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
                "resource_path": resource_path,
                "metadata": metadata,
            }
        )
        output_tex.parent.mkdir(parents=True, exist_ok=True)
        output_tex.write_text("\\documentclass{article}")
        return output_tex

    monkeypatch.setattr(server.latex_engine, "render_latex", fake_render_latex)
    monkeypatch.setattr(
        server.latex_engine,
        "compile_pdf",
        lambda tex_path, exports_dir: exports_dir / "paper.pdf",
    )

    result = await export_to_latex(project.id, "paper", template_name="article_id")

    assert result["format"] == "latex"
    assert result["tex_path"] == str(workspace_root / project.slug / "exports" / "paper.tex")
    assert result["pdf_path"] == str(workspace_root / project.slug / "exports" / "paper.pdf")
    assert result["output_path"] == result["pdf_path"]
    assert "Intro body" in recorded[0]["markdown"]
    assert str(recorded[0]["template"]).endswith("article_id.tex")
    metadata = recorded[0]["metadata"]
    assert metadata
    assert "geometry" in metadata


@pytest.mark.asyncio
async def test_unknown_template_name_is_a_tool_error(project_with_drafts):
    project, _ = project_with_drafts

    with pytest.raises(ToolError) as excinfo:
        await export_to_docx(project.id, "thesis", csl_style="nonexistent")

    assert "apa" in str(excinfo.value)


@pytest.mark.asyncio
async def test_export_rejects_path_traversal_output_name(project_with_drafts):
    project, _ = project_with_drafts

    with pytest.raises(ToolError):
        await export_to_markdown_bundle(project.id, "../escape")
