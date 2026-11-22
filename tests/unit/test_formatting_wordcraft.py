"""WordCraft translations of ``FormattingSpec`` stay pinned to verified command ids.

Every step is ``{"tool": "execute", "arguments": {"command": ..., "params": ...}}``
so the bridge can forward it to ``wordcraft.execute`` unchanged. Command and
parameter names were checked against WordCraft's engine command list; in
particular style names carry a space ("Heading 1") and page margins are points.
"""

import pytest

from research_mcp.exporters import formatting
from research_mcp.styles.models import FormattingSpec


def _commands(steps: list[dict]) -> list[str]:
    return [step["arguments"]["command"] for step in steps]


def _params(steps: list[dict], command: str) -> dict:
    step = next(step for step in steps if step["arguments"]["command"] == command)
    return step["arguments"]["params"]


def test_steps_wrap_the_execute_tool():
    for step in formatting.wordcraft_style_steps(FormattingSpec()):
        assert step["tool"] == "execute"
        assert isinstance(step["arguments"]["command"], str)
        assert isinstance(step["arguments"]["params"], dict)


def test_style_steps_use_spaced_heading_names():
    steps = formatting.wordcraft_style_steps(FormattingSpec(line_spacing=2.0))
    names = [
        step["arguments"]["params"].get("style")
        for step in steps
        if step["arguments"]["command"] == "styles.modify"
    ]
    assert "Heading 1" in names
    assert "Heading1" not in names


def test_normal_style_carries_font_size_and_line_spacing():
    fmt = FormattingSpec(font_family="Liberation Serif", font_size_pt=11, line_spacing=2.0)
    params = _params(formatting.wordcraft_style_steps(fmt), "styles.modify")
    assert params["style"] == "Normal"
    assert params["chr"] == {"font": "Liberation Serif", "size": 11}
    assert params["para"]["lineSpacing"] == {"rule": "multiple", "value": 2.0}


def test_normal_style_maps_justify_flag_to_alignment():
    justified_steps = formatting.wordcraft_style_steps(FormattingSpec(justify=True))
    ragged_steps = formatting.wordcraft_style_steps(FormattingSpec(justify=False))
    assert _params(justified_steps, "styles.modify")["para"]["align"] == "justify"
    assert _params(ragged_steps, "styles.modify")["para"]["align"] == "left"


def test_headings_are_bold_with_descending_sizes():
    steps = formatting.wordcraft_style_steps(FormattingSpec(font_family="Georgia"))
    headings = [
        step["arguments"]["params"]
        for step in steps
        if step["arguments"]["command"] == "styles.modify"
        and step["arguments"]["params"]["style"].startswith("Heading ")
    ]
    assert [params["style"] for params in headings] == ["Heading 1", "Heading 2", "Heading 3"]
    assert [params["chr"]["size"] for params in headings] == [16, 14, 12]
    assert all(params["chr"]["bold"] is True for params in headings)
    assert all(params["chr"]["font"] == "Georgia" for params in headings)
    assert all("spaceBefore" in params["para"] for params in headings)
    assert all("spaceAfter" in params["para"] for params in headings)


def test_page_steps_convert_cm_to_pt():
    fmt = FormattingSpec(margin_cm={"top": 3, "right": 3, "bottom": 3, "left": 4})
    steps = formatting.wordcraft_page_steps(fmt)
    margins = next(s for s in steps if s["arguments"]["command"] == "layout.margins")
    assert round(margins["arguments"]["params"]["left"], 1) == 113.4
    assert round(margins["arguments"]["params"]["top"], 1) == 85.0
    size = next(s for s in steps if s["arguments"]["command"] == "layout.size")
    assert size["arguments"]["params"] == {"name": "A4"}


def test_page_steps_keep_paper_size_and_all_four_margins():
    fmt = FormattingSpec(
        paper_size="Letter",
        margin_cm={"top": 1.5, "right": 2, "bottom": 2.5, "left": 3},
        page_number_position="none",
    )
    steps = formatting.wordcraft_page_steps(fmt)
    assert _params(steps, "layout.size") == {"name": "Letter"}
    assert _params(steps, "layout.margins") == {
        "top": round(1.5 * formatting.CM_TO_PT, 2),
        "right": round(2 * formatting.CM_TO_PT, 2),
        "bottom": round(2.5 * formatting.CM_TO_PT, 2),
        "left": round(3 * formatting.CM_TO_PT, 2),
    }


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        ("footer_center", {"position": "bottom", "align": "center"}),
        ("footer_right", {"position": "bottom", "align": "right"}),
        ("header_right", {"position": "top", "align": "right"}),
    ],
)
def test_page_number_position_maps_to_wordcraft(position: str, expected: dict):
    fmt = FormattingSpec(page_number_position=position)
    assert _params(formatting.wordcraft_page_steps(fmt), "insert.pageNumber") == expected


def test_page_number_format_follows_placement():
    roman = formatting.wordcraft_page_steps(
        FormattingSpec(page_number_position="footer_center", page_number_format="lowerRoman")
    )
    assert _params(roman, "layout.pageNumberFormat") == {"format": "lowerRoman"}

    none = formatting.wordcraft_page_steps(FormattingSpec(page_number_position="none"))
    assert "insert.pageNumber" not in _commands(none)
    assert "layout.pageNumberFormat" not in _commands(none)


def test_columns_only_when_two_column():
    assert formatting.wordcraft_column_steps(FormattingSpec(two_column=True))
    assert formatting.wordcraft_column_steps(FormattingSpec(two_column=False)) == []


def test_columns_emit_two_columns():
    steps = formatting.wordcraft_column_steps(FormattingSpec(two_column=True))
    assert _commands(steps) == ["layout.columns"]
    assert _params(steps, "layout.columns") == {"count": 2}
