# Audit & Rencana Perbaikan Research MCP
_Tanggal: 2026-11-22 | Status: Draft — belum diimplementasi_

---

## Ringkasan Eksekutif

Research MCP memiliki fondasi teknis yang solid (SQLite FTS5, 6 provider literatur, citation gate, pipeline Pandoc/Tectonic). Namun dua lapisan mengalami masalah serius:

1. **WordCraft bridge menghasilkan dokumen yang tidak terstruktur** — tiga bug kode mengakibatkan heading, body text, dan tabel menyatu menjadi satu blok tanpa pemisah paragraf atau format akademik yang benar.
2. **Ergonomi seluruh toolset tidak ramah** — baik untuk agent maupun user manusia — karena response verbose, nama parameter membingungkan, banyak operasi penting tidak tersedia sebagai tool, dan alur riset end-to-end tidak dipandu dengan cukup.

---

## Bagian 1 — Bug Kritis di `wordcraft_bridge.py`

File: `src/research_mcp/exporters/wordcraft_bridge.py`

### BUG-1 · Tidak ada `text.newParagraph` antar blok (Critical)

**Lokasi:** fungsi `_block_steps()` baris 406–439 dan `_bibliography_steps()` baris 477–486.

**Masalah:** Setelah `type_text` + `para.heading1/2/3`, kursor WordCraft tetap di dalam paragraf heading. `type_text` berikutnya menempel ke heading yang sama → semua konten jadi satu blok.

```python
# SEKARANG (salah):
def _block_steps(block):
    if kind == "heading":
        return [{"tool": "type_text", ...}, _execute("para.heading1")]
        # cursor masih di heading paragraph!

# HARUS:
_NEW_PARA = _execute("text.newParagraph")

def _block_steps(block):
    if kind == "heading":
        return [{"tool": "type_text", ...}, _execute(f"para.heading{level}"), _NEW_PARA]
    if kind == "paragraph":
        return [...type_text..., _NEW_PARA]
    if kind == "table":
        return [...insert_table..., _execute("caret.docEnd"), _NEW_PARA]
    if kind == "list":
        return [...type_text..., _execute("para.bullets"), _NEW_PARA]
```

### BUG-2 · Nama style salah di `styles.modify` (High)

**Lokasi:** fungsi `_style_steps()` baris 369–403.

**Masalah:** `styles.modify` dipanggil dengan `{"style": "Heading1"}` tanpa spasi. WordCraft mengenal style ini sebagai `"Heading 1"` (dengan spasi). Akibatnya modifikasi font/spacing heading gagal silent — heading tampil dengan style default.

```python
# SEKARANG (salah):
_execute("styles.modify", {"style": "Heading1", ...})

# HARUS:
_execute("styles.modify", {"style": "Heading 1", ...})
```

### BUG-3 · Simbol Markdown mentah dikirim ke WordCraft (High)

**Lokasi:** fungsi `_block_steps()` → `type_text` untuk paragraph block.

**Masalah:** Teks dari Pandoc Citeproc berisi simbol Markdown/Pandoc yang tidak di-strip sebelum masuk ke WordCraft:
- `---` (horizontal rule) → muncul sebagai teks literal
- `\<`, `\>` → muncul sebagai `\<` dan `\>` 
- `\~` → muncul sebagai `\~`
- `{#refs .references .csl-bib-body ...}` → div attribute Pandoc muncul verbatim
- `:::` (Pandoc div fence) → muncul sebagai teks

**Fix:** Tambah fungsi `_strip_md_inline(text: str) -> str` yang membersihkan simbol-simbol ini sebelum `type_text`.

### BUG-4 · Tidak ada setup halaman akademik (Medium)

**Lokasi:** fungsi `_style_steps()` dan `build_blueprint()`.

**Masalah:** Tidak ada perintah untuk:
- Ukuran kertas A4
- Margin dokumen (artikel: 2.5 cm semua sisi; skripsi Indonesia: 4/3/3/3 cm)
- Alignment paragraf (justify)
- Nomor halaman di footer
- TOC (Daftar Isi) untuk skripsi

Akibatnya dokumen keluar dengan ukuran letter, margin default, rata kiri.

### BUG-5 · Tidak ada parameter `doc_type` (Medium)

**Lokasi:** `build_blueprint()` dan tool `prepare_wordcraft_handoff`.

**Masalah:** Tidak ada cara membedakan format output antara:
- **Artikel jurnal** (judul + abstrak + IMRaD + referensi, margin tipis, 1 kolom atau 2 kolom)
- **Karya ilmiah/makalah** (halaman judul singkat, bab bernomor, daftar pustaka)
- **Skripsi/Tesis** (halaman judul lengkap + halaman pengesahan + abstrak + daftar isi + bab bernomor BAB I/II/III + spasi 2× + margin 4-3-3-3 cm + halaman romawi untuk preamble)

---

## Bagian 2 — Ergonomi Project Management

### PM-1 · `create_project` tidak return workspace_path (Medium)

`create_project` hanya return `model_dump()` dari project record (id, slug, name, description, timestamps). Agent harus langsung memanggil `get_project_status` untuk tahu di mana workspace berada.

**Fix:** Sertakan `workspace_path` dalam response `create_project`.

### PM-2 · Tidak ada tool `list_papers` (High)

Tidak ada cara melihat daftar paper yang sudah disimpan di proyek beserta metadata lengkap (judul, penulis, tahun, DOI, apakah PDF sudah diunduh). `get_project_status` hanya return array `citekeys` tanpa metadata.

**Fix:** Tambah tool `list_papers(project_id)` yang return:
```json
[{
  "citekey": "mamani2026comparative",
  "title": "Comparative Evaluation...",
  "authors": ["Mamani", "..."],
  "year": 2026,
  "doi": "10.3390/s26175329",
  "pdf_downloaded": true,
  "section_count": 8
}]
```

### PM-3 · Tidak ada tool `delete_paper`, `delete_draft`, `delete_project` (High)

Tidak ada operasi penghapusan. User dan agent tidak bisa:
- Menghapus paper yang salah masuk
- Menghapus draft bab yang sudah tidak relevan
- Menghapus seluruh proyek

**Fix:** Tambah tool:
- `delete_paper(project_id, citekey)` — hapus paper + PDF + sections dari proyek
- `delete_draft_section(project_id, filename)` — hapus bab draf
- `delete_project(project_id, confirm=False)` — hapus seluruh proyek (memerlukan `confirm=True`)

### PM-4 · `get_project_status` terlalu verbose (Medium)

Response `get_project_status` mencampur metadata proyek, path workspace, daftar file, dan citekeys dalam satu response besar. Agent sering harus re-call untuk mendapat info spesifik.

**Fix:** Pisahkan atau tambahkan filter parameter `include=["papers", "drafts", "exports", "assets"]`.

### PM-5 · Tidak ada tool `add_project_note` (Low)

Tidak ada cara menyimpan konteks penelitian (tujuan, rumusan masalah, hipotesis) yang bisa dibaca kembali oleh agent di sesi berbeda.

**Fix:** Tambah `save_project_note(project_id, content)` dan `read_project_note(project_id)` sebagai "research brief" proyek.

---

## Bagian 3 — Ergonomi Citation Management

### CIT-1 · Sintaks `[@citekey]` tidak diajarkan secara eksplisit (High)

Tool `save_draft_section` menerima konten Markdown dengan citation Pandoc, tapi tidak ada panduan di docstring atau response tentang format yang benar. User pertama kali tidak tahu harus menulis `[@mamani2026comparative]` bukan `(Mamani et al., 2026)`.

**Fix:** Tambah panduan di docstring dan di response `save_paper_to_project`:
```json
{
  "citekey": "mamani2026comparative",
  "cite_as": "[@mamani2026comparative]",
  "cite_multiple": "[@mamani2026comparative; @grbic2023automatic]"
}
```

### CIT-2 · Tidak ada tool `update_citekey` (Medium)

Jika agent menghasilkan citekey yang salah (misalnya collision suffix), tidak ada cara mengubah citekey paper. User harus delete + re-add paper.

**Fix:** Tambah `rename_paper_citekey(project_id, old_citekey, new_citekey)`.

### CIT-3 · `verify_citations` tidak return context lokasi error (Medium)

`verify_citations` return array `cited_but_missing` berisi citekeys yang tidak ditemukan, tapi tidak menunjukkan di mana dalam draft citekey tersebut muncul. Agent harus re-scan draft manual.

**Fix:** Return juga `{citekey: "...", locations: ["01_intro.md:L23", "03_methods.md:L7"]}`.

### CIT-4 · Tidak ada tool `search_papers_in_project` (Medium)

Setelah menyimpan 10+ paper, tidak ada cara mencari paper berdasarkan judul, penulis, atau tahun tanpa membaca semua section melalui FTS. `search_local_library` mencari dalam isi PDF, bukan metadata paper.

**Fix:** Tambah filter ke `list_papers`: `list_papers(project_id, query="YOLO", year_from=2024)`.

---

## Bagian 4 — Ergonomi Draft & Penulisan

### DRF-1 · Tidak ada `list_draft_sections` sebagai tool publik (High)

`list_draft_sections` ada di repository internal tapi tidak di-expose sebagai MCP tool. Agent harus memanggil `get_project_status` dan melihat array `draft_filenames` yang hanya berisi nama file, bukan judul atau word count.

**Fix:** Expose `list_draft_sections(project_id)` sebagai tool dengan response:
```json
[{
  "filename": "01_intro.md",
  "title": "1. Pendahuluan",
  "order": 1,
  "word_count": 342,
  "updated_at": "2026-11-22T10:15:00Z"
}]
```

### DRF-2 · Tidak ada konvensi penamaan file draf yang di-enforce (Medium)

`save_draft_section` menerima filename bebas. Ini menyebabkan ketidakkonsistenan: satu sesi memakai `01_abstract.md`, sesi lain memakai `abstract.md` atau `bab1.md`, yang mengacaukan urutan merge saat export.

**Fix:** Enforce prefix numerik atau tambah parameter `section_type` yang menentukan nama file standar:
- `section_type="abstract"` → `00_abstract.md`
- `section_type="introduction"` → `01_introduction.md`
- `section_type="methods"` → `02_methods.md`
- dll.

### DRF-3 · `read_draft_section` tidak return metadata (Low)

Response hanya berisi `{content: "..."}` tanpa word_count, last_updated, atau bagian mana dari merged document ini.

**Fix:** Return juga `word_count`, `updated_at`, `order`, `citation_count` (jumlah `[@...]` dalam konten).

### DRF-4 · Tidak ada `append_to_draft_section` (Low)

Untuk menambahkan konten ke bab yang sudah ada, agent harus: read → concatenate → write kembali. Ini memboroskan token dan berisiko overwrite konten jika ada race condition.

**Fix:** Tambah `append_to_draft_section(project_id, filename, content)`.

---

## Bagian 5 — Ergonomi PDF & Search

### PDF-1 · Gagal download PDF tidak dilaporkan dengan jelas (High)

`save_paper_to_project` dengan `download_pdf=True` pada paper closed-access akan:
1. Menyimpan metadata paper (sukses)
2. Gagal download PDF (silent: hanya log warning)
3. Return success response

Agent mengira PDF tersedia padahal tidak. Panggilan berikutnya ke `list_paper_sections` atau `read_paper_section` akan return kosong atau error.

**Fix:** Response harus eksplisit:
```json
{
  "citekey": "...",
  "pdf_downloaded": false,
  "pdf_download_error": "No open-access PDF found via Unpaywall. Import manually with import_local_pdf.",
  "sections_indexed": 0
}
```

### PDF-2 · `read_paper_section` butuh exact section name (Medium)

`section_name` harus persis sama dengan heading yang diekstrak. Jika extractor menghasilkan "2. Methods" tapi agent memanggil dengan "Methods", fungsi gagal.

**Fix:** Tambah fuzzy matching (case-insensitive, strip numbering prefix) atau return available section names dalam error message.

### PDF-3 · Tidak ada tool `retry_pdf_download` (Medium)

Jika PDF tidak tersedia saat `save_paper_to_project` tapi kemudian user mendapat akses (misalnya koneksi VPN kampus), tidak ada cara re-download tanpa delete + re-add paper.

**Fix:** Tambah `retry_pdf_download(project_id, citekey)`.

### PDF-4 · `search_local_library` snippet tidak memadai (Low)

Response berisi `{snippet: "...", heading: "...", citekey: "..."}` tapi snippet dipotong di 300 karakter tanpa context kalimat utuh. Agent sering harus `read_paper_section` untuk mendapat teks yang cukup.

**Fix:** Return 2 kalimat konteks sebelum dan sesudah match, bukan dipotong di karakter ke-300.

---

## Bagian 6 — Ergonomi Export & Publishing

### EXP-1 · `prepare_wordcraft_handoff` return terlalu besar (High)

Response berisi `blueprint.blocks` (semua konten dokumen) DAN `tool_sequence` (semua command WordCraft) sekaligus. Untuk dokumen 10+ halaman ini bisa menjadi 50–100KB JSON yang membanjiri context window agent.

**Fix:** Default hanya return `tool_sequence` dan summary stats. `blocks` hanya direturn jika diminta dengan `include_blocks=True`.

### EXP-2 · Tidak ada parameter `output_dir` untuk export (Medium)

Semua export selalu ke `workspace/exports/`. User tidak bisa memilih folder tujuan (misalnya `~/Documents/Riset/`).

**Fix:** Tambah parameter opsional `output_dir: str | None = None` pada semua export tool.

### EXP-3 · Tidak ada `doc_type` untuk format akademik yang berbeda (High)

`export_to_docx`, `export_to_latex`, dan `prepare_wordcraft_handoff` tidak punya parameter untuk membedakan format dokumen. Semua output terasa seperti artikel IEEE padahal user mungkin butuh format skripsi Indonesia.

**Fix:** Tambah `doc_type: Literal["article", "thesis", "paper"] = "article"` yang mengatur:
- **article**: margin 2.5cm semua, 1.5× spasi, justify, heading `1. Judul`, `1.1 Sub`, page number footer center
- **thesis**: margin 4cm kiri/3cm lain, 2× spasi, justify, heading `BAB I`, `1.1`, halaman romawi preamble, TOC wajib, halaman judul
- **paper**: margin 3cm semua, 1.5× spasi, justify, heading bernomor, abstrak singkat

### EXP-4 · `export_to_latex` tidak ada opsi template Indonesia (Medium)

Template tersedia: `ieee`, `springer`, `article_id`. Tidak ada template skripsi universitas Indonesia yang umum (format DIKTI/SNPT: Times New Roman 12pt, spasi 1.5–2, margin 4-3-3-3).

**Fix:** Tambah template `thesis_id` dengan format standar perguruan tinggi Indonesia.

### EXP-5 · Mode handoff `live_authoring` / `seed_docx` / `batch_script` tidak intuitif (Medium)

Nama mode tidak mendeskripsikan kapan dipakai. User tidak tahu harus pilih yang mana.

**Fix:** Rename atau tambah deskripsi yang lebih jelas:
- `live_authoring` → `"write_live"` (WordCraft MCP aktif, tulis langsung)
- `seed_docx` → `"open_in_wordcraft"` (export dulu, buka di WordCraft)
- `batch_script` → `"headless_batch"` (jalankan tanpa interaksi)

Atau tambah `mode="auto"` yang otomatis deteksi berdasarkan `check_wordcraft_env`.

---

## Bagian 7 — Ergonomi Search & Discovery

### SRC-1 · Semantic Scholar rate-limit tidak di-handle dengan baik (High)

Semantic Scholar (S2) sering return HTTP 429 (rate limit). Saat ini, error ini terlog di `errors` dict tapi tidak ada retry logic yang memadai. `aggregator.py` menggunakan `asyncio.gather` semua provider sekaligus → semua kena 429 jika dipanggil berturut-turut.

**Fix:** Tambah exponential backoff khusus S2 (sudah ada `tenacity` di dependensi, perlu diterapkan ke S2 provider) + kurangi concurrency.

### SRC-2 · `search_papers` tidak ada opsi pencarian penulis (Medium)

Tidak bisa mencari paper by author. Query string harus menyertakan nama penulis secara manual dalam `query` text.

**Fix:** Tambah parameter `author: str | None = None` yang di-pass ke provider yang mendukung (OpenAlex, S2).

### SRC-3 · Tidak ada tool `quick_save_all` untuk batch save dari search results (Medium)

Alur saat ini: `search_papers` → pilih satu per satu → `save_paper_to_project` per paper. Tidak ada cara save multiple sekaligus dari search results.

**Fix:** Tambah `save_papers_to_project(project_id, identifiers: list[str], download_pdf=True)` yang batch save.

---

## Bagian 8 — Tool yang Kurang / Alur yang Tidak Lengkap

### MISS-1 · Tidak ada `synthesis_table` tool (Medium)

Untuk literature review, agent sering perlu membuat tabel perbandingan paper (metode, dataset, akurasi, dll). Saat ini harus dilakukan manual di draft Markdown.

**Fix:** Tambah `generate_synthesis_table(project_id, columns: list[str])` yang membaca abstrak + sections dari semua paper dan menghasilkan Markdown table untuk di-paste ke draft.

### MISS-2 · Tidak ada health check "siap riset?" (Low)

Tidak ada tool yang menjawab: "Apakah research-mcp sudah dikonfigurasi dengan benar?" — termasuk cek `RESEARCH_MCP_EMAIL`, pandoc tersedia, direktori workspace writable, dll.

**Fix:** Tambah `health_check()` tool yang return status semua dependensi.

### MISS-3 · `systematic_literature_review` prompt terlalu panjang (Low)

Prompt ini berisi instruksi 40+ baris dalam satu teks. Jika diminta oleh agent, ini memakan banyak context window hanya untuk instruksi.

**Fix:** Perpendek jadi 10–15 baris esensial, link ke dokumentasi untuk detail.

---

## Prioritas Implementasi

| # | Item | Dampak | Effort | Prioritas |
|---|------|--------|--------|-----------|
| 1 | BUG-1: `text.newParagraph` antar blok | Tinggi | Kecil | **P0** |
| 2 | BUG-2: nama style `"Heading 1"` | Tinggi | Kecil | **P0** |
| 3 | BUG-3: strip simbol Markdown | Tinggi | Kecil | **P0** |
| 4 | EXP-3: parameter `doc_type` artikel/skripsi | Tinggi | Sedang | **P1** |
| 5 | BUG-4: setup halaman A4 + margin + justify | Tinggi | Sedang | **P1** |
| 6 | PDF-1: laporan gagal download PDF jelas | Tinggi | Kecil | **P1** |
| 7 | PM-2: tool `list_papers` | Tinggi | Kecil | **P1** |
| 8 | CIT-1: panduan sitasi `[@citekey]` di response | Tinggi | Kecil | **P1** |
| 9 | DRF-1: expose `list_draft_sections` sebagai tool | Sedang | Kecil | **P2** |
| 10 | PM-3: tool `delete_paper`, `delete_draft` | Sedang | Kecil | **P2** |
| 11 | CIT-3: lokasi error dalam `verify_citations` | Sedang | Kecil | **P2** |
| 12 | EXP-1: kurangi verbosity `prepare_wordcraft_handoff` | Sedang | Kecil | **P2** |
| 13 | PDF-2: fuzzy section name matching | Sedang | Sedang | **P2** |
| 14 | SRC-1: retry S2 rate limit | Sedang | Kecil | **P2** |
| 15 | EXP-2: parameter `output_dir` | Rendah | Kecil | **P3** |
| 16 | EXP-4: template skripsi Indonesia | Rendah | Sedang | **P3** |
| 17 | SRC-3: `save_papers_to_project` batch | Rendah | Kecil | **P3** |
| 18 | MISS-1: `synthesis_table` tool | Rendah | Sedang | **P3** |
| 19 | PM-5: `save_project_note` | Rendah | Kecil | **P3** |
| 20 | EXP-5: rename mode handoff | Rendah | Kecil | **P3** |

---

## Rencana Implementasi Bertahap

### Sprint 1 — Fix Bug Kritis WordCraft (P0 semua)
File: `src/research_mcp/exporters/wordcraft_bridge.py`
- BUG-1: Tambah `_NEW_PARA` setelah setiap block
- BUG-2: Fix `"Heading1"` → `"Heading 1"`
- BUG-3: Tambah `_strip_md_inline()`
- BUG-4: Tambah `_page_setup_steps(doc_type)` 
- BUG-5: Tambah parameter `doc_type` ke `build_blueprint()`

Juga update tool `prepare_wordcraft_handoff` di `server.py` untuk menerima `doc_type`.

### Sprint 2 — Perbaiki Ergonomi Tools (P1)
File: `src/research_mcp/server.py` + `src/research_mcp/db/repository.py`
- PM-2: Tool `list_papers`
- PM-1: Tambah `workspace_path` di response `create_project`
- PDF-1: Perjelas status download PDF di response
- CIT-1: Tambah `cite_as` hint di response `save_paper_to_project`

### Sprint 3 — Tambah Tools yang Kurang (P2)
File: `src/research_mcp/server.py`
- DRF-1: Expose `list_draft_sections` sebagai tool
- PM-3: Tool `delete_paper`, `delete_draft_section`, `delete_project`
- CIT-3: Tambah lokasi error di `verify_citations`
- EXP-1: Kurangi verbosity response `prepare_wordcraft_handoff`
- SRC-1: Tambah retry logic untuk Semantic Scholar

### Sprint 4 — Format Akademik & Quality of Life (P3)
- EXP-4: Template LaTeX skripsi Indonesia
- EXP-2: Parameter `output_dir` di semua export
- SRC-3: `save_papers_to_project` batch
- PM-5: `save_project_note` / `read_project_note`

---

_Dokumen ini adalah living document. Update setiap kali ada temuan baru atau keputusan implementasi._
