import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.server import create_project, delete_project


@pytest.mark.asyncio
async def test_delete_project_works_outside_default_root(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "default"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))

    other_root = tmp_path / "elsewhere"
    created = await create_project("Thesis")
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        await conn.execute(
            "UPDATE projects SET output_root = ? WHERE id = ?",
            (str(other_root), created["id"]),
        )
        await conn.commit()
    finally:
        await conn.close()
    (other_root / created["slug"]).mkdir(parents=True)

    result = await delete_project(created["id"], confirm=created["slug"])
    assert result["workspace_removed"] is True
    assert not (other_root / created["slug"]).exists()


@pytest.mark.asyncio
async def test_delete_project_refuses_when_output_root_is_filesystem_root(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    created = await create_project("Rooted")

    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        await conn.execute(
            "UPDATE projects SET output_root = '/', slug = 'tmp' WHERE id = ?",
            (created["id"],),
        )
        await conn.commit()
    finally:
        await conn.close()

    with pytest.raises(ToolError, match="filesystem root"):
        await delete_project(created["id"], confirm="tmp")
