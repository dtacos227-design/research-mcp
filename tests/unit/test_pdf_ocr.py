"""PDF hasil scan harus menghasilkan teks lewat backend OCR default (RapidOCR)."""

from pathlib import Path

import pymupdf

from research_mcp.parsers import pdf_extractor

DIGITAL_TEXT = "Parking occupancy detection with YOLO and RT-DETR on video."


def _make_digital_pdf(path: Path, text: str = DIGITAL_TEXT) -> Path:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()
    return path


def _make_scanned_pdf(path: Path, text: str = "PARKING OCCUPANCY DETECTION") -> Path:
    """Halaman tanpa text layer: teks dirender ke pixmap lalu ditempel sebagai gambar."""
    source = pymupdf.open()
    source_page = source.new_page(width=595, height=842)
    source_page.insert_text((80, 200), text, fontsize=36)
    pixmap = source_page.get_pixmap(dpi=150)

    scan = pymupdf.open()
    scan_page = scan.new_page(width=595, height=842)
    scan_page.insert_image(scan_page.rect, pixmap=pixmap)
    scan.save(path)
    scan.close()
    source.close()
    return path


def test_scanned_pdf_is_ocred_by_default_backend(tmp_path: Path, monkeypatch):
    project_dir = tmp_path / "project"
    (project_dir / "pdfs").mkdir(parents=True)
    pdf_path = _make_scanned_pdf(project_dir / "pdfs" / "scan.pdf")
    # Isolasi: test ini hanya membuktikan backend default (RapidOCR) bekerja.
    monkeypatch.setenv("RESEARCH_MCP_OCR_FALLBACK", "false")

    markdown = pdf_extractor.extract_markdown(pdf_path, project_dir)

    assert "PARKING" in markdown.upper()


def test_digital_text_pdf_is_unchanged(tmp_path: Path):
    project_dir = tmp_path / "project"
    (project_dir / "pdfs").mkdir(parents=True)
    pdf_path = _make_digital_pdf(project_dir / "pdfs" / "paper.pdf")

    markdown = pdf_extractor.extract_markdown(pdf_path, project_dir)

    # Teks digital jauh di atas ambang OCR_MIN_TEXT_CHARS: tidak disentuh lapis kedua.
    assert markdown.count(DIGITAL_TEXT) == 1
