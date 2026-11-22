"""A style's reference outline section must not duplicate Citeproc's `# References`."""

import shutil

import pytest

from research_mcp import server
from research_mcp.config import get_settings
from research_mcp.db import repository
from research_mcp.db.database import get_connection
from research_mcp.db.repository import save_paper_and_sections
from research_mcp.exporters.wordcraft_bridge import BIBLIOGRAPHY_HEADINGS
from research_mcp.models import PaperModel, ResearchBrief
from research_mcp.styles.models import SectionSpec
from research_mcp.styles.registry import get_style

REFERENCE_STYLES = [
    "article_imrad",
    "ieee_paper",
    "case_study",
    "rapid_synthesis",
    "annotated_bibliography",
]


def _is_reference_heading(line: str) -> bool:
    if not line.startswith("#"):
        return False
    # Pandoc decorates the generated heading with `{#bibliography .unnumbered}`.
    text = line.lstrip("#").split("{", 1)[0].strip().casefold()
    return text in {heading.casefold() for heading in BIBLIOGRAPHY_HEADINGS}


def _reference_section(style) -> SectionSpec:
    for section in style.outline:
        if server._is_bibliography_section(section):
            return section
    raise AssertionError(f"{style.id} has no reference section")


def _body_section(style) -> SectionSpec:
    for section in style.outline:
        if section.kind == "body" and not server._is_bibliography_section(section):
            return section
    raise AssertionError(f"{style.id} has no body section")


def _paper(project_id: str) -> PaperModel:
    return PaperModel(
        id="p-ref",
        project_id=project_id,
        citekey="doe2024ref",
        doi="10.1/ref",
        title="Reference Paper",
        authors=["Jane Doe"],
        authors_structured=[{"given": "Jane", "family": "Doe", "literal": None}],
        year=2024,
        venue="Nature",
        item_type="journal-article",
        created_at="2024-01-01T00:00:00Z",
    )


@pytest.mark.parametrize("style_id", REFERENCE_STYLES)
def test_style_merge_skips_the_reference_outline_section(style_id, tmp_path):
    style = get_style(style_id)
    body = _body_section(style)
    reference = _reference_section(style)
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / body.filename).write_text(f"# {body.title}\n\nBody [@doe2024ref].\n")
    (drafts / reference.filename).write_text(f"# {reference.title}\n\n<!-- guidance: manual -->\n")

    merged, extra = server._style_merge_markdown(tmp_path, [], style)

    assert "Body [@doe2024ref]." in merged
    assert not any(_is_reference_heading(line) for line in merged.splitlines())
    assert extra == []


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is required")
@pytest.mark.asyncio
@pytest.mark.parametrize("style_id", REFERENCE_STYLES)
async def test_citeproc_renders_exactly_one_reference_heading(style_id, tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        project = await repository.create_project(conn, settings.workspace_dir, "Ref Project")
        style = get_style(style_id)
        brief = ResearchBrief(
            style_id=style_id,
            title="T",
            research_question="Q",
            citation=style.citation,
        )
        await repository.set_project_brief(conn, project.id, brief.model_dump_json(), style_id)
        await save_paper_and_sections(conn, project.id, _paper(project.id), sections=None)
    finally:
        await conn.close()

    body = _body_section(style)
    reference = _reference_section(style)
    await server.save_draft_section(
        project.id, body.filename, f"# {body.title}\n\nBody [@doe2024ref].\n"
    )
    await server.save_draft_section(
        project.id, reference.filename, f"# {reference.title}\n\n<!-- guidance: manual -->\n"
    )

    result = await server.export_to_markdown_bundle(project.id, "paper")

    rendered = (settings.workspace_dir / project.slug / "exports" / "paper.md").read_text(
        encoding="utf-8"
    )
    headings = [line for line in rendered.splitlines() if _is_reference_heading(line)]
    assert len(headings) == 1, rendered
    assert result["style_id"] == style_id
