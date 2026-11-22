# OCR Backend (RapidOCR + Tesseract) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PDF hasil scan menghasilkan teks (RapidOCR selalu aktif, Tesseract sebagai lapis kedua berbahasa `ind+eng`), tanpa warning `RapidOCR is missing`.

**Architecture:** RapidOCR masuk dependency utama sehingga `pymupdf4llm.select_ocr_function()` menemukan backend sendiri dan meng-OCR halaman tanpa text layer. Karena paket baru `rapidocr` membuat `pymupdf4llm` early-return (Tesseract tidak pernah dipanggil), `pdf_extractor.extract_markdown()` menambahkan lapis kedua sendiri: halaman dengan teks hasil ekstraksi < 40 karakter di-OCR ulang lewat `page.get_textpage_ocr(language=…)`, dan hasilnya menggantikan teks chunk halaman itu.

**Tech Stack:** Python 3.13, `pymupdf` 2.1, `pymupdf4llm` 2.1, `rapidocr>=3.10.0` (ONNX, offline), Tesseract 5 (opsional, sistem), pytest.

**Spec:** `docs/design/2026-11-23-ocr-backends-design.md`

## Global Constraints

- Python **3.13.15** — `rapidocr-onnxruntime` (`requires_python <3.13`) **tidak boleh** dipakai/dipertimbangkan.
- `rapidocr>=3.10.0` masuk `[project] dependencies` (bukan optional extra).
- Tesseract **opsional**: seluruh kode harus tetap jalan bila `pymupdf.get_tessdata()` gagal.
- Default bahasa OCR `ind+eng`; default fallback `true`.
- Semua chatter OCR/parser WAJIB lewat `_parser_messages_to_stderr()`; stdout hanya JSON-RPC.
- `extract_markdown(pdf_path: Path, project_dir: Path) -> str` — signature tidak berubah (dipanggil `asyncio.to_thread` dari `ingestion.py:386` dan `:502`).
- Baseline suite: **562 passed, 1 skipped** — harus tetap hijau.

## Review Focus

- PDF campuran (satu halaman scan + sisanya teks digital): hanya halaman tipis yang di-OCR ulang; teks digital tidak boleh berubah/duplikat.
- PDF rusak, terenkripsi, atau halaman kosong: tidak boleh ada exception yang lolos ke tool.
- `RESEARCH_MCP_OCR_FALLBACK=false`: nol pemanggilan Tesseract (latency tidak bertambah).
- Halaman yang teks OCR-nya sendiri tetap <40 karakter (mis. cover kosong): tidak boleh retry berulang.
- `ind` tidak ada di tessdata (hanya `eng`): Tesseract gagal per halaman → ditelan, hasil RapidOCR tetap dipakai.

---

### Task 1: RapidOCR sebagai dependency + backend terpilih

**Files:**
- Modify: `pyproject.toml` (`[project] dependencies`), `uv.lock`
- Modify: `README.md` (bagian prasyarat OCR)
- Create: `tests/unit/test_ocr_backend.py`

**Interfaces:**
- Consumes: tidak ada.
- Produces: paket `rapidocr` terpasang; `pymupdf4llm.helpers.document_layout.select_ocr_function()` mengembalikan callable dan tidak lagi mencetak "RapidOCR is missing".

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_ocr_backend.py
from pathlib import Path


def test_pymupdf4llm_selects_rapidocr_without_missing_warning():
    from pymupdf4llm.helpers import document_layout

    document_layout.INFO_MESSAGES.seek(0)
    document_layout.INFO_MESSAGES.truncate(0)
    ocr_function = document_layout.select_ocr_function()
    dump = document_layout.INFO_MESSAGES.getvalue()

    assert callable(ocr_function)
    assert "RapidOCR is missing" not in dump
    assert "Using RapidOCR" in dump


def test_rapidocr_ships_onnx_models_for_offline_use():
    import rapidocr

    models = Path(rapidocr.__file__).parent / "models"
    names = {p.name for p in models.glob("*.onnx")}
    assert "PP-OCRv6_det_small.onnx" in names
    assert "PP-OCRv6_rec_small.onnx" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_ocr_backend.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rapidocr'`

- [ ] **Step 3: Add the dependency**

Run: `uv add "rapidocr>=3.10.0"`
Expected: `pyproject.toml` mendapat `rapidocr>=3.10.0`, `uv.lock` diperbarui, venv ter-sync (`rapidocr`, `opencv-python-headless`, `pyclipper`, `shapely`, `omegaconf`, `colorlog`, `tqdm`).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_ocr_backend.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Document the dependency in README**

Tambahkan setelah blok "Instalasi Cepat" (README.md:74) satu paragraf: `uv sync` sudah memasang RapidOCR (ONNX PP-OCRv6, offline) sehingga PDF hasil scan ter-OCR otomatis; tidak ada unduhan model saat runtime.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock README.md tests/unit/test_ocr_backend.py
git commit -m "feat(parsers): ship RapidOCR as the default OCR backend"
```

---

### Task 2: Setting OCR (bahasa + fallback)

**Files:**
- Modify: `src/research_mcp/config.py` (class `Settings`)
- Modify: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: tidak ada.
- Produces: `Settings.ocr_language: str` (default `"ind+eng"`), `Settings.ocr_fallback: bool` (default `True`) — dibaca Task 4 sebagai `get_settings().ocr_language` / `.ocr_fallback`.

- [ ] **Step 1: Write the failing test**

```python
# tambahkan di tests/unit/test_config.py
def test_ocr_language_defaults_to_ind_eng(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RESEARCH_MCP_OCR_LANGUAGE", raising=False)
    assert Settings().ocr_language == "ind+eng"


def test_ocr_language_read_from_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RESEARCH_MCP_OCR_LANGUAGE", "eng")
    assert Settings().ocr_language == "eng"


def test_ocr_fallback_defaults_true_and_reads_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RESEARCH_MCP_OCR_FALLBACK", raising=False)
    assert Settings().ocr_fallback is True

    monkeypatch.setenv("RESEARCH_MCP_OCR_FALLBACK", "false")
    assert Settings().ocr_fallback is False
```

`monkeypatch.chdir(tmp_path)` memastikan `.env` di root repo tidak bocor ke test (file `tests/unit/test_config.py` sudah memakai pola yang sama).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_config.py -v -k ocr`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'ocr_language'`

- [ ] **Step 3: Implement the settings**

Di `Settings`, setelah `log_level`:

```python
    ocr_language: str = Field(default="ind+eng", validation_alias="RESEARCH_MCP_OCR_LANGUAGE")
    ocr_fallback: bool = Field(default=True, validation_alias="RESEARCH_MCP_OCR_FALLBACK")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_config.py -v -k ocr`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/config.py tests/unit/test_config.py
git commit -m "feat(config): add OCR language and fallback settings"
```

---

### Task 3: PDF scan ter-OCR oleh backend default (regresi + perilaku baru)

**Files:**
- Create: `tests/unit/test_pdf_ocr.py`
- (Tidak ada perubahan kode produksi di task ini — membuktikan Task 1 cukup untuk halaman scan.)

**Interfaces:**
- Consumes: `pdf_extractor.extract_markdown` (signature tetap), `rapidocr` dari Task 1.
- Produces: helper fixture `_make_scanned_pdf(path: Path, text: str = "PARKING OCCUPANCY DETECTION") -> Path` dan `_make_digital_pdf(path: Path, text: str) -> Path`, dipakai ulang Task 4.

- [ ] **Step 1: Write the failing test**

Fixture builder (dipakai ulang Task 4):

```python
# tests/unit/test_pdf_ocr.py
from pathlib import Path

import pymupdf

from research_mcp.parsers import pdf_extractor


def _make_digital_pdf(
    path: Path, text: str = "Parking occupancy detection with YOLO and RT-DETR on video."
) -> Path:
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
```

Dua test:

| Test | Arrange | Assert |
|---|---|---|
| `test_scanned_pdf_is_ocred_by_default_backend(tmp_path, monkeypatch)` | env `RESEARCH_MCP_OCR_FALLBACK=false` (isolasi ke backend default), PDF dari `_make_scanned_pdf` | `"PARKING" in markdown.upper()` |
| `test_digital_text_pdf_is_unchanged(tmp_path)` | PDF dari `_make_digital_pdf` | `markdown.count("Parking occupancy detection with YOLO and RT-DETR on video.") == 1` |

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_pdf_ocr.py -v`
Expected: PASS (2 passed). Bukti "sebelum" sudah tercatat di spec §1: pada baseline `28ad3f3`, PDF scan yang sama menghasilkan `text: ''` dan mencetak `Warning: RapidOCR is missing`. Bila `test_scanned_pdf_is_ocred_by_default_backend` masih FAIL dengan markdown kosong, backend belum terpilih — kembali ke Task 1.

- [ ] **Step 3: Simpan baseline hasil scan untuk review**

Jalankan sekali dengan `-s` dan tempel output markdown ke komentar PR/commit message bagian verifikasi (bukti OCR benar-benar menghasilkan teks, bukan string kosong).

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_pdf_ocr.py
git commit -m "test(parsers): prove scanned PDFs are OCRed by the default backend"
```

---

### Task 4: Lapis kedua Tesseract untuk halaman tipis

**Files:**
- Modify: `src/research_mcp/parsers/pdf_extractor.py` (setelah `_markdown_text`, sebelum `extract_markdown`)
- Modify: `tests/unit/test_pdf_ocr.py`
- Modify: `README.md` (prasyarat Tesseract)

**Interfaces:**
- Consumes: `Settings.ocr_language`, `Settings.ocr_fallback` (Task 2), `_parser_messages_to_stderr()` (sudah ada), fixture Task 3.
- Produces:
  - `OCR_MIN_TEXT_CHARS: int = 40`, `OCR_DPI: int = 300` (modul `pdf_extractor`)
  - `_tesseract_available() -> bool`
  - `_ocr_pages(pdf_path: Path, page_numbers: list[int], language: str) -> dict[int, str]` — kunci = nomor halaman 1-based
  - `_ocr_thin_pages(chunks: list[dict], pdf_path: Path, language: str) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

Tambahkan `import pytest` di header `tests/unit/test_pdf_ocr.py`, lalu fixture + helper:

```python
@pytest.fixture
def ocr_case(tmp_path: Path):
    """(project_dir, pdf_path) valid untuk extract_markdown; isi chunk di-stub per test."""
    project_dir = tmp_path / "project"
    (project_dir / "pdfs").mkdir(parents=True)
    return project_dir, _make_scanned_pdf(project_dir / "pdfs" / "scan.pdf")


def _stub_pages(monkeypatch, texts: list[str]) -> None:
    """Ganti output parser dengan page chunks buatan.

    Setelah Task 1 halaman scan sungguhan sudah berteks (RapidOCR), jadi jalur
    halaman-tipis tidak pernah terpicu oleh PDF asli; test di bawah memaku
    wiring `_ocr_thin_pages`, bukan perilaku RapidOCR.
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
```

Test yang harus ditulis — `digital = "Digital methods section stays intact and is long enough."`

| Test | Arrange (di atas `ocr_case` + helper) | Assert |
|---|---|---|
| `test_thin_page_is_replaced_by_tesseract_text` | chunks `[digital, ""]`, tesseract `{2: "TESSERACT OCR TEXT"}` | `"TESSERACT OCR TEXT" in markdown` **dan** `digital in markdown` |
| `test_fallback_disabled_never_calls_tesseract` | env `RESEARCH_MCP_OCR_FALLBACK=false`, chunks `["", ""]` | `calls == []` |
| `test_missing_tesseract_keeps_default_backend_output` | `_tesseract_available` → `False`, chunks `["RAPID OCR TEXT"]` | `"RAPID OCR TEXT" in markdown` dan `calls == []` |
| `test_tesseract_failure_is_swallowed` | `_ocr_pages` raise `RuntimeError("Failed to load language 'ind'")`, chunks `["RAPID OCR TEXT"]` | `"RAPID OCR TEXT" in markdown`; `capfd.readouterr().out == ""` |
| `test_blank_pages_are_attempted_once` | chunks `["", ""]`, tesseract `{1: "", 2: ""}` | `calls == [[1, 2]]` (satu percobaan per halaman) |
| `test_mixed_pages_ocrs_only_the_thin_page` | chunks `[digital, ""]`, tesseract `{2: "SCAN PAGE TEXT"}` | `calls == [[2]]`, `"SCAN PAGE TEXT" in markdown`, `markdown.count(digital) == 1` |
| `test_tesseract_available_is_false_when_tessdata_missing` | `monkeypatch.setattr(pdf_extractor.pymupdf, "get_tessdata", raise RuntimeError)` | `_tesseract_available() is False` |
| `test_live_tesseract_ocred_scanned_page` | `@pytest.mark.skipif(not pdf_extractor._tesseract_available(), ...)`, PDF scan asli, `_ocr_pages(pdf_path, [1], "ind+eng")` | `"PARKING" in pages[1].upper()` |

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_pdf_ocr.py -v`
Expected: FAIL — `AttributeError: module 'research_mcp.parsers.pdf_extractor' has no attribute '_tesseract_available'`

- [ ] **Step 3: Implement the Tesseract layer**

Di `src/research_mcp/parsers/pdf_extractor.py`, tambahkan konstanta modul `OCR_MIN_TEXT_CHARS = 40` dan `OCR_DPI = 300`, lalu:

```python
def _tesseract_available() -> bool:
    """True bila Tesseract + tessdata bisa dipakai PyMuPDF."""
    try:
        pymupdf.get_tessdata()
    except Exception:
        return False
    return True


def _ocr_pages(pdf_path: Path, page_numbers: list[int], language: str) -> dict[int, str]:
    """OCR satu set halaman dengan Tesseract; kegagalan per halaman ditelan."""
    texts: dict[int, str] = {}
    with _parser_messages_to_stderr(), pymupdf.open(pdf_path) as document:
        for page_number in page_numbers:
            try:
                page = document[page_number - 1]
                textpage = page.get_textpage_ocr(language=language, dpi=OCR_DPI, full=True)
                texts[page_number] = textpage.extractText()
            except Exception as exc:  # OCR opsional: jangan pernah gagalkan tool
                print(f"Tesseract OCR skipped on page {page_number}: {exc}", file=sys.stderr)
    return texts


def _ocr_thin_pages(chunks: list[dict], pdf_path: Path, language: str) -> list[dict]:
    """Ganti teks halaman tipis dengan hasil OCR Tesseract."""
    thin = [
        chunk["metadata"]["page_number"]
        for chunk in chunks
        if isinstance(chunk.get("text"), str)
        and len(chunk["text"].strip()) < OCR_MIN_TEXT_CHARS
        and isinstance(chunk.get("metadata"), dict)
        and isinstance(chunk["metadata"].get("page_number"), int)
    ]
    if not thin:
        return chunks
    replacements = _ocr_pages(pdf_path, thin, language)
    for chunk in chunks:
        page_number = chunk.get("metadata", {}).get("page_number")
        text = replacements.get(page_number, "").strip()
        if text:
            chunk["text"] = text
    return chunks
```

Lalu ubah `extract_markdown`:

```python
def extract_markdown(pdf_path: Path, project_dir: Path) -> str:
    resolved_path = _project_path(pdf_path, project_dir)
    _validate_pdf_file(resolved_path)
    settings = get_settings()
    with _parser_messages_to_stderr():
        result = pymupdf4llm.to_markdown(
            str(resolved_path),
            page_chunks=True,
            show_progress=False,
        )
    if isinstance(result, list) and settings.ocr_fallback and _tesseract_available():
        result = _ocr_thin_pages(result, resolved_path, settings.ocr_language)
    return _markdown_text(result)
```

`from research_mcp.config import get_settings` di bagian import modul. Pastikan tidak ada import melingkar (`config` tidak mengimpor `parsers`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_pdf_ocr.py -v`
Expected: PASS — 9 passed, 1 skipped (live test skip bila Tesseract belum terpasang).

- [ ] **Step 5: Install Tesseract (opsional, untuk membuktikan lapis kedua)**

```bash
brew install tesseract tesseract-lang   # menyediakan ind, eng
.venv/bin/python -m pytest tests/unit/test_pdf_ocr.py -v -k live
```
Expected: PASS — `test_live_tesseract_ocred_scanned_page` tidak lagi skipped.

- [ ] **Step 6: Document the optional prerequisite**

README: tambahkan catatan bahwa Tesseract opsional (`brew install tesseract tesseract-lang`), dipakai sebagai lapis kedua untuk halaman yang teksnya tipis, bahasa diatur `RESEARCH_MCP_OCR_LANGUAGE` (default `ind+eng`), dan bisa dimatikan dengan `RESEARCH_MCP_OCR_FALLBACK=false`.

- [ ] **Step 7: Full suite + commit**

```bash
.venv/bin/python -m pytest -q
git add src/research_mcp/parsers/pdf_extractor.py tests/unit/test_pdf_ocr.py README.md
git commit -m "feat(parsers): add Tesseract second-pass OCR for thin pages"
```
Expected: 576 passed, 2 skipped, 0 failed (baseline 562 + 2 test backend + 3 test config + 10 test OCR; 2 skip tanpa Tesseract).

---

### Task 5: Verifikasi akhir worktree baru

**Files:** tidak ada perubahan kode.

- [ ] **Step 1: Buktikan `uv sync` bersih di clone/worktree baru**

```bash
git -C /Users/rezalkomdan/python-workspace/research-mcp worktree add /tmp/ocr-verify --detach fix/rapidocr-ocr-backend
cd /tmp/ocr-verify && uv sync && .venv/bin/python -c "from pymupdf4llm.helpers.document_layout import select_ocr_function; print(callable(select_ocr_function()))"
```
Expected: `True`, tanpa baris "RapidOCR is missing".

- [ ] **Step 2: Bersihkan worktree verifikasi**

```bash
cd /Users/rezalkomdan/python-workspace/research-mcp && git worktree remove /tmp/ocr-verify --force
```
