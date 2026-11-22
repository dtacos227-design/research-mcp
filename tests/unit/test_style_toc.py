"""A generated TOC must not sit next to the empty "Daftar Isi" skeleton heading."""

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
        id="p-toc",
        project_id=project_id,
        citekey="doe2024toc",
        doi="10.1/toc",
        title="TOC Paper",
        authors=["Jane Doe"],
        authors_structured=[{"given": "Jane", "family": "Doe", "literal": None}],
        year=2024,
        venue="Nature",
        item_type="journal-article",
        created_at="2024-01-01T00:00:00Z",
    )


def _write_skripsi_drafts(drafts):
    drafts.mkdir()
    (drafts / "04_daftar_isi.md").write_text(
        "# Daftar Isi\n\n<!-- guidance: generated from the headings -->\n"
    )
    (drafts / "10_bab1_pendahuluan.md").write_text("# Pendahuluan\n\nIsi bab satu.\n")


def test_style_merge_skips_the_skripsi_toc_skeleton(tmp_path):
    style = get_style("skripsi_s1")
    _write_skripsi_drafts(tmp_path / "drafts")

    merged, extra = server._style_merge_markdown(tmp_path, [], style, skip_toc=True)

    assert "Daftar Isi" not in merged
    assert "# BAB I Pendahuluan" in merged
    assert "Isi bab satu." in merged
    assert extra == []


def test_style_merge_keeps_the_toc_heading_for_the_live_blueprint(tmp_path):
    style = get_style("skripsi_s1")
    _write_skripsi_drafts(tmp_path / "drafts")

    merged, _extra = server._style_merge_markdown(tmp_path, [], style)

    # build_blueprint needs the heading to place `references.toc`.
    assert "# Daftar Isi" in merged


@pytest.mark.asyncio
async def test_markdown_bundle_warns_that_it_has_no_generated_toc(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        project = await repository.create_project(conn, settings.workspace_dir, "TOC Project")
        brief = ResearchBrief(
            style_id="skripsi_s1",
            title="T",
            research_question="Q",
            citation="apa",
        )
        await repository.set_project_brief(conn, project.id, brief.model_dump_json(), "skripsi_s1")
        await save_paper_and_sections(conn, project.id, _paper(project.id), sections=None)
    finally:
        await conn.close()

    await server.save_draft_section(
        project.id, "04_daftar_isi.md", "# Daftar Isi\n\n<!-- guidance: auto -->\n"
    )
    await server.save_draft_section(
        project.id, "10_bab1_pendahuluan.md", "# Pendahuluan\n\nIsi [@doe2024toc].\n"
    )
    recorded: list[str] = []

    def fake_render_markdown(markdown, bib_path, csl_path, output, resource_path=None, **kwargs):
        recorded.append(markdown)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
        return output

    monkeypatch.setattr(server, "render_markdown", fake_render_markdown)

    result = await server.export_to_markdown_bundle(project.id, "paper")

    assert result["style_id"] == "skripsi_s1"
    assert "Daftar Isi" not in recorded[0]
    assert "Isi [@doe2024toc]." in recorded[0]
    assert any("does not include a generated TOC" in warning for warning in result["warnings"])
