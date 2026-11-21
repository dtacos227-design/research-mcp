from pathlib import Path

from research_mcp.exporters import wordcraft_bridge as wb


def test_settings_dir_macos(tmp_path):
    env = {"HOME": str(tmp_path)}
    expected = tmp_path / "Library/Application Support/WordCraft"
    assert wb.wordcraft_settings_dir(env, "darwin") == expected


def test_settings_dir_linux_xdg(tmp_path):
    env = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    assert wb.wordcraft_settings_dir(env, "linux") == tmp_path / "cfg" / "wordcraft"


def test_settings_dir_linux_default(tmp_path):
    env = {"HOME": str(tmp_path)}
    assert wb.wordcraft_settings_dir(env, "linux") == tmp_path / ".config" / "wordcraft"


def test_settings_dir_windows(tmp_path):
    env = {"APPDATA": str(tmp_path / "AppData")}
    assert wb.wordcraft_settings_dir(env, "win32") == tmp_path / "AppData" / "WordCraft"


def test_settings_dir_profile_override_wins(tmp_path):
    env = {"HOME": str(tmp_path), "WORDCRAFT_PROFILE_DIR": str(tmp_path / "profile")}
    assert wb.wordcraft_settings_dir(env, "darwin") == tmp_path / "profile"


def test_settings_dir_without_home_is_none():
    assert wb.wordcraft_settings_dir({}, "linux") is None


def test_control_key_path(tmp_path):
    env = {"HOME": str(tmp_path)}
    expected = tmp_path / "Library/Application Support/WordCraft" / "control-key.local-7981"
    assert wb.control_key_path(7981, env, "darwin") == expected


def test_detect_cli_from_env_override(tmp_path):
    binary = tmp_path / "wordcraft-cli"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    env = {"RESEARCH_MCP_WORDCRAFT_CLI": str(binary), "PATH": ""}
    assert wb.detect_wordcraft_cli(env, "linux") == binary


def test_detect_cli_from_path(tmp_path):
    binary = tmp_path / "wordcraft-cli"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    env = {"PATH": str(tmp_path), "HOME": str(tmp_path / "home")}
    assert wb.detect_wordcraft_cli(env, "linux") == binary


def test_detect_cli_from_known_candidate(tmp_path):
    home = tmp_path / "home"
    candidate = home / ".local/bin/wordcraft-cli"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("#!/bin/sh\n")
    candidate.chmod(0o755)
    found = wb.detect_wordcraft_cli({"HOME": str(home), "PATH": ""}, "linux")
    assert found == candidate


def test_detect_cli_missing(tmp_path):
    assert wb.detect_wordcraft_cli({"HOME": str(tmp_path), "PATH": ""}, "linux") is None


def test_detect_desktop_macos(tmp_path, monkeypatch):
    app = tmp_path / "Applications/WordCraft.app"
    app.mkdir(parents=True)
    monkeypatch.setattr(wb, "_desktop_candidates", lambda *args: [app])
    assert wb.detect_wordcraft_desktop("darwin", {"HOME": str(tmp_path)}) == app


def test_detect_desktop_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(wb, "_desktop_candidates", lambda *args: [tmp_path / "nope"])
    assert wb.detect_wordcraft_desktop("darwin", {"HOME": str(tmp_path)}) is None


def test_probe_port_true_and_false():
    import socket

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert wb.probe_port("127.0.0.1", port) is True
    finally:
        server.close()
    assert wb.probe_port("127.0.0.1", port, timeout=0.1) is False


def test_cli_version_reads_first_line(tmp_path):
    binary = tmp_path / "wordcraft-cli"
    binary.write_text("#!/bin/sh\necho 'wordcraft-cli 0.18.1'\n")
    binary.chmod(0o755)
    assert wb.cli_version(binary) == "wordcraft-cli 0.18.1"


def test_cli_version_missing_binary(tmp_path):
    assert wb.cli_version(tmp_path / "nope") is None


def test_build_mcp_command():
    cmd = wb.build_mcp_command(Path("/x/wordcraft-cli"), Path("/work"), "Research Agent")
    assert cmd == [
        "/x/wordcraft-cli",
        "mcp",
        "--author",
        "Research Agent",
        "--automation-read-root",
        "/work",
        "--automation-write-root",
        "/work",
    ]


def test_build_mcp_command_without_cli():
    assert wb.build_mcp_command(None, Path("/work"))[0] == "wordcraft-cli"
