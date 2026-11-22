# OCR Backend: RapidOCR + Tesseract — Design

_Tanggal: 2026-11-23 · Status: Approved · Pendekatan: A (RapidOCR sebagai dependency) + C (Tesseract sebagai lapis kedua, di-wire sendiri)_

---

## 1. Intent & Why

`pymupdf4llm` 2.1 mencetak `Warning: RapidOCR is missing. Install for much
improved OCR results.` karena `select_ocr_function()` tidak menemukan backend OCR
sama sekali:

- `detect_rapidocr_backend()` → `None` (tidak ada `rapidocr` maupun
  `rapidocr_onnxruntime`)
- `pymupdf.get_tessdata()` → `RuntimeError: ... Tesseract is not installed`

Dampaknya bukan sekadar peringatan: **PDF hasil scan tidak menghasilkan teks
apa pun**. Proyek riset yang bergantung pada paper scan jadi kehilangan seluruh
isi dokumen tanpa error yang jelas.

`pymupdf` sudah menarik `onnxruntime` 1.32.0, jadi mesin inferensi sudah ada —
yang kurang hanya paket OCR-nya dan model bahasanya.

**Tujuan: halaman scan menghasilkan teks, peringatan hilang, dan teks berbahasa
Indonesia dikenali dengan model `ind` — bukan hanya model Latin generik.**

---

## 2. Goals, Non-Goals, Anti-Goals

### Goals
- `uv sync` di clone/worktree mana pun menghasilkan instalasi dengan OCR aktif
  (offline, tanpa unduh model saat runtime).
- Tesseract terpakai secara eksplisit untuk halaman yang gagal/kosong, dengan
  bahasa yang bisa dikonfigurasi (`ind+eng` default).
- Tanpa Tesseract terpasang, semuanya tetap jalan tanpa error.
- Perilaku ekstraksi PDF ber-teks normal tidak berubah (tidak ada duplikasi teks).

### Non-Goals
- Mengganti pipeline layout `pymupdf4llm`/`pymupdf_layout`.
- OCR tabel/gambar terpisah, deteksi bahasa otomatis, atau OCR real-time.
- Mengubah `ocr_language` milik `pymupdf4llm` — parameter itu diabaikan backend
  RapidOCR, jadi tidak ada gunanya diteruskan.

### Anti-Goals
- Menambah dependensi sistem yang wajib (Tesseract harus tetap opsional).
- Menjadikan OCR jalur wajib: PDF digital biasa tidak boleh lebih lambat karena
  OCR ikut berjalan.

---

## 3. Constraints

- Python 3.13.15. **`rapidocr-onnxruntime` (1.4.4) butuh `Python <3.13`** → tidak
  bisa dipakai. Ini mematikan jalur bawaan `pymupdf4llm` yang memakai RapidOCR
  untuk deteksi + Tesseract untuk recognition (`rapidtess_api`).
- Dengan paket baru `rapidocr` terpasang, `select_ocr_function()` **early-return**
  ke RapidOCR-only (`document_layout.py:1243-1249`) sebelum memeriksa `tessdata`.
  Konsekuensi: Tesseract tidak akan pernah dipanggil `pymupdf4llm`; wiring harus
  dilakukan oleh kode kita.
- `pymupdf4llm.to_markdown(*args, **kwargs)` meneruskan kwargs ke
  `parse_document()`, termasuk `ocr_language` dan `ocr_function`.
- Stdout dipakai MCP JSON-RPC: setiap chatter OCR harus tetap di stderr
  (`_parser_messages_to_stderr()` yang sudah ada).

---

## 4. Approaches

| # | Pendekatan | Trade-off | Keputusan |
|---|---|---|---|
| A | `rapidocr>=3.10.0` masuk `dependencies` utama | +~80 MB per instalasi (wheel 27 MB + opencv-headless), tapi `uv sync` cukup dan 100% offline | **Dipilih** |
| B | optional extra `[optional-dependencies] ocr` | default ringan, tapi `uv sync` biasa tetap memunculkan warning → terasa belum beres | Ditolak |
| C | Tesseract (`brew install tesseract tesseract-lang`), dipakai lewat fallback eksplisit di `pdf_extractor.py` | dependensi sistem opsional; tetapi satu-satunya cara mendapat model bahasa `ind` | **Dipilih**, dengan wiring sendiri |
| D | `rapidocr-onnxruntime` + Tesseract (jalur bawaan `rapidtess_api`) | mustahil di Python 3.13 | Ditolak (constraint) |

---

## 5. Design

### 5.1 Dependency (A)
- `pyproject.toml` → `dependencies`: `rapidocr>=3.10.0`.
- `uv lock` diperbarui; `uv sync` memasang `rapidocr`, `opencv-python-headless`,
  `pyclipper`, `shapely`, `omegaconf`, `colorlog`, `tqdm`.
- Model `PP-OCRv6_det_small`, `PP-OCRv6_rec_small`, `ch_ppocr_mobile_v2.0_cls`
  ikut di dalam wheel → tidak ada unduhan saat runtime.

### 5.2 Tesseract sebagai lapis kedua (C)
- Prasyarat sistem (opsional, didokumentasikan di README):
  `brew install tesseract tesseract-lang` (menyediakan `ind`, `eng`).
- `config.py` → dua setting baru:
  - `RESEARCH_MCP_OCR_LANGUAGE` (default `ind+eng`)
  - `RESEARCH_MCP_OCR_FALLBACK` (default `true`)
- `pdf_extractor.py`:
  - `_tesseract_available() -> bool` — cache hasil `pymupdf.get_tessdata()`;
    `False` bila Tesseract/tessdata tidak ada.
  - `_ocr_page_with_tesseract(doc, page_index, language) -> str` — render halaman
    ke pixmap 300 dpi, `get_textpage_ocr(language=...)`, kembalikan teks.
  - `extract_markdown()` menjalankan `pymupdf4llm.to_markdown(...)` seperti
    sekarang; **hanya bila** `ocr_fallback` aktif **dan** Tesseract tersedia,
    halaman dengan teks hasil ekstraksi `< 40` karakter di-OCR ulang dengan
    Tesseract dan hasilnya menggantikan bagian halaman itu. Semua chatter
    dibungkus `_parser_messages_to_stderr()`.
  - Kegagalan Tesseract per halaman ditelan (log ke stderr) — ekstraksi tetap
    mengembalikan hasil RapidOCR, tidak pernah melempar error ke tool.

### 5.3 Yang menjadi keputusan builder
- Ambang "teks tipis" (diusulkan 40 karakter, konstanta bernama).
- Bentuk struktur hasil `to_markdown(page_chunks=True)` saat mengganti teks satu
  halaman (perlu pembuktian di test, bukan asumsi).
- Nama fungsi privat dan pesan log.

---

## 6. Verification

1. `uv sync` bersih di worktree baru; `pymupdf4llm` menemukan backend (dump
   `INFO_MESSAGES` tidak lagi memuat "RapidOCR is missing").
2. Fixture PDF scan (teks digambar ke image, tanpa text layer) → `extract_markdown`
   mengembalikan teks yang berisi kata kunci.
3. PDF ber-teks normal → hasil identik dengan baseline, tanpa duplikasi.
4. `RESEARCH_MCP_OCR_FALLBACK=false` → tidak ada pemanggilan Tesseract.
5. Simulasi Tesseract tidak terpasang → tidak error, hasil RapidOCR tetap keluar.
6. Suite penuh: baseline 562 passed, 1 skipped harus tetap hijau.

---

## 7. Out of Scope / Follow-up

- Ingesti template PDF/DOCX milik pengguna (Phase 2 gaya riset) tidak berubah.
- Bila kelak Python diturunkan ke 3.12, jalur `rapidtess_api` bawaan bisa
  dipertimbangkan ulang.
