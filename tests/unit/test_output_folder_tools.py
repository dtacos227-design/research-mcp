import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.server import (
    create_project,
    get_output_config,
    list_projects,
)


@pytest.mark.asyncio
async def test_create_project_with_custom_output_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    target = tmp_path / "Kerja" / "Klien-X"

    created = await create_project("Client X", output_dir=str(target))

    assert created["output_root"] == str(target.resolve())
    assert created["project_dir"] == str((target / created["slug"]).resolve())
    assert (target / created["slug"] / "exports").is_dir()


@pytest.mark.asyncio
async def test_create_project_without_output_dir_uses_default(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "Riset"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))

    created = await create_project("Skripsi S1")

    assert created["output_root"] == str((tmp_path / "Riset").resolve())
    assert created["project_dir"] == str((tmp_path / "Riset" / created["slug"]).resolve())


@pytest.mark.asyncio
async def test_create_project_rejects_filesystem_root(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    with pytest.raises(ToolError, match="filesystem root"):
        await create_project("Bad", output_dir="/")


@pytest.mark.asyncio
async def test_list_projects_reports_output_root_and_project_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "Riset"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    created = await create_project("Skripsi S1")

    entry = (await list_projects())[0]

    assert entry["output_root"] == str((tmp_path / "Riset").resolve())
    assert entry["project_dir"] == created["project_dir"]


@pytest.mark.asyncio
async def test_get_output_config_reports_env_source(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "Riset"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))

    config = await get_output_config()

    assert config["source"] == "env"
    assert config["output_root"] == str((tmp_path / "Riset").resolve())
    assert config["db_path"] == str((tmp_path / "db.sqlite").resolve())
    assert config["writable"] is True
    assert config["config_file"].endswith("config.toml")


@pytest.mark.asyncio
async def test_get_output_config_reports_legacy_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "legacy"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))

    config = await get_output_config()

    assert config["source"] == "env-legacy"
    assert config["legacy_env_present"] is True
