"""PDF hasil scan harus menghasilkan teks lewat backend OCR default (RapidOCR),
dengan Tesseract sebagai lapis kedua untuk halaman yang teksnya tipis.
"""

from pathlib import Path

import pymupdf
import pytest

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


# --- Lapis kedua: Tesseract untuk halaman tipis -------------------------------


@pytest.fixture
def ocr_case(tmp_path: Path):
    """(project_dir, pdf_path) valid untuk extract_markdown; isi chunk di-stub per test."""
    project_dir = tmp_path / "project"
    (project_dir / "pdfs").mkdir(parents=True)
    return project_dir, _make_scanned_pdf(project_dir / "pdfs" / "scan.pdf")


def _stub_pages(monkeypatch, texts: list[str]) -> None:
    """Ganti output parser dengan page chunks buatan.

    Dipakai agar tiap kasus wiring bisa dipaku persis (panjang teks, jumlah
    halaman) tanpa bergantung pada hasil OCR RapidOCR yang bervariasi.
    """
    chunks = [
        {"metadata": {"page_number": i + 1, "page_count": len(texts)}, "toc_items": [], "text": t}
        for i, t in enumerate(texts)
    ]
    monkeypatch.setattr(
        pdf_extractor.pymupdf4llm,
        "to_markdown",
        lambda path, page_chunks=True, show_progress=False: chunks,
    )


def _stub_tesseract(monkeypatch, pages: dict[int, str] | None = None, calls: list | None = None):
    """Pasang `_tesseract_available` = True dan `_ocr_pages` yang merekam target halaman."""
    monkeypatch.setattr(pdf_extractor, "_tesseract_available", lambda: True)

    def fake(path, page_numbers, language):
        if calls is not None:
            calls.append(page_numbers)
        return {n: (pages or {}).get(n, "") for n in page_numbers}

    monkeypatch.setattr(pdf_extractor, "_ocr_pages", fake)


DIGITAL_LONG = "Digital methods section stays intact and is long enough."


def test_thin_page_is_replaced_by_tesseract_text(ocr_case, monkeypatch):
    project_dir, pdf_path = ocr_case
    _stub_pages(monkeypatch, [DIGITAL_LONG, ""])
    _stub_tesseract(monkeypatch, pages={2: "TESSERACT OCR TEXT"})

    markdown = pdf_extractor.extract_markdown(pdf_path, project_dir)

    assert "TESSERACT OCR TEXT" in markdown
    assert DIGITAL_LONG in markdown


def test_fallback_disabled_never_calls_tesseract(ocr_case, monkeypatch):
    project_dir, pdf_path = ocr_case
    _stub_pages(monkeypatch, ["", ""])
    calls: list[list[int]] = []
    _stub_tesseract(monkeypatch, calls=calls)
    monkeypatch.setenv("RESEARCH_MCP_OCR_FALLBACK", "false")

    pdf_extractor.extract_markdown(pdf_path, project_dir)

    assert calls == []


def test_missing_tesseract_keeps_default_backend_output(ocr_case, monkeypatch):
    project_dir, pdf_path = ocr_case
    _stub_pages(monkeypatch, ["RAPID OCR TEXT"])
    calls: list[list[int]] = []
    _stub_tesseract(monkeypatch, calls=calls)
    monkeypatch.setattr(pdf_extractor, "_tesseract_available", lambda: False)

    markdown = pdf_extractor.extract_markdown(pdf_path, project_dir)

    assert "RAPID OCR TEXT" in markdown  # hasil backend default tetap dipakai
    assert calls == []


def test_tesseract_failure_is_swallowed(ocr_case, monkeypatch, capfd):
    project_dir, pdf_path = ocr_case
    _stub_pages(monkeypatch, ["RAPID OCR TEXT"])
    monkeypatch.setattr(pdf_extractor, "_tesseract_available", lambda: True)

    def boom(path, page_numbers, language):
        raise RuntimeError("Failed to load language 'ind'")

    monkeypatch.setattr(pdf_extractor, "_ocr_pages", boom)

    markdown = pdf_extractor.extract_markdown(pdf_path, project_dir)

    assert "RAPID OCR TEXT" in markdown
    assert "Failed to load language" not in capfd.readouterr().out


def test_blank_pages_are_attempted_once(ocr_case, monkeypatch):
    project_dir, pdf_path = ocr_case
    _stub_pages(monkeypatch, ["", ""])
    calls: list[list[int]] = []
    _stub_tesseract(monkeypatch, pages={1: "", 2: ""}, calls=calls)

    pdf_extractor.extract_markdown(pdf_path, project_dir)

    assert calls == [[1, 2]]  # satu percobaan per halaman, tanpa loop ulang


def test_mixed_pages_ocrs_only_the_thin_page(ocr_case, monkeypatch):
    project_dir, pdf_path = ocr_case
    _stub_pages(monkeypatch, [DIGITAL_LONG, ""])
    calls: list[list[int]] = []
    _stub_tesseract(monkeypatch, pages={2: "SCAN PAGE TEXT"}, calls=calls)

    markdown = pdf_extractor.extract_markdown(pdf_path, project_dir)

    assert calls == [[2]]  # halaman 1 ber-teks digital, tidak di-OCR ulang
    assert "SCAN PAGE TEXT" in markdown
    assert markdown.count(DIGITAL_LONG) == 1


def test_tesseract_available_is_false_when_tessdata_missing(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("No tessdata specified and Tesseract is not installed")

    monkeypatch.setattr(pdf_extractor.pymupdf, "get_tessdata", boom)
    pdf_extractor._tesseract_available.cache_clear()
    try:
        assert pdf_extractor._tesseract_available() is False
    finally:
        pdf_extractor._tesseract_available.cache_clear()


class _FakeTextPage:
    """Pengganti TextPage agar test tidak butuh Tesseract terpasang."""

    def extractText(self) -> str:  # noqa: N802 - API PyMuPDF
        return "OCR OVERWRITE"


@pytest.mark.parametrize("text", ["E = mc2 and F = ma", "— 7 —"])
def test_short_digital_page_is_not_rewritten(ocr_case, monkeypatch, text):
    """Halaman ber-text layer tidak boleh di-OCR ulang walau teksnya < ambang."""
    project_dir, pdf_path = ocr_case
    _make_digital_pdf(pdf_path, text)
    _stub_pages(monkeypatch, [text])
    monkeypatch.setattr(pdf_extractor, "_tesseract_available", lambda: True)
    calls: list[dict] = []
    monkeypatch.setattr(
        pymupdf.Page,
        "get_textpage_ocr",
        lambda self, **kwargs: calls.append(kwargs) or _FakeTextPage(),
    )

    markdown = pdf_extractor.extract_markdown(pdf_path, project_dir)

    assert calls == []  # halaman punya text layer → tidak disentuh
    assert markdown == text


def test_ocr_pages_passes_language_dpi_and_page_index(ocr_case, monkeypatch):
    """Kontrak `_ocr_pages`: dpi 300, full page, bahasa dari setting, indeks 1-based."""
    _, pdf_path = ocr_case
    seen: list[tuple[int, dict]] = []
    monkeypatch.setattr(
        pymupdf.Page,
        "get_textpage_ocr",
        lambda self, **kwargs: seen.append((self.number, kwargs)) or _FakeTextPage(),
    )

    pages = pdf_extractor._ocr_pages(pdf_path, [1], "eng")

    assert seen == [(0, {"language": "eng", "dpi": 300, "full": True})]
    assert pages == {1: "OCR OVERWRITE"}


@pytest.mark.skipif(
    not pdf_extractor._tesseract_available(), reason="Tesseract/tessdata not installed"
)
def test_live_tesseract_ocred_scanned_page(ocr_case):
    _, pdf_path = ocr_case

    pages = pdf_extractor._ocr_pages(pdf_path, [1], "ind+eng")

    assert "PARKING" in pages[1].upper()
