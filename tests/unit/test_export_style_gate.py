"""Style-aware DOCX export: the precondition gate and R10 chapter merge order."""

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
        id="p-gate",
        project_id=project_id,
        citekey="doe2024gate",
        doi="10.1/gate",
        title="Gate Paper",
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
    style_id: str = "skripsi_s1",
    with_brief: bool = True,
    with_paper: bool = True,
):
    """Create an isolated workspace project, optionally briefing and papering it."""
    workspace_root = tmp_path / "workspaces"
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(workspace_root))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        project = await repository.create_project(conn, settings.workspace_dir, "Gate Project")
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
    return project, workspace_root


def _fake_render_docx(recorded, payload=b"rendered"):
    def fake(markdown, *args, **kwargs):
        output = args[-1]
        recorded.append({"markdown": markdown, "output": output})
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payload)
        return output

    return fake


def _patch_export_side_effects(monkeypatch):
    rendered: list[dict] = []
    formatted: list[tuple] = []
    polished: list[tuple] = []
    monkeypatch.setattr(server, "render_docx", _fake_render_docx(rendered))
    monkeypatch.setattr(
        server,
        "apply_docx_formatting",
        lambda path, fmt: formatted.append((path, fmt)),
    )
    monkeypatch.setattr(server, "polish_docx", lambda path, title: polished.append((path, title)))
    return rendered, formatted, polished


@pytest.mark.asyncio
async def test_export_without_brief_is_blocked(tmp_path, monkeypatch):
    project, workspace_root = await _bootstrap(
        tmp_path, monkeypatch, with_brief=False, with_paper=False
    )
    _patch_export_side_effects(monkeypatch)

    result = await server.export_to_docx(project.id, "paper")

    assert result.get("status") == "precondition_failed"
    assert "brief" in str(result["failed"]).lower()
    assert result["remediation"]
    assert not (workspace_root / project.slug / "exports" / "paper.docx").exists()


@pytest.mark.asyncio
async def test_export_without_saved_paper_is_blocked(tmp_path, monkeypatch):
    project, workspace_root = await _bootstrap(tmp_path, monkeypatch, with_paper=False)
    _patch_export_side_effects(monkeypatch)

    result = await server.export_to_docx(project.id, "paper")

    assert result.get("status") == "precondition_failed"
    assert any("papers" in item for item in result["failed"])
    assert not (workspace_root / project.slug / "exports" / "paper.docx").exists()


@pytest.mark.asyncio
async def test_export_reports_style_id(tmp_path, monkeypatch):
    project, _workspace_root = await _bootstrap(tmp_path, monkeypatch, style_id="skripsi_s1")
    await server.save_draft_section(
        project.id, "10_bab1_pendahuluan.md", "# Pendahuluan\n\nIsi bab satu."
    )
    rendered, formatted, polished = _patch_export_side_effects(monkeypatch)

    result = await server.export_to_docx(project.id, "skripsi")

    assert result["style_id"] == "skripsi_s1"
    assert result["csl_style"] == "apa"
    assert result["format"] == "docx"
    assert result["cited_but_missing"] == []
    assert result["warnings"] == []
    assert "# BAB I Pendahuluan" in rendered[0]["markdown"]
    assert formatted == [(rendered[0]["output"], get_style("skripsi_s1").formatting)]
    assert len(polished) == 1


@pytest.mark.asyncio
async def test_outline_chapters_come_first_and_extras_are_warned(tmp_path, monkeypatch):
    """R10: outline order first (normalized), then unmatched drafts with a warning."""
    project, _workspace_root = await _bootstrap(tmp_path, monkeypatch, style_id="skripsi_s1")
    await server.save_draft_section(
        project.id, "10_bab1_pendahuluan.md", "# Pendahuluan\n\nIsi bab satu."
    )
    await server.save_draft_section(project.id, "01_catatan.md", "# Catatan\n\nBahan tambahan.")
    rendered, _formatted, _polished = _patch_export_side_effects(monkeypatch)

    result = await server.export_to_docx(project.id, "skripsi")

    merged = rendered[0]["markdown"]
    assert merged.index("# BAB I Pendahuluan") < merged.index("# Catatan")
    assert "Isi bab satu." in merged
    assert "Bahan tambahan." in merged
    assert result["warnings"] == ["Exported draft(s) not in the style outline: 01_catatan.md"]
