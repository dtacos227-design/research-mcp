"""Translate one :class:`FormattingSpec` into engine-specific formatting steps.

This is the single place that knows how an academic format maps onto WordCraft's
engine commands, so styles, exporters and the handoff bridge never duplicate the
mapping. Command ids and parameter shapes were verified against WordCraft's
command list:

* style names carry a space: ``"Heading 1"``, not ``"Heading1"``;
* page margins are **points** (``ParaProps.lineSpacing`` likewise uses points for
  ``atLeast``/``exactly``), hence :data:`CM_TO_PT`;
* ``ParaProps`` uses camelCase fields (``lineSpacing``, ``spaceBefore``,
  ``align``) and ``LineSpacing`` is tagged ``{"rule": "multiple", "value": n}``.

Everything returns a list of MCP tool steps shaped as
``{"tool": "execute", "arguments": {"command": <id>, "params": {...}}}`` so a
caller can forward them to ``wordcraft.execute`` unchanged.
"""

from research_mcp.styles.models import FormattingSpec

CM_TO_PT = 28.3464567

# Page-number placement in FormattingSpec terms -> WordCraft header/footer slot.
_PAGE_NUMBER_POSITIONS: dict[str, dict[str, str]] = {
    "footer_center": {"position": "bottom", "align": "center"},
    "footer_right": {"position": "bottom", "align": "right"},
    "header_right": {"position": "top", "align": "right"},
}

# Heading level -> (font size, space before, space after) in points.
_HEADING_FORMATS: tuple[tuple[int, float, float, float], ...] = (
    (1, 16, 12, 6),
    (2, 14, 12, 4),
    (3, 12, 10, 4),
)


def _execute(command: str, params: dict | None = None) -> dict:
    return {"tool": "execute", "arguments": {"command": command, "params": params or {}}}


def _cm_to_pt(value_cm: float) -> float:
    """Convert centimetres to points, rounded to a sane 0.01 pt precision."""
    return round(value_cm * CM_TO_PT, 2)


def wordcraft_style_steps(fmt: FormattingSpec) -> list[dict]:
    """Normal + ``Heading 1``..``Heading 3`` style definitions."""
    normal_para: dict = {
        "lineSpacing": {"rule": "multiple", "value": fmt.line_spacing},
        "align": "justify" if fmt.justify else "left",
    }
    steps = [
        _execute(
            "styles.modify",
            {
                "style": "Normal",
                "chr": {"font": fmt.font_family, "size": fmt.font_size_pt},
                "para": normal_para,
            },
        )
    ]
    for level, size, space_before, space_after in _HEADING_FORMATS:
        steps.append(
            _execute(
                "styles.modify",
                {
                    "style": f"Heading {level}",
                    "chr": {"font": fmt.font_family, "size": size, "bold": True},
                    "para": {
                        "spaceBefore": space_before,
                        "spaceAfter": space_after,
                        "keepNext": True,
                    },
                },
            )
        )
    return steps


def wordcraft_page_steps(fmt: FormattingSpec) -> list[dict]:
    """Paper size, margins and page numbering for the current section.

    The page-number format is emitted only together with a page number, so a
    roman-numbered front matter section is numbered but a format with
    ``page_number_position="none"`` does not silently change the numbering.
    """
    steps = [
        _execute("layout.size", {"name": fmt.paper_size}),
        _execute(
            "layout.margins",
            {
                "top": _cm_to_pt(fmt.margin_cm["top"]),
                "bottom": _cm_to_pt(fmt.margin_cm["bottom"]),
                "left": _cm_to_pt(fmt.margin_cm["left"]),
                "right": _cm_to_pt(fmt.margin_cm["right"]),
            },
        ),
    ]
    placement = _PAGE_NUMBER_POSITIONS.get(fmt.page_number_position)
    if placement is not None:
        steps.append(_execute("insert.pageNumber", dict(placement)))
        steps.append(_execute("layout.pageNumberFormat", {"format": fmt.page_number_format}))
    return steps


def wordcraft_column_steps(fmt: FormattingSpec) -> list[dict]:
    """Two columns only when the format asks for them (IEEE-style papers)."""
    if not fmt.two_column:
        return []
    return [_execute("layout.columns", {"count": 2})]
