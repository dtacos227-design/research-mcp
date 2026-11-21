import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.exporters import wordcraft_bridge as wb


def test_job_script_shape(tmp_path):
    script = wb.build_job_script(
        tmp_path / "exports/seed.docx",
        "apa",
        tmp_path / "exports/out.docx",
        tmp_path / "exports/out.pdf",
    )
    assert script["file"] == str(tmp_path / "exports/seed.docx")
    assert script["save"] == [
        str(tmp_path / "exports/out.docx"),
        str(tmp_path / "exports/out.pdf"),
    ]
    ids = [command["command"] for command in script["commands"]]
    assert ids[0] == "file.setAuthor"
    assert "references.citationStyle" in ids
    assert "references.toc" in ids
    assert ids[-1] == "caret.docEnd"
    assert script["commands"][1]["params"]["style"] == "APA"


def test_job_script_round_trips(tmp_path):
    script = wb.build_job_script(
        tmp_path / "a.docx", "ieee", tmp_path / "b.docx", tmp_path / "b.pdf"
    )
    payload = wb.serialize_job_script(script)
    assert json.loads(payload) == script


def test_job_script_rejects_oversize(tmp_path):
    script = wb.build_job_script(
        tmp_path / "a.docx", "apa", tmp_path / "b.docx", tmp_path / "b.pdf"
    )
    script["commands"].append(
        {"command": "text.insert", "params": {"text": "x" * (4 * 1024 * 1024)}}
    )
    with pytest.raises(ToolError, match="4 MiB"):
        wb.serialize_job_script(script)


def test_job_script_rejects_unknown_style(tmp_path):
    with pytest.raises(ToolError, match="CSL style"):
        wb.build_job_script(tmp_path / "a.docx", "harvard", tmp_path / "b.docx", tmp_path / "b.pdf")


def test_job_script_rejects_too_many_outputs(tmp_path):
    script = wb.build_job_script(
        tmp_path / "a.docx", "apa", tmp_path / "b.docx", tmp_path / "b.pdf"
    )
    script["save"] = [str(tmp_path / f"{index}.docx") for index in range(65)]
    with pytest.raises(ToolError, match="64"):
        wb.serialize_job_script(script)
