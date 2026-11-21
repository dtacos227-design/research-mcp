import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.exporters import wordcraft_bridge as wb


def test_citation_style_mapping():
    assert wb.citation_style_for("apa") == "APA"
    assert wb.citation_style_for("ieee") == "IEEE"
    assert wb.citation_style_for("vancouver") == "ISO 690 (numerical)"
    assert wb.citation_style_for("chicago") == "Chicago"
    with pytest.raises(ToolError):
        wb.citation_style_for("harvard")


def test_parse_blocks_kinds(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/chart.png").write_bytes(b"png")
    markdown = (
        "# Introduction\n\n"
        "Body text with [@doe2024valid].\n\n"
        "| Month | Cases |\n|---|---|\n| Jan | 12 |\n\n"
        "- one\n- two\n\n"
        "![Chart](assets/chart.png)\n\n"
        "```python\nprint(1)\n```\n"
    )
    blocks = wb.parse_markdown_blocks(markdown, tmp_path)
    assert [b["kind"] for b in blocks] == [
        "heading",
        "paragraph",
        "table",
        "list",
        "figure",
        "code",
    ]
    assert blocks[0] == {"kind": "heading", "level": 1, "text": "Introduction"}
    assert blocks[1] == {"kind": "paragraph", "text": "Body text with [@doe2024valid]."}
    assert blocks[2]["header"] == ["Month", "Cases"]
    assert blocks[2]["rows"] == [["Jan", "12"]]
    assert blocks[3]["ordered"] is False and blocks[3]["items"] == ["one", "two"]
    assert blocks[4]["path"] == str(tmp_path / "assets/chart.png")
    assert blocks[5]["language"] == "python"


def test_relative_asset_without_file_is_kept_and_warned(tmp_path):
    markdown = "![Missing](assets/nope.png)\n"
    blocks = wb.parse_markdown_blocks(markdown, tmp_path)
    assert blocks[0]["path"] == str(tmp_path / "assets/nope.png")
    assert blocks[0]["exists"] is False


def test_build_blueprint_sequence(tmp_path):
    (tmp_path / "assets").mkdir()
    image = tmp_path / "assets/chart.png"
    image.write_bytes(b"png")
    markdown = "# Intro\n\nText.\n\n![Chart](assets/chart.png)\n"
    blueprint = wb.build_blueprint(
        "My Thesis",
        tmp_path / "exports/handoff.md",
        markdown,
        [image],
        "apa",
        tmp_path / "exports",
        "manuscript",
    )
    steps = blueprint["tool_sequence"]
    tools = [step["tool"] for step in steps]
    commands = [step["arguments"].get("command") for step in steps]
    assert tools[0] == "new_document"
    assert commands[1] == "file.setAuthor"
    assert "references.citationStyle" in commands
    assert "insert.picture" in commands
    assert tools[-2] == "save_document"
    assert commands[-1] == "file.exportPdf"
    assert blueprint["assets"] == [str(image)]
    assert blueprint["warnings"] == []
    assert blueprint["blocks"][0]["kind"] == "heading"


def test_build_blueprint_warns_about_missing_asset(tmp_path):
    markdown = "![Missing](assets/nope.png)\n"
    blueprint = wb.build_blueprint(
        "T", tmp_path / "handoff.md", markdown, [], "ieee", tmp_path, "out"
    )
    assert blueprint["warnings"] == [f"Figure path does not exist: {tmp_path / 'assets/nope.png'}"]


def test_blueprint_heading_levels_and_lists(tmp_path):
    markdown = "### Deep\n\n1. first\n2. second\n"
    steps = wb.build_blueprint("T", tmp_path / "h.md", markdown, [], "apa", tmp_path, "out")[
        "tool_sequence"
    ]
    commands = [s["arguments"].get("command") for s in steps]
    assert "para.heading3" in commands
    assert "para.numbering" in commands


def test_split_bibliography_removes_trailing_references(tmp_path):
    markdown = "# Intro\n\nBody.\n\n# References\n\nDoe, J. (2024). Valid Paper.\n"
    blocks, bibliography = wb.split_bibliography(wb.parse_markdown_blocks(markdown, tmp_path))
    assert [block["kind"] for block in blocks] == ["heading", "paragraph"]
    assert bibliography["title"] == "References"
    assert bibliography["markdown"] == "Doe, J. (2024). Valid Paper."


def test_split_bibliography_without_references(tmp_path):
    blocks, bibliography = wb.split_bibliography(
        wb.parse_markdown_blocks("# Intro\n\nBody.\n", tmp_path)
    )
    assert len(blocks) == 2
    assert bibliography == {"title": "References", "markdown": ""}


def test_polish_sequence_starts_by_opening_the_seed(tmp_path):
    sequence = wb.build_polish_sequence(
        tmp_path / "exports/seed.docx", "ieee", tmp_path / "exports", "final"
    )
    assert sequence[0] == {
        "tool": "open_document",
        "arguments": {"path": str(tmp_path / "exports/seed.docx")},
    }
    commands = [step["arguments"].get("command") for step in sequence]
    assert "references.toc" in commands
    assert "file.strictReport" in commands
    assert commands[-1] == "file.exportPdf"
    assert sequence[-2]["arguments"]["path"] == str(tmp_path / "exports" / "final.docx")
