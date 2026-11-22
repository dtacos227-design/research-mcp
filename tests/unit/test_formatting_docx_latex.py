"""DOCX/LaTeX rendering of ``FormattingSpec`` and chapter heading normalization.

The DOCX and LaTeX paths are the fallback when WordCraft is unavailable, so the
same ``FormattingSpec`` must land in both: A4 with centimetre margins, the
Normal style's font/spacing/justification for DOCX, and Pandoc ``geometry`` /
``linestretch`` / ``fontsize`` metadata for LaTeX.

``normalize_chapter_markdown`` follows controller ruling R3: a leading ``# ...``
that loosely matches the section title (or id) is rewritten to the outline's
canonical ``# {section.title}`` -- never demoted -- and guidance HTML comments
never reach an export.
"""

from pathlib import Path

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from research_mcp.exporters import docx_polisher, formatting
from research_mcp.styles.models import FormattingSpec, SectionSpec


def _section(**overrides) -> SectionSpec:
    values = {"id": "pendahuluan", "title": "Pendahuluan", "filename": "10.md", "order": 1}
    values.update(overrides)
    return SectionSpec(**values)


def _blank_docx(tmp_path: Path) -> Path:
    path = tmp_path / "d.docx"
    document = Document()
    document.add_paragraph("x")
    document.save(str(path))
    return path


# --- DOCX -----------------------------------------------------------------


def test_apply_docx_sets_a4_and_margins(tmp_path):
    path = _blank_docx(tmp_path)
    ft = FormattingSpec(margin_cm={"top": 3, "right": 3, "bottom": 3, "left": 4})

    formatting.apply_docx_formatting(path, ft)

    section = Document(str(path)).sections[0]
    assert round(section.left_margin.cm, 1) == 4.0
    assert round(section.top_margin.cm, 1) == 3.0
    assert round(section.right_margin.cm, 1) == 3.0
    assert round(section.bottom_margin.cm, 1) == 3.0
    assert round(section.page_width.cm, 1) == 21.0
    assert round(section.page_height.cm, 1) == 29.7


def test_apply_docx_sets_normal_style_font_spacing_and_justify(tmp_path):
    path = _blank_docx(tmp_path)
    ft = FormattingSpec(
        font_family="Liberation Serif",
        font_size_pt=11,
        line_spacing=2.0,
        justify=True,
    )

    formatting.apply_docx_formatting(path, ft)

    normal = Document(str(path)).styles["Normal"]
    assert normal.font.name == "Liberation Serif"
    assert normal.font.size.pt == 11
    assert normal.paragraph_format.line_spacing == 2.0
    assert normal.paragraph_format.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY


def test_apply_docx_leaves_alignment_alone_when_not_justified(tmp_path):
    path = _blank_docx(tmp_path)
    formatting.apply_docx_formatting(path, FormattingSpec(justify=False))

    normal = Document(str(path)).styles["Normal"]
    assert normal.paragraph_format.alignment != WD_ALIGN_PARAGRAPH.JUSTIFY


def test_apply_docx_saves_in_place(tmp_path):
    path = _blank_docx(tmp_path)
    formatting.apply_docx_formatting(path, FormattingSpec())
    # Re-opening the same path proves the edit was saved, not only applied to RAM.
    assert round(Document(str(path)).sections[0].page_width.cm, 1) == 21.0


def test_docx_polisher_exposes_apply_formatting(tmp_path):
    path = _blank_docx(tmp_path)
    docx_polisher.apply_formatting(
        path, FormattingSpec(margin_cm={"top": 3, "right": 3, "bottom": 3, "left": 4})
    )

    assert round(Document(str(path)).sections[0].left_margin.cm, 1) == 4.0


def test_polish_docx_still_sets_title(tmp_path):
    path = _blank_docx(tmp_path)
    docx_polisher.polish_docx(path, "My Thesis")
    assert Document(str(path)).core_properties.title == "My Thesis"


# --- LaTeX ----------------------------------------------------------------


def test_latex_metadata_has_geometry_and_linestretch():
    ft = FormattingSpec(margin_cm={"top": 3, "right": 3, "bottom": 3, "left": 4}, line_spacing=2.0)

    meta = formatting.latex_metadata(ft)

    assert "4cm" in meta["geometry"]
    assert meta["linestretch"] == "2.0"


def test_latex_metadata_geometry_orders_margins_and_trims_floats():
    ft = FormattingSpec(
        margin_cm={"top": 2.5, "right": 2.5, "bottom": 3.0, "left": 4.0},
        font_size_pt=11,
    )

    meta = formatting.latex_metadata(ft)

    assert meta["geometry"] == "top=2.5cm,right=2.5cm,bottom=3cm,left=4cm"
    assert meta["fontsize"] == "11pt"


# --- Heading normalization (R3) ------------------------------------------


def test_normalize_rewrites_loose_title_match_to_canonical_h1():
    section = _section(id="pendahuluan", title="BAB I Pendahuluan")

    out = formatting.normalize_chapter_markdown("# Pendahuluan\n\nIsi.", section)

    assert out.startswith("# BAB I Pendahuluan")
    assert out.count("# BAB I Pendahuluan") == 1
    assert "## " not in out
    assert "Isi." in out


def test_normalize_matches_bab_prefixed_heading_against_plain_title():
    section = _section(id="pendahuluan", title="Pendahuluan")

    out = formatting.normalize_chapter_markdown("# BAB II Pendahuluan\n\nIsi.", section)

    assert out.startswith("# Pendahuluan")
    assert "Isi." in out


def test_normalize_matches_section_id_case_insensitively():
    section = _section(id="metodologi", title="Metode Penelitian")

    out = formatting.normalize_chapter_markdown("# METODOLOGI\n\nIsi.", section)

    assert out.startswith("# Metode Penelitian")
    assert "Isi." in out


def test_normalize_keeps_unmatched_heading():
    section = _section(id="b1", title="Metode")

    out = formatting.normalize_chapter_markdown("# Lain\n\nIsi.", section)

    assert out.startswith("# Lain")
    assert "# Metode" not in out


def test_normalize_prepends_title_when_no_leading_h1():
    section = _section(id="abstrak", title="Abstrak")

    out = formatting.normalize_chapter_markdown("Isi tanpa judul.", section)

    assert out.startswith("# Abstrak\n\n")
    assert "Isi tanpa judul." in out


def test_normalize_prepends_title_when_only_h2_leads():
    section = _section(id="abstrak", title="Abstrak")

    out = formatting.normalize_chapter_markdown("## Subjudul\n\nIsi.", section)

    assert out.startswith("# Abstrak\n\n## Subjudul")


def test_normalize_strips_single_line_html_comment():
    section = _section(id="pendahuluan", title="Pendahuluan")

    out = formatting.normalize_chapter_markdown(
        "# Pendahuluan\n\n<!-- tulis 3 paragraf -->\n\nIsi.", section
    )

    assert "<!--" not in out
    assert "tulis 3 paragraf" not in out
    assert "Isi." in out


def test_normalize_strips_multiline_html_comment():
    section = _section(id="pendahuluan", title="Pendahuluan")

    out = formatting.normalize_chapter_markdown(
        "# Pendahuluan\n\n<!--\nPanduan:\n- satu\n- dua\n-->\n\nIsi.", section
    )

    assert "-->" not in out
    assert "Panduan:" not in out
    assert "Isi." in out


def test_normalize_comment_only_leading_heading_is_still_recognized():
    section = _section(id="pendahuluan", title="Pendahuluan")

    out = formatting.normalize_chapter_markdown("<!-- catatan -->\n# Pendahuluan\n\nIsi.", section)

    assert out.startswith("# Pendahuluan")
    assert "catatan" not in out


@pytest.mark.parametrize("heading", ["#BAB I Pendahuluan", "#   Pendahuluan   "])
def test_normalize_handles_spacing_variants_of_leading_h1(heading):
    section = _section(id="pendahuluan", title="Pendahuluan")

    out = formatting.normalize_chapter_markdown(f"{heading}\n\nIsi.", section)

    assert out.startswith("# Pendahuluan")
    assert "Isi." in out
