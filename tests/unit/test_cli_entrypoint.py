import json

import pytest

from research_mcp import server
from research_mcp.config import read_config_file


@pytest.fixture
def stub_run(monkeypatch):
    calls = []
    monkeypatch.setattr(server.mcp, "run", lambda: calls.append(True))
    return calls


def test_output_dir_flag_is_persisted(tmp_path, stub_run):
    server.main(["--output-dir", str(tmp_path / "Riset")])

    assert read_config_file()["output_root"] == str((tmp_path / "Riset").resolve())
    assert stub_run == [True]


def test_no_persist_skips_writing_the_config_file(tmp_path, stub_run):
    cfg = tmp_path / "config.toml"
    server.main(["--output-dir", str(tmp_path / "Riset"), "--no-persist"])

    assert not cfg.exists()
    assert stub_run == [True]


def test_show_config_prints_json_and_does_not_run_the_server(tmp_path, stub_run, capsys):
    server.main(["--output-dir", str(tmp_path / "Riset"), "--no-persist", "--show-config"])

    printed = json.loads(capsys.readouterr().out)
    assert printed["output_root"] == str((tmp_path / "Riset").resolve())
    assert printed["source"] == "cli"
    assert stub_run == []


def test_show_config_without_flags_reports_default(tmp_path, stub_run, capsys):
    server.main(["--show-config"])

    printed = json.loads(capsys.readouterr().out)
    assert printed["source"] == "default"
    assert stub_run == []


def test_filesystem_root_flag_exits_with_error(stub_run):
    with pytest.raises(SystemExit):
        server.main(["--output-dir", "/"])
    assert stub_run == []
