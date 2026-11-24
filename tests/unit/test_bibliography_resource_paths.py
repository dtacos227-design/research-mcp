import pytest

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.server import create_project, resource_bibliography, resource_csl_json


async def _move_project_to(tmp_path, project_id, root):
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        await conn.execute(
            "UPDATE projects SET output_root = ? WHERE id = ?", (str(root), project_id)
        )
        await conn.commit()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_bibliography_resource_reads_project_output_root(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "default"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    created = await create_project("Skripsi S1")
    custom = tmp_path / "Kerja" / "Klien-X"
    await _move_project_to(tmp_path, created["id"], custom)

    bib = custom / created["slug"] / "references.bib"
    bib.parent.mkdir(parents=True, exist_ok=True)
    bib.write_text("@article{demo2024,\n  title = {Demo}\n}\n", encoding="utf-8")

    assert "demo2024" in await resource_bibliography(created["id"])


@pytest.mark.asyncio
async def test_csl_json_resource_reads_project_output_root(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "default"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    created = await create_project("Skripsi S1")
    custom = tmp_path / "Kerja" / "Klien-X"
    await _move_project_to(tmp_path, created["id"], custom)

    csl = custom / created["slug"] / "references.csl.json"
    csl.parent.mkdir(parents=True, exist_ok=True)
    csl.write_text('[{"id": "demo2024"}]\n', encoding="utf-8")

    assert "demo2024" in await resource_csl_json(created["id"])
