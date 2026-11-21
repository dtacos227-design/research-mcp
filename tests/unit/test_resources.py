import json

import pytest

from research_mcp import server


@pytest.mark.asyncio
async def test_projects_resource_registered():
    uris = [r.uri for r in await server.mcp.list_resources()]
    assert "research://projects" in uris


@pytest.mark.asyncio
async def test_templates_resource_registered():
    uris = [r.uri for r in await server.mcp.list_resources()]
    assert "research://templates" in uris


@pytest.mark.asyncio
async def test_project_template_resources_registered():
    templates = [t.uri_template for t in await server.mcp.list_resource_templates()]
    assert any("bibliography" in t for t in templates)
    assert any("csl-json" in t for t in templates)
    assert any("outline" in t for t in templates)


@pytest.mark.asyncio
async def test_projects_resource_returns_json(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    result = await server.resource_projects()
    data = json.loads(result)
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_templates_resource_lists_csl():
    result = await server.resource_templates()
    data = json.loads(result)
    assert "csl" in data
    assert any("apa" in f for f in data["csl"])
    assert "docx" in data
    assert "latex" in data


@pytest.mark.asyncio
async def test_bibliography_resource_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    from research_mcp import server as srv
    from research_mcp.db import repository as repo
    from research_mcp.db.database import get_connection

    settings = srv.get_settings()
    conn = await get_connection(settings.db_path)
    project = await repo.create_project(conn, settings.workspace_dir, "BibTest")
    await conn.close()
    result = await server.resource_bibliography(project_id=project.id)
    assert "generate_bibliography" in result


@pytest.mark.asyncio
async def test_csl_json_resource_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    from research_mcp import server as srv
    from research_mcp.db import repository as repo
    from research_mcp.db.database import get_connection

    settings = srv.get_settings()
    conn = await get_connection(settings.db_path)
    project = await repo.create_project(conn, settings.workspace_dir, "CslTest")
    await conn.close()
    result = await server.resource_csl_json(project_id=project.id)
    assert "generate_bibliography" in result


@pytest.mark.asyncio
async def test_outline_resource_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    from research_mcp import server as srv
    from research_mcp.db import repository as repo
    from research_mcp.db.database import get_connection

    settings = srv.get_settings()
    conn = await get_connection(settings.db_path)
    project = await repo.create_project(conn, settings.workspace_dir, "OutlineTest")
    await conn.close()
    result = await server.resource_outline(project_id=project.id)
    assert "save_draft_section" in result


@pytest.mark.asyncio
async def test_outline_resource_has_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    from research_mcp import server as srv
    from research_mcp.db import repository as repo
    from research_mcp.db.database import get_connection

    settings = srv.get_settings()
    conn = await get_connection(settings.db_path)
    project = await repo.create_project(conn, settings.workspace_dir, "OutlineWithSections")
    await conn.close()
    # save a draft section via the tool
    await srv.save_draft_section(
        project_id=project.id,
        filename="01_intro.md",
        content="# Introduction\n\nHello world.",
        order=1,
    )
    result = await server.resource_outline(project_id=project.id)
    assert "01_intro.md" in result
    assert "Introduction" in result


@pytest.mark.asyncio
async def test_bibliography_resource_has_content(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    from research_mcp import server as srv
    from research_mcp.db import repository as repo
    from research_mcp.db.database import get_connection

    settings = srv.get_settings()
    conn = await get_connection(settings.db_path)
    project = await repo.create_project(conn, settings.workspace_dir, "BibContent")
    await conn.close()
    # write a fake references.bib
    bib_path = settings.workspace_dir / project.slug / "references.bib"
    bib_path.parent.mkdir(parents=True, exist_ok=True)
    bib_path.write_text("@article{test2024, title={Test}}\n")
    result = await server.resource_bibliography(project_id=project.id)
    assert "@article" in result


@pytest.mark.asyncio
async def test_project_not_found_in_bibliography_resource(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    result = await server.resource_bibliography(project_id="nonexistent-id")
    assert "not found" in result.lower() or "Project" in result
