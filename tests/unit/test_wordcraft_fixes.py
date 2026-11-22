"""Regression tests for the WordCraft handoff bridge.

Three review findings drive this file:

* **R2** — the bridge must not keep its own copy of the style mapping; it
  delegates to :func:`research_mcp.exporters.formatting.wordcraft_style_steps`.
* **BUG-1** — every block has to end its paragraph, otherwise WordCraft appends
  the next heading/table/list into the paragraph that is still open.
* **BUG-3** — Citeproc/Pandoc Markdown artefacts (horizontal rules, escaped
  glyphs, fenced-div markers, attribute blocks) must never reach ``type_text``.

A carry-over fix from the DOCX exporter is pinned here too: justified body text
must not leak into heading styles.
"""

import pytest

from research_mcp.exporters import formatting
from research_mcp.exporters import wordcraft_bridge as wb
from research_mcp.styles.models import FormattingSpec

NEW_PARAGRAPH = {
    "tool": "execute",
    "arguments": {"command": "text.newParagraph", "params": {}},
}


def _commands(steps: list[dict]) -> list[str]:
    return [step["arguments"].get("command") for step in steps]


# --- BUG-1: paragraph break after every block ------------------------------


@pytest.mark.parametrize(
    "block",
    [
        {"kind": "heading", "level": 1, "text": "BAB I"},
        {"kind": "heading", "level": 2, "text": "Scope"},
        {"kind": "heading", "level": 4, "text": "Deep"},
        {"kind": "paragraph", "text": "Isi"},
        {"kind": "table", "header": ["A"], "rows": [["1"]]},
        {"kind": "list", "ordered": False, "items": ["one", "two"]},
        {"kind": "list", "ordered": True, "items": ["one", "two"]},
        {"kind": "figure", "alt": "Chart", "path": "/tmp/chart.png", "exists": True},
    ],
    ids=["h1", "h2", "h4", "paragraph", "table", "bullets", "numbers", "figure"],
)
def test_every_block_ends_with_a_paragraph_break(block):
    steps = wb._block_steps(block)
    assert steps[-1] == NEW_PARAGRAPH


def test_heading_text_is_typed_before_its_style_and_break():
    steps = wb._block_steps({"kind": "heading", "level": 1, "text": "BAB I"})
    assert steps[0] == {"tool": "type_text", "arguments": {"text": "BAB I"}}
    assert steps[1]["arguments"]["command"] == "para.heading1"
    assert steps[-1] == NEW_PARAGRAPH


def test_deep_heading_style_name_keeps_its_space():
    steps = wb._block_steps({"kind": "heading", "level": 4, "text": "Deep"})
    style = steps[1]["arguments"]["params"]["props"]["style"]
    assert style == "Heading 4"


def test_table_moves_the_caret_out_before_breaking_the_paragraph():
    """`insert_table` leaves the caret in the last cell; a bare newParagraph would
    open a paragraph inside the table and swallow the next block."""
    steps = wb._block_steps({"kind": "table", "header": ["A"], "rows": [["1"]]})
    assert [step["arguments"].get("command") for step in steps] == [
        None,  # insert_table is a tool call, not an execute command
        "caret.docEnd",
        "text.newParagraph",
    ]
    assert steps[0]["tool"] == "insert_table"
    assert steps[-2]["arguments"]["command"] == "caret.docEnd"
    assert steps[-1] == NEW_PARAGRAPH


# --- R2: one source of style steps ----------------------------------------


def test_style_steps_delegate_to_formatting():
    assert wb._style_steps() == formatting.wordcraft_style_steps(FormattingSpec())


def test_style_steps_accept_a_formatting_spec():
    fmt = FormattingSpec(font_family="Liberation Serif", font_size_pt=11, line_spacing=2.0)
    assert wb._style_steps(fmt) == formatting.wordcraft_style_steps(fmt)


def test_style_steps_use_spaced_heading_names():
    names = [
        step["arguments"]["params"].get("style")
        for step in wb._style_steps()
        if step["arguments"]["command"] == "styles.modify"
    ]
    assert "Heading 1" in names
    assert "Heading1" not in names


def test_blueprint_style_steps_use_the_shared_names(tmp_path):
    markdown = "# Intro\n\nBody.\n"
    steps = wb.build_blueprint("T", tmp_path / "h.md", markdown, [], "apa", tmp_path, "out")[
        "tool_sequence"
    ]
    names = [
        step["arguments"]["params"].get("style")
        for step in steps
        if step["arguments"].get("command") == "styles.modify"
    ]
    assert "Heading 1" in names
    assert "Heading1" not in names


# --- Carry-over fix: justification stays out of heading styles -------------


def _heading_params(steps: list[dict]) -> list[dict]:
    return [
        step["arguments"]["params"]
        for step in steps
        if step["arguments"]["command"] == "styles.modify"
        and step["arguments"]["params"]["style"].startswith("Heading ")
    ]


def test_justified_body_pins_headings_left():
    steps = formatting.wordcraft_style_steps(FormattingSpec(justify=True))
    headings = _heading_params(steps)
    assert headings
    assert all(params["para"].get("align") == "left" for params in headings)


def test_ragged_body_does_not_justify_headings():
    steps = formatting.wordcraft_style_steps(FormattingSpec(justify=False))
    assert all(params["para"].get("align") != "justify" for params in _heading_params(steps))


# --- BUG-3: Markdown artefacts never reach type_text ----------------------


def test_strip_md_inline_removes_markdown_artefacts():
    raw = "---\n\\<div\\>\nText \\~ here {#refs .csl-bib-body} :::"
    cleaned = wb._strip_md_inline(raw)
    assert "---" not in cleaned
    assert "\\<" not in cleaned
    assert "{#refs" not in cleaned
    assert ":::" not in cleaned
    assert "Text" in cleaned
    assert "<div>" in cleaned
    assert "~" in cleaned


def test_strip_md_inline_keeps_plain_scientific_text():
    text = "Mean age was 42.5 years (SD 3.1); p < 0.05 for the trend."
    assert wb._strip_md_inline(text) == text


def test_strip_md_inline_collapses_space_left_by_attribute_blocks():
    assert wb._strip_md_inline("Body text {.unnumbered}") == "Body text"


def test_strip_md_inline_keeps_escaped_literal_braces():
    assert wb._strip_md_inline("The set \\{1, 2\\} is small.") == "The set {1, 2} is small."


def test_heading_steps_strip_markdown():
    steps = wb._block_steps({"kind": "heading", "level": 1, "text": "BAB I {#bab-i}"})
    assert steps[0]["arguments"]["text"] == "BAB I"


def test_paragraph_steps_strip_markdown():
    raw = "---\n\\<figcaption\\> Results \\~ solid {.note}"
    steps = wb._block_steps({"kind": "paragraph", "text": raw})
    assert steps[0]["arguments"]["text"] == "<figcaption> Results ~ solid"


def test_list_steps_strip_markdown_per_item():
    block = {"kind": "list", "ordered": False, "items": ["one {.a}", "two \\~ 3"]}
    steps = wb._block_steps(block)
    assert steps[0]["arguments"]["text"] == "one\ntwo ~ 3"


def test_bibliography_entry_text_is_stripped_before_typing():
    steps = wb._bibliography_steps(
        {"title": "References", "markdown": "Doe, J. \\~ 2024. Valid. {#ref-doe}"}
    )
    typed = steps[2]["arguments"]["text"]
    assert typed == "Doe, J. ~ 2024. Valid."


def test_blueprint_never_types_raw_markdown_artefacts(tmp_path):
    markdown = "# Intro {#intro}\n\n---\n\nBody \\~ text {.csl-entry}.\n"
    steps = wb.build_blueprint("T", tmp_path / "h.md", markdown, [], "apa", tmp_path, "out")[
        "tool_sequence"
    ]
    typed = [step["arguments"]["text"] for step in steps if step["tool"] == "type_text"]
    assert "Intro" in typed
    assert all("\\~" not in text and "{#" not in text and "---" not in text for text in typed)
