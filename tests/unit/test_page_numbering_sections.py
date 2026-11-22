"""Front matter / body page numbering and TOC field hardening."""

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from research_mcp.exporters.formatting import apply_docx_formatting
from research_mcp.styles.registry import get_style


def _document_with_body(tmp_path):
    path = tmp_path / "doc.docx"
    document = Document()
    document.add_heading("Sampul", level=1)
    document.add_paragraph("Judul skripsi")
    document.add_heading("BAB I Pendahuluan", level=1)
    document.add_paragraph("Isi bab satu.")
    document.save(path)
    return path


def _number_formats(path):
    document = Document(str(path))
    formats, starts = [], []
    for section in document.sections:
        pg_num = section._sectPr.find(qn("w:pgNumType"))
        formats.append(None if pg_num is None else pg_num.get(qn("w:fmt")))
        starts.append(None if pg_num is None else pg_num.get(qn("w:start")))
    return len(document.sections), formats, starts


def test_skripsi_style_splits_front_matter_from_body(tmp_path):
    path = _document_with_body(tmp_path)
    style = get_style("skripsi_s1")

    apply_docx_formatting(path, style.formatting, body_start_heading="BAB I Pendahuluan")

    count, formats, starts = _number_formats(path)
    assert count == 2
    assert formats == ["lowerRoman", "decimal"]
    assert starts == ["1", "1"]


def test_style_without_front_format_keeps_single_section(tmp_path):
    path = _document_with_body(tmp_path)
    style = get_style("article_imrad")

    apply_docx_formatting(path, style.formatting, body_start_heading="BAB I Pendahuluan")

    count, _formats, _starts = _number_formats(path)
    assert count == 1


def test_missing_body_heading_keeps_single_section(tmp_path):
    path = _document_with_body(tmp_path)
    style = get_style("skripsi_s1")

    apply_docx_formatting(path, style.formatting, body_start_heading="BAB IX Tidak Ada")

    count, _formats, _starts = _number_formats(path)
    assert count == 1


def _document_with_toc(tmp_path, with_toc: bool):
    path = tmp_path / ("toc.docx" if with_toc else "no_toc.docx")
    document = Document()
    if with_toc:
        paragraph = document.add_paragraph("Daftar Isi")
        run = paragraph.add_run()
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = 'TOC \\o "1-3" \\h \\z \\u'
        run._r.append(instr)
    document.add_heading("BAB I Pendahuluan", level=1)
    document.add_paragraph("Isi.")
    document.save(path)
    return path


def test_toc_field_requests_update_on_open(tmp_path):
    path = _document_with_toc(tmp_path, with_toc=True)

    apply_docx_formatting(path, get_style("article_imrad").formatting)

    settings = Document(str(path)).settings.element
    assert settings.find(qn("w:updateFields")) is not None


def test_document_without_toc_does_not_force_update(tmp_path):
    path = _document_with_toc(tmp_path, with_toc=False)

    apply_docx_formatting(path, get_style("article_imrad").formatting)

    settings = Document(str(path)).settings.element
    assert settings.find(qn("w:updateFields")) is None


def test_skripsi_style_uppercases_chapter_headings(tmp_path):
    path = _document_with_body(tmp_path)

    apply_docx_formatting(
        path, get_style("skripsi_s1").formatting, body_start_heading="BAB I Pendahuluan"
    )

    headings = [p.text for p in Document(str(path)).paragraphs if p.style.name == "Heading 1"]
    assert "BAB I PENDAHULUAN" in headings


def test_article_style_keeps_chapter_heading_case(tmp_path):
    path = _document_with_body(tmp_path)

    apply_docx_formatting(path, get_style("article_imrad").formatting)

    headings = [p.text for p in Document(str(path)).paragraphs if p.style.name == "Heading 1"]
    assert "BAB I Pendahuluan" in headings
