"""Translate one :class:`FormattingSpec` into engine-specific formatting steps.

This is the single place that knows how an academic format maps onto export
backends, so styles, exporters and the handoff bridge never duplicate the
mapping. The WordCraft side emits engine commands; the DOCX and LaTeX fallbacks
consume the same spec so all three paths render one document identically.

Command ids and parameter shapes were verified against WordCraft's command list:

* style names carry a space: ``"Heading 1"``, not ``"Heading1"``;
* page margins are **points** (``ParaProps.lineSpacing`` likewise uses points for
  ``atLeast``/``exactly``), hence :data:`CM_TO_PT`;
* ``ParaProps`` uses camelCase fields (``lineSpacing``, ``spaceBefore``,
  ``align``) and ``LineSpacing`` is tagged ``{"rule": "multiple", "value": n}``.

Everything returns a list of MCP tool steps shaped as
``{"tool": "execute", "arguments": {"command": <id>, "params": {...}}}`` so a
caller can forward them to ``wordcraft.execute`` unchanged.
"""

import re
from pathlib import Path

from research_mcp.styles.models import FormattingSpec, SectionSpec

CM_TO_PT = 28.3464567

# A4 is the academic default; Letter is the only other paper size we support.
_PAGE_SIZE_CM: dict[str, tuple[float, float]] = {
    "A4": (21.0, 29.7),
    "Letter": (21.59, 27.94),
}

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
        # Heading styles are based on Normal and carry no own alignment, so a
        # justified Normal would stretch wrapped chapter headings. Pin them left
        # there too, mirroring the DOCX fix in `apply_docx_formatting`.
        heading_para: dict = {
            "spaceBefore": space_before,
            "spaceAfter": space_after,
            "keepNext": True,
        }
        if fmt.justify:
            heading_para["align"] = "left"
        steps.append(
            _execute(
                "styles.modify",
                {
                    "style": f"Heading {level}",
                    "chr": {"font": fmt.font_family, "size": size, "bold": True},
                    "para": heading_para,
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


def apply_docx_formatting(path: Path, fmt: FormattingSpec) -> None:
    """Apply paper size, margins, Normal font/spacing to an exported `.docx` in place.

    Pandoc's ``--reference-doc`` does not carry page margins reliably, so the
    exporter re-applies them here. Justification goes on the ``Normal`` style so
    body paragraphs inherit it; the heading styles (``basedOn`` Normal and
    without their own ``jc``) are explicitly pinned left so wrapped chapter
    headings never inherit justified spacing.
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    document = Document(str(path))
    width_cm, height_cm = _PAGE_SIZE_CM[fmt.paper_size]
    for section in document.sections:
        section.page_width = Cm(width_cm)
        section.page_height = Cm(height_cm)
        section.top_margin = Cm(fmt.margin_cm["top"])
        section.right_margin = Cm(fmt.margin_cm["right"])
        section.bottom_margin = Cm(fmt.margin_cm["bottom"])
        section.left_margin = Cm(fmt.margin_cm["left"])

    normal = document.styles["Normal"]
    normal.font.name = fmt.font_family
    normal.font.size = Pt(fmt.font_size_pt)
    normal.paragraph_format.line_spacing = fmt.line_spacing
    if fmt.justify:
        normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        # Heading styles are basedOn Normal and carry no own jc, so they would
        # inherit justification and stretch wrapped chapter headings. Pin them
        # left; absent styles (custom templates) are simply skipped.
        for name in ("Heading 1", "Heading 2", "Heading 3"):
            try:
                heading = document.styles[name]
            except KeyError:
                continue
            heading.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    document.save(str(path))


def _format_cm(value_cm: float) -> str:
    """Format a centimetre value for LaTeX, dropping a trailing ``.0``."""
    return f"{value_cm:g}"


def latex_metadata(fmt: FormattingSpec) -> dict[str, str]:
    """Pandoc ``--metadata`` values for the LaTeX fallback.

    Keys match the variables a Pandoc LaTeX template reads, so templates stay
    unchanged and only the exported metadata varies per style.
    """
    margins = fmt.margin_cm
    geometry = ",".join(
        f"{side}={_format_cm(margins[side])}cm" for side in ("top", "right", "bottom", "left")
    )
    return {
        "geometry": geometry,
        "linestretch": str(fmt.line_spacing),
        "fontsize": f"{int(fmt.font_size_pt)}pt",
    }


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_BAB_PREFIX_RE = re.compile(r"^\s*bab\s+[ivxlcdm]+\b", re.IGNORECASE)
_H1_RE = re.compile(r"^[ \t]*#(?!#)[ \t]*(.+?)[ \t]*$")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def _heading_key(text: str) -> str:
    """Loose comparison key: no ``BAB <roman>`` prefix, punctuation or spaces."""
    return _NON_ALNUM_RE.sub("", _BAB_PREFIX_RE.sub("", text)).casefold()


def normalize_chapter_markdown(markdown: str, section: SectionSpec) -> str:
    """Make a chapter's leading heading match the outline and drop guidance comments.

    A leading ``# ...`` that loosely matches the section title or id (ignoring a
    ``BAB <roman numeral>`` prefix, case, punctuation and spacing) is rewritten to
    the canonical ``# {section.title}`` so exported chapters never carry ad-hoc
    headings. An unmatched heading is preserved, and a draft without any leading
    H1 gets one prepended. HTML comments from the skeleton guidance are stripped
    so they cannot leak into an export.
    """
    lines = _HTML_COMMENT_RE.sub("", markdown).split("\n")
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    lines = lines[start:]
    if not lines:
        return f"# {section.title}\n\n"

    match = _H1_RE.match(lines[0])
    if match is None:
        return f"# {section.title}\n\n" + "\n".join(lines)

    heading_key = _heading_key(match.group(1))
    targets = {_heading_key(section.title), _heading_key(section.id)}
    if heading_key and heading_key in targets:
        lines[0] = f"# {section.title}"
    return "\n".join(lines)
