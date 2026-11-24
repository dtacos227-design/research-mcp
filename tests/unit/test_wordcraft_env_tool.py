from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp import server
from research_mcp.exporters import wordcraft_bridge as wb


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    return tmp_path


@pytest.mark.asyncio
async def test_env_report_without_wordcraft(workspace, monkeypatch):
    monkeypatch.setattr(wb, "detect_wordcraft_cli", lambda *a, **k: None)
    monkeypatch.setattr(wb, "detect_wordcraft_desktop", lambda *a, **k: None)
    monkeypatch.setattr(wb, "probe_port", lambda *a, **k: False)
    monkeypatch.setattr(wb, "control_key_path", lambda *a, **k: workspace / "missing-key")
    report = await server.check_wordcraft_env()
    assert report["wordcraft_cli"]["found"] is False
    assert report["desktop_app"]["found"] is False
    assert report["live_desktop"]["ready"] is False
    assert report["confinement"]["mcp_command"][1] == "mcp"
    assert Path(report["workspace_root"]).is_dir()
    assert any("export_to_docx" in item for item in report["recommendations"])


@pytest.mark.asyncio
async def test_env_report_ready_live_desktop(workspace, monkeypatch):
    key = workspace / "control-key.local-7981"
    key.write_text("token")
    monkeypatch.setattr(wb, "detect_wordcraft_cli", lambda *a, **k: None)
    monkeypatch.setattr(wb, "probe_port", lambda *a, **k: True)
    monkeypatch.setattr(wb, "control_key_path", lambda *a, **k: key)
    report = await server.check_wordcraft_env()
    assert report["live_desktop"]["ready"] is True
    assert report["live_desktop"]["control_key_present"] is True
    assert any("--connect" in item for item in report["recommendations"])


@pytest.mark.asyncio
async def test_env_report_headless_recommendation(workspace, monkeypatch):
    cli = workspace / "wordcraft-cli"
    cli.write_text("#!/bin/sh\n")
    monkeypatch.setattr(wb, "detect_wordcraft_cli", lambda *a, **k: cli)
    monkeypatch.setattr(wb, "cli_version", lambda *a, **k: "wordcraft-cli 0.18.1")
    monkeypatch.setattr(wb, "probe_port", lambda *a, **k: False)
    monkeypatch.setattr(wb, "control_key_path", lambda *a, **k: workspace / "missing-key")
    report = await server.check_wordcraft_env()
    assert report["wordcraft_cli"] == {
        "found": True,
        "path": str(cli),
        "version": "wordcraft-cli 0.18.1",
    }
    assert any("--automation-read-root" in item for item in report["recommendations"])
    assert report["confinement"]["read_root"] == report["workspace_root"]


@pytest.mark.asyncio
async def test_env_report_unknown_project_raises(workspace, monkeypatch):
    monkeypatch.setattr(wb, "detect_wordcraft_cli", lambda *a, **k: None)
    monkeypatch.setattr(wb, "probe_port", lambda *a, **k: False)
    monkeypatch.setattr(wb, "control_key_path", lambda *a, **k: workspace / "missing-key")
    with pytest.raises(ToolError, match="not found"):
        await server.check_wordcraft_env("does-not-exist")


@pytest.mark.asyncio
async def test_confinement_lists_every_project_root(workspace, monkeypatch):
    monkeypatch.setattr(wb, "detect_wordcraft_cli", lambda *a, **k: None)
    monkeypatch.setattr(wb, "probe_port", lambda *a, **k: False)
    monkeypatch.setattr(wb, "control_key_path", lambda *a, **k: workspace / "missing-key")

    await server.create_project("A")
    await server.create_project("B", output_dir=str(workspace / "elsewhere"))

    report = await server.check_wordcraft_env()

    listed = {entry["root"] for entry in report["confinement"]["roots"]}
    assert listed == {
        str((workspace / "ws").resolve()),
        str((workspace / "elsewhere").resolve()),
    }
    assert report["confinement"]["read_root"] == str((workspace / "ws").resolve())
    for entry in report["confinement"]["roots"]:
        assert entry["mcp_command"][0] == "wordcraft-cli"
        assert entry["mcp_command"][-1] == entry["root"]


@pytest.mark.asyncio
async def test_multiple_roots_recommend_one_instance_per_root(workspace, monkeypatch):
    monkeypatch.setattr(wb, "detect_wordcraft_cli", lambda *a, **k: None)
    monkeypatch.setattr(wb, "probe_port", lambda *a, **k: False)
    monkeypatch.setattr(wb, "control_key_path", lambda *a, **k: workspace / "missing-key")

    await server.create_project("A", output_dir=str(workspace / "elsewhere"))

    report = await server.check_wordcraft_env()

    assert any("per root" in item for item in report["recommendations"])
