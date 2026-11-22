"""Format-only mode: use the exporters without a research brief or saved papers."""

import pytest
import pytest_asyncio

from research_mcp import server


@pytest_asyncio.fixture
async def project(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspaces"
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(workspace_root))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    created = await server.create_project("Format Only")
    return created, workspace_root / created["slug"]


@pytest.mark.asyncio
async def test_save_draft_without_brief_is_gated_by_default(project):
    created, _ = project
    result = await server.save_draft_section(created["id"], "body.md", "# Body\n\nText.", 1)
    assert result["status"] == "needs_input"


@pytest.mark.asyncio
async def test_save_draft_format_only_writes_without_brief(project):
    created, project_dir = project
    result = await server.save_draft_section(
        created["id"], "body.md", "# Body\n\nText.", 1, format_only=True
    )
    assert result.get("status") != "needs_input"
    assert (project_dir / "drafts" / "body.md").is_file()


@pytest.mark.asyncio
async def test_export_defaults_still_gate(project):
    created, _ = project
    for result in (
        await server.export_to_docx(created["id"], "out"),
        await server.export_to_latex(created["id"], "out", compile_pdf=False),
        await server.export_to_markdown_bundle(created["id"], "out"),
        await server.prepare_wordcraft_handoff(created["id"]),
    ):
        assert result["status"] == "precondition_failed"


@pytest.mark.asyncio
async def test_docx_format_only_skips_gate(project, monkeypatch):
    created, project_dir = project
    await server.save_draft_section(
        created["id"], "body.md", "# Body\n\nText.", 1, format_only=True
    )

    def fake_render(markdown, *args, **kwargs):
        output = args[-1]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"docx")
        return output

    monkeypatch.setattr(server, "render_docx", fake_render)
    monkeypatch.setattr(server, "apply_docx_formatting", lambda path, fmt, **kwargs: None)
    monkeypatch.setattr(server, "polish_docx", lambda path, title: None)

    result = await server.export_to_docx(created["id"], "out", format_only=True)

    assert result["format_only"] is True
    assert result["style_id"] == "article_imrad"
    assert (project_dir / "exports" / "out.docx").is_file()


@pytest.mark.asyncio
async def test_latex_format_only_skips_gate(project, monkeypatch):
    created, _ = project
    await server.save_draft_section(
        created["id"], "body.md", "# Body\n\nText.", 1, format_only=True
    )

    def fake_latex(markdown, template_path, bib_path, csl_path, output_tex, **kwargs):
        output_tex.parent.mkdir(parents=True, exist_ok=True)
        output_tex.write_text("\\documentclass{article}")
        return output_tex

    monkeypatch.setattr(server.latex_engine, "render_latex", fake_latex)

    result = await server.export_to_latex(created["id"], "out", compile_pdf=False, format_only=True)

    assert result["format_only"] is True
    assert result["style_id"] == "article_imrad"


@pytest.mark.asyncio
async def test_markdown_format_only_strips_guidance(project, monkeypatch):
    created, project_dir = project
    await server.save_draft_section(
        created["id"],
        "body.md",
        "# Body\n\nText. <!-- guidance: secret -->",
        1,
        format_only=True,
    )
    recorded = []

    def fake_markdown(markdown, *args, **kwargs):
        recorded.append(markdown)
        output = args[-1]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown)
        return output

    monkeypatch.setattr(server, "render_markdown", fake_markdown)

    result = await server.export_to_markdown_bundle(created["id"], "out", format_only=True)

    assert result["format_only"] is True
    assert "guidance:" not in recorded[0]
    assert (project_dir / "exports" / "out.md").is_file()


@pytest.mark.asyncio
async def test_handoff_format_only_skips_gate(project, monkeypatch):
    created, project_dir = project
    await server.save_draft_section(
        created["id"], "body.md", "# Body\n\nText.", 1, format_only=True
    )

    def fake_markdown(markdown, *args, **kwargs):
        output = args[-1]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown)
        return output

    monkeypatch.setattr(server, "render_markdown", fake_markdown)

    result = await server.prepare_wordcraft_handoff(
        created["id"], mode="live_authoring", format_only=True
    )

    assert result["format_only"] is True
    assert result["style_id"] == "article_imrad"
    assert result["blueprint"] is not None
    assert (project_dir / "exports").is_dir()
