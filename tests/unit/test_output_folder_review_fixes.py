"""Regressions from the independent review of the output-folder work (2026-11-24)."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp import config, server
from research_mcp.config import Settings, persist_output_root, read_config_file

# --- C1: --no-persist must still drive the tools -----------------------------


@pytest.mark.asyncio
async def test_no_persist_flag_still_drives_tool_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(server.mcp, "run", lambda: None)
    target = tmp_path / "SessionOnly"

    server.main(["--output-dir", str(target), "--no-persist"])

    created = await server.create_project("Session Only")
    assert created["project_dir"] == str((target / created["slug"]).resolve())
    assert not (tmp_path / "config.toml").exists()


@pytest.mark.asyncio
async def test_persisted_flag_also_drives_the_running_session(tmp_path, monkeypatch):
    monkeypatch.setattr(server.mcp, "run", lambda: None)
    target = tmp_path / "Riset"

    server.main(["--output-dir", str(target)])
    created = await server.create_project("Skripsi S1")

    assert created["project_dir"] == str((target / created["slug"]).resolve())


# --- I1: documented precedence is env(legacy) > config file ------------------


def test_legacy_env_beats_config_file(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'output_root = "{tmp_path / "from-config"}"\n', encoding="utf-8")
    monkeypatch.setenv("RESEARCH_MCP_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "from-legacy"))

    settings = Settings()

    assert settings.output_root == (tmp_path / "from-legacy").resolve()
    assert settings.output_root_source == "env-legacy"


def test_new_env_still_beats_legacy_env_and_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'output_root = "{tmp_path / "from-config"}"\n', encoding="utf-8")
    monkeypatch.setenv("RESEARCH_MCP_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "from-legacy"))
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "from-new"))

    settings = Settings()

    assert settings.output_root == (tmp_path / "from-new").resolve()
    assert settings.output_root_source == "env"


# --- C3: config.toml must never brick startup --------------------------------


def test_unknown_config_key_is_ignored(tmp_path, monkeypatch, caplog):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'output_rut = "/tmp/typo"\noutput_root = "{tmp_path / "Riset"}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_MCP_CONFIG_FILE", str(cfg))

    settings = Settings()

    assert settings.output_root == (tmp_path / "Riset").resolve()
    assert settings.output_root_source == "config"
    assert any("output_rut" in record.message for record in caplog.records)


def test_persist_quotes_keys_that_need_quoting(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('"my key" = "v"\n', encoding="utf-8")
    monkeypatch.setenv("RESEARCH_MCP_CONFIG_FILE", str(cfg))

    persist_output_root(tmp_path / "Riset")

    data = read_config_file()
    assert data["my key"] == "v"
    assert data["output_root"] == str((tmp_path / "Riset").resolve())


def test_persist_is_atomic_and_leaves_no_temp_file(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("RESEARCH_MCP_CONFIG_FILE", str(cfg))

    persist_output_root(tmp_path / "Riset")

    assert cfg.is_file()
    assert [p.name for p in tmp_path.iterdir() if "config.toml" in p.name] == ["config.toml"]


# --- C2: an existing database is never abandoned -----------------------------


def test_db_adjacent_to_legacy_env_root_is_found_after_moving_output(tmp_path, monkeypatch):
    old_root = tmp_path / "Data" / "workspaces"
    old_root.mkdir(parents=True)
    old_db = tmp_path / "Data" / "research_mcp.db"
    old_db.write_text("", encoding="utf-8")
    monkeypatch.setattr(config, "DEFAULT_DB_PATH", tmp_path / "home" / "research_mcp.db")
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(old_root))
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "Riset"))
    monkeypatch.delenv("RESEARCH_MCP_DB_PATH", raising=False)

    assert Settings().db_path == old_db.resolve()


def test_existing_canonical_db_wins_over_root_adjacent_db(tmp_path, monkeypatch):
    canonical = tmp_path / "home" / "research_mcp.db"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("", encoding="utf-8")
    root_adjacent = tmp_path / "elsewhere" / "research_mcp.db"
    root_adjacent.parent.mkdir(parents=True)
    root_adjacent.write_text("", encoding="utf-8")
    monkeypatch.setattr(config, "DEFAULT_DB_PATH", canonical)
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(tmp_path / "elsewhere" / "workspaces"))
    monkeypatch.delenv("RESEARCH_MCP_DB_PATH", raising=False)
    monkeypatch.delenv("RESEARCH_MCP_WORKSPACE", raising=False)

    assert Settings().db_path == canonical.resolve()


# --- I2 / I5: the global root is validated, and described honestly -----------


@pytest.mark.asyncio
async def test_create_project_validates_the_global_root(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", "/")
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))

    with pytest.raises(ToolError, match="filesystem root"):
        await server.create_project("RootEnv")


@pytest.mark.asyncio
async def test_create_project_rejects_empty_output_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))

    with pytest.raises(ToolError, match="empty"):
        await server.create_project("Empty", output_dir="   ")


@pytest.mark.asyncio
async def test_output_config_reports_file_root_as_not_writable(tmp_path, monkeypatch):
    not_a_dir = tmp_path / "notadir"
    not_a_dir.write_text("x", encoding="utf-8")
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(not_a_dir))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))

    report = await server.get_output_config()

    assert report["writable"] is False
    assert report["is_dir"] is False
    assert report["exists"] is True


# --- I3: a read-only diagnostic must not create folders ---------------------


@pytest.mark.asyncio
async def test_wordcraft_env_does_not_create_output_roots(tmp_path, monkeypatch):
    root = tmp_path / "Riset"
    monkeypatch.setenv("RESEARCH_MCP_OUTPUT_ROOT", str(root))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setattr(server.wordcraft_bridge, "detect_wordcraft_cli", lambda *a, **k: None)
    monkeypatch.setattr(server.wordcraft_bridge, "probe_port", lambda *a, **k: False)
    monkeypatch.setattr(
        server.wordcraft_bridge, "control_key_path", lambda *a, **k: tmp_path / "missing"
    )

    report = await server.check_wordcraft_env()

    assert not root.exists()
    entry = report["confinement"]["roots"][0]
    assert entry["exists"] is False
    assert entry["is_dir"] is False
    assert entry["writable"] is True  # parent is writable, so it could be created


# --- I4: never adopt a folder that already holds files ----------------------


@pytest.mark.asyncio
async def test_create_project_refuses_a_non_empty_target_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "keep.md").write_text("x", encoding="utf-8")

    with pytest.raises(ToolError, match="already"):
        await server.create_project("Taken", output_dir=str(tmp_path))

    assert (taken / "keep.md").is_file()
    assert await server.list_projects() == []


# --- Custom root actually receives writes ----------------------------------


@pytest.mark.asyncio
async def test_draft_write_lands_in_the_custom_output_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "db.sqlite"))
    custom = tmp_path / "Kerja" / "Klien-X"
    created = await server.create_project("Client X", output_dir=str(custom))

    await server.save_draft_section(
        created["id"], "01_intro.md", "# Intro\n\nHello.\n", format_only=True
    )

    assert (custom / created["slug"] / "drafts" / "01_intro.md").is_file()
