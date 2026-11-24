import pytest

from research_mcp import server
from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.server import create_project, import_local_pdf, save_paper_to_project


@pytest.fixture
def captured_ingestion(monkeypatch):
    calls = {}

    async def fake_ingest_local(conn, project_id, output_root, file_path, doi):
        calls["root"] = output_root
        return {"ok": True}

    async def fake_ingest_remote(conn, project_id, output_root, identifier, download_pdf):
        calls["root"] = output_root
        return {"ok": True}

    monkeypatch.setattr(server, "ingest_local_pdf", fake_ingest_local)
    monkeypatch.setattr(server, "ingest_remote_paper", fake_ingest_remote)
    return calls


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
async def test_import_local_pdf_targets_project_output_root(
    tmp_path, monkeypatch, captured_ingestion
):
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "default"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    created = await create_project("Skripsi S1")
    await _move_project_to(tmp_path, created["id"], tmp_path / "Kerja" / "Klien-X")

    await import_local_pdf(created["id"], str(tmp_path / "paper.pdf"))

    assert captured_ingestion["root"] == (tmp_path / "Kerja" / "Klien-X").resolve()


@pytest.mark.asyncio
async def test_save_paper_targets_project_output_root(tmp_path, monkeypatch, captured_ingestion):
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "default"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    created = await create_project("Skripsi S1")
    await _move_project_to(tmp_path, created["id"], tmp_path / "Kerja" / "Klien-X")

    await save_paper_to_project(created["id"], "10.1234/example")

    assert captured_ingestion["root"] == (tmp_path / "Kerja" / "Klien-X").resolve()
