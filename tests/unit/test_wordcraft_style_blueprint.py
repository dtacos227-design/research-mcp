"""The writing blueprint must apply a style's page setup before writing content."""

from research_mcp.exporters import wordcraft_bridge as wb
from research_mcp.styles import registry
from research_mcp.styles.models import FormattingSpec, StyleSpec


def _commands(sequence: list[dict]) -> list[str]:
    return [step["arguments"].get("command") for step in sequence if step["tool"] == "execute"]


def test_blueprint_includes_page_setup(tmp_path):
    (tmp_path / "exports").mkdir()
    md = tmp_path / "d.md"
    md.write_text("# BAB I\n\nIsi.")
    style = registry.get_style("skripsi_s1")
    bp = wb.build_blueprint(
        "P", md, md.read_text(), [], "apa", tmp_path / "exports", "out", style=style
    )
    commands = [
        s["arguments"].get("command") for s in bp["tool_sequence"] if s["tool"] == "execute"
    ]
    assert "layout.size" in commands
    assert "layout.margins" in commands
    assert "text.newParagraph" in commands


def test_page_setup_precedes_typed_content(tmp_path):
    (tmp_path / "exports").mkdir()
    md = tmp_path / "d.md"
    md.write_text("# BAB I\n\nIsi.")
    style = registry.get_style("skripsi_s1")
    bp = wb.build_blueprint(
        "P", md, md.read_text(), [], "apa", tmp_path / "exports", "out", style=style
    )
    sequence = bp["tool_sequence"]
    setup_at = next(
        index
        for index, step in enumerate(sequence)
        if step["arguments"].get("command") == "layout.margins"
    )
    first_text = next(index for index, step in enumerate(sequence) if step["tool"] == "type_text")
    assert setup_at < first_text
    assert _commands(sequence).index("references.citationStyle") < _commands(sequence).index(
        "para.heading1"
    )


def test_page_setup_uses_the_style_values(tmp_path):
    (tmp_path / "exports").mkdir()
    md = tmp_path / "d.md"
    md.write_text("# Intro\n\nBody.")
    style = registry.get_style("skripsi_s1")
    bp = wb.build_blueprint(
        "P", md, md.read_text(), [], "apa", tmp_path / "exports", "out", style=style
    )
    margins = next(
        step["arguments"]["params"]
        for step in bp["tool_sequence"]
        if step["arguments"].get("command") == "layout.margins"
    )
    assert margins["left"] > margins["top"]  # 4 cm left vs 3 cm elsewhere
    size = next(
        step["arguments"]["params"]["name"]
        for step in bp["tool_sequence"]
        if step["arguments"].get("command") == "layout.size"
    )
    assert size == "A4"


def test_two_column_style_emits_columns_without_warning(tmp_path):
    style = registry.get_style("ieee_paper")
    assert style.formatting.two_column is True
    md = tmp_path / "d.md"
    md.write_text("# Intro\n\nBody.")
    bp = wb.build_blueprint(
        "P", md, md.read_text(), [], "ieee", tmp_path / "exports", "out", style=style
    )
    assert "layout.columns" in _commands(bp["tool_sequence"])
    assert not any("column" in warning.lower() for warning in bp["warnings"])


def test_heading_numbering_degrades_with_a_warning(tmp_path):
    style = registry.get_style("skripsi_s1")
    assert style.formatting.heading_numbering == "roman_bab"
    md = tmp_path / "d.md"
    md.write_text("# BAB I\n\nIsi.")
    bp = wb.build_blueprint(
        "P", md, md.read_text(), [], "apa", tmp_path / "exports", "out", style=style
    )
    assert any("numbering" in warning.lower() for warning in bp["warnings"])
    assert any("BAB" in warning for warning in bp["warnings"])


def test_no_numbering_warning_when_numbering_is_none(tmp_path):
    style = StyleSpec(
        id="plain",
        label="Plain",
        outline=[],
        formatting=FormattingSpec(heading_numbering="none"),
    )
    md = tmp_path / "d.md"
    md.write_text("# Intro\n\nBody.")
    bp = wb.build_blueprint(
        "P", md, md.read_text(), [], "apa", tmp_path / "exports", "out", style=style
    )
    assert not any("numbering" in warning.lower() for warning in bp["warnings"])


def test_blueprint_without_style_keeps_the_default_sequence(tmp_path):
    markdown = "# Intro\n\nBody.\n"
    bp = wb.build_blueprint("T", tmp_path / "h.md", markdown, [], "apa", tmp_path, "out")
    commands = _commands(bp["tool_sequence"])
    assert "layout.size" not in commands
    assert "layout.margins" not in commands
    assert "layout.columns" not in commands
    assert bp["warnings"] == []


def test_polish_sequence_applies_style_page_setup(tmp_path):
    style = registry.get_style("skripsi_s1")
    sequence = wb.build_polish_sequence(
        tmp_path / "exports/seed.docx", "apa", tmp_path / "exports", "final", style=style
    )
    commands = _commands(sequence)
    assert "layout.size" in commands
    assert "layout.margins" in commands


def test_polish_sequence_without_style_has_no_page_setup(tmp_path):
    sequence = wb.build_polish_sequence(
        tmp_path / "exports/seed.docx", "apa", tmp_path / "exports", "final"
    )
    commands = _commands(sequence)
    assert "layout.size" not in commands
    assert "layout.margins" not in commands


def test_bibliography_leaves_generated_content(tmp_path):
    markdown = "# Intro\n\nBody.\n\n# References\n\nDoe, J. (2024). Valid Paper.\n"
    bp = wb.build_blueprint("T", tmp_path / "h.md", markdown, [], "apa", tmp_path, "out")
    commands = _commands(bp["tool_sequence"])
    caret_at = commands.index("caret.docEnd")
    assert commands[caret_at + 1] == "text.newParagraph"
    assert commands.index("file.strictReport") > caret_at + 1
