import shutil

import pytest

from research_mcp.server import create_project, get_project_status


@pytest.mark.asyncio
async def test_status_reports_project_dir_in_custom_root(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "Riset"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))

    created = await create_project("Skripsi S1")
    status = await get_project_status(created["id"])

    expected = (tmp_path / "Riset" / created["slug"]).resolve()
    assert status["workspace_path"] == str(expected)
    assert status["output_root"] == str((tmp_path / "Riset").resolve())


@pytest.mark.asyncio
async def test_status_survives_missing_subdirs(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "Riset"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))

    created = await create_project("Skripsi S1")
    shutil.rmtree(tmp_path / "Riset" / created["slug"] / "drafts")

    status = await get_project_status(created["id"])
    assert status["draft_filenames"] == []
