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

# Page-number placement -> (python-docx header/footer part, alignment name).
_DOCX_PAGE_NUMBER_POSITIONS: dict[str, tuple[str, str]] = {
    "footer_center": ("footer", "CENTER"),
    "footer_right": ("footer", "RIGHT"),
    "header_right": ("header", "RIGHT"),
}

# Pandoc's bundled `article_id.tex` composes `$papersize$paper`, so feed it the
# bare class option (`a4` -> `a4paper`). `ieee.tex` has no papersize variable.
_LATEX_PAPER_SIZE: dict[str, str] = {"A4": "a4", "Letter": "letter"}

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


def _add_page_number_field(paragraph, alignment: str) -> None:
    """Write a live ``PAGE`` field into a header/footer paragraph."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    paragraph.alignment = getattr(WD_ALIGN_PARAGRAPH, alignment)
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for element in (begin, instruction, separate, placeholder, end):
        run._r.append(element)


def _set_page_number_format(section, page_format: str) -> None:
    """Set the section's ``w:pgNumType`` numeral format (decimal/lowerRoman/...).

    python-docx has no API for this attribute, so the ``sectPr`` element is
    edited directly; Word and LibreOffice both honor ``w:fmt``.
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    sect_pr = section._sectPr
    pg_num_type = sect_pr.find(qn("w:pgNumType"))
    if pg_num_type is None:
        pg_num_type = OxmlElement("w:pgNumType")
        # `w:sectPr` is an ordered schema: pgNumType belongs right before cols.
        sect_pr.insert_element_before(
            pg_num_type,
            "w:cols",
            "w:formProt",
            "w:vAlign",
            "w:noEndnote",
            "w:titlePg",
            "w:textDirection",
            "w:bidi",
            "w:rtlGutter",
            "w:docGrid",
            "w:printerSettings",
            "w:sectPrChange",
        )
    pg_num_type.set(qn("w:fmt"), page_format)


def _set_columns(section, count: int) -> None:
    """Set the section's column count; python-docx exposes no column API."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    sect_pr = section._sectPr
    columns = sect_pr.find(qn("w:cols"))
    if columns is None:
        columns = OxmlElement("w:cols")
        # `w:sectPr` is an ordered schema: cols belongs right after pgNumType.
        sect_pr.insert_element_before(
            columns,
            "w:formProt",
            "w:vAlign",
            "w:noEndnote",
            "w:titlePg",
            "w:textDirection",
            "w:bidi",
            "w:rtlGutter",
            "w:docGrid",
            "w:printerSettings",
            "w:sectPrChange",
        )
    columns.set(qn("w:num"), str(count))
    columns.set(qn("w:space"), "720")


def apply_docx_formatting(
    path: Path,
    fmt: FormattingSpec,
    body_start_heading: str | None = None,
) -> None:
    """Apply paper size, margins, typography, numbering and columns to a `.docx`.

    Pandoc's ``--reference-doc`` does not carry page margins reliably, so the
    exporter re-applies them here. Justification goes on the ``Normal`` style so
    body paragraphs inherit it; the heading styles (``basedOn`` Normal and
    without their own ``jc``) are explicitly pinned left so wrapped chapter
    headings never inherit justified spacing. Page numbers are inserted as a
    live ``PAGE`` field in the requested header/footer slot with the section's
    ``w:pgNumType`` numeral format, and ``two_column`` sets ``w:cols/@w:num``.
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    document = Document(str(path))
    width_cm, height_cm = _PAGE_SIZE_CM[fmt.paper_size]
    placement = _DOCX_PAGE_NUMBER_POSITIONS.get(fmt.page_number_position)
    for section in document.sections:
        section.page_width = Cm(width_cm)
        section.page_height = Cm(height_cm)
        section.top_margin = Cm(fmt.margin_cm["top"])
        section.right_margin = Cm(fmt.margin_cm["right"])
        section.bottom_margin = Cm(fmt.margin_cm["bottom"])
        section.left_margin = Cm(fmt.margin_cm["left"])
        if placement is not None:
            part, alignment = placement
            container = section.header if part == "header" else section.footer
            container.is_linked_to_previous = False
            _add_page_number_field(container.paragraphs[0], alignment)
            _set_page_number_format(section, fmt.page_number_format)
        if fmt.two_column:
            _set_columns(section, 2)

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
    if fmt.heading_numbering == "roman_bab":
        _uppercase_chapter_headings(document)
    if fmt.front_matter_page_number_format and body_start_heading:
        _split_front_matter(document, body_start_heading, fmt.front_matter_page_number_format)
    _request_toc_update(document)
    document.save(str(path))


def _uppercase_chapter_headings(document) -> None:
    """`BAB I Pendahuluan` -> `BAB I PENDAHULUAN`, the Indonesian thesis convention."""
    for paragraph in document.paragraphs:
        if paragraph.style.name != "Heading 1":
            continue
        text = paragraph.text.strip()
        if text.upper().startswith("BAB ") and not text.isupper():
            paragraph.text = text.upper()


def _set_section_page_numbers(section, number_format: str, start: int) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    sect_pr = section._sectPr
    existing = sect_pr.find(qn("w:pgNumType"))
    if existing is not None:
        sect_pr.remove(existing)
    pg_num_type = OxmlElement("w:pgNumType")
    pg_num_type.set(qn("w:fmt"), number_format)
    pg_num_type.set(qn("w:start"), str(start))
    sect_pr.insert_element_before(
        pg_num_type,
        "w:cols",
        "w:formProt",
        "w:vAlign",
        "w:noEndnote",
        "w:titlePg",
        "w:textDirection",
        "w:bidi",
        "w:rtlGutter",
        "w:docGrid",
        "w:printerSettings",
        "w:sectPrChange",
    )


def _split_front_matter(document, heading_text: str, front_format: str) -> bool:
    """Start the body on its own section so front matter keeps Roman numerals.

    Returns True when the split happened. The body section restarts at 1 with
    decimal numbering, which is the Indonesian thesis convention.
    """
    from copy import deepcopy

    from docx.oxml import OxmlElement

    target = None
    wanted = heading_text.strip().casefold()
    for paragraph in document.paragraphs:
        if paragraph.style.name != "Heading 1":
            continue
        if paragraph.text.strip().casefold().startswith(wanted):
            target = paragraph
            break
    if target is None:
        return False

    front = deepcopy(document.sections[-1]._sectPr)
    holder = OxmlElement("w:p")
    holder_p_pr = OxmlElement("w:pPr")
    holder_p_pr.append(front)
    holder.append(holder_p_pr)
    target._element.addprevious(holder)
    target.paragraph_format.page_break_before = False

    sections = document.sections
    if len(sections) < 2:
        return False
    _set_section_page_numbers(sections[0], front_format, 1)
    _set_section_page_numbers(sections[1], "decimal", 1)
    return True


def _request_toc_update(document) -> None:
    """Ask Word to refresh a TOC field on open so the list is never stale."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    has_toc = any(
        "TOC" in (node.text or "") for node in document.element.body.iter(qn("w:instrText"))
    )
    if not has_toc:
        return
    settings = document.settings.element
    if settings.find(qn("w:updateFields")) is not None:
        return
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    settings.append(update)


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
        "papersize": _LATEX_PAPER_SIZE[fmt.paper_size],
        "geometry": geometry,
        "linestretch": str(fmt.line_spacing),
        "fontsize": f"{int(fmt.font_size_pt)}pt",
    }


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_BAB_PREFIX_RE = re.compile(r"^\s*bab\s+[ivxlcdm]+\b", re.IGNORECASE)
_H1_RE = re.compile(r"^[ \t]*#(?!#)[ \t]*(.+?)[ \t]*$")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def strip_guidance_comments(markdown: str) -> str:
    """Remove HTML comment blocks (skeleton guidance) from Markdown text."""
    return _HTML_COMMENT_RE.sub("", markdown)


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
    lines = strip_guidance_comments(markdown).split("\n")
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
