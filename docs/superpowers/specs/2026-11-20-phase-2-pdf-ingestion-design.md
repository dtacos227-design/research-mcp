# Fase 2 — PDF Ingestion, Parsing, dan Pencarian Lokal

## Intent

Menambahkan alur untuk menyimpan PDF open-access secara legal atau mengimpor PDF lokal milik pengguna, mengubah isinya menjadi Markdown terstruktur, menyimpan section ke SQLite/FTS5, dan menyediakan pembacaan section serta pencarian full-text per proyek. Ini meneruskan alur Fase 1: `Search → Inspect Metadata → Save & Parse → Read Specific Section`, tanpa mengembalikan seluruh teks PDF pada tahap pencarian.

## Goals

- `save_paper_to_project(project_id, identifier, download_pdf=True)` menyimpan metadata paper, citekey, jalur PDF/Markdown, serta section hasil parsing; unduhan hanya dilakukan bila tersedia sumber PDF yang diketahui open-access.
- `import_local_pdf(project_id, file_path, doi=None)` menerima PDF lokal, termasuk PDF berlisensi kampus; DOI opsional untuk memperkaya metadata melalui Crossref.
- `list_paper_sections`, `read_paper_section`, dan `search_local_library` bekerja pada data lokal proyek dan mengembalikan hasil ringkas yang sesuai konteks.
- Seluruh penyimpanan file dibatasi ke direktori proyek setelah `resolve()`; data PDF dan hasil parsing disimpan di `pdfs/` dan `parsed/`.
- FTS5 tetap sinkron melalui trigger yang telah dibuat pada Fase 1.

## Non-goals

- OCR khusus untuk PDF hasil scan sebagai jaminan; ekstraksi memakai perilaku standar PyMuPDF4LLM. Jika teks tidak tersedia, tool melaporkan hasil ekstraksi kosong/terbatas secara jelas.
- Pemrosesan PDF berpassword, perbaikan PDF rusak, penyuntingan PDF, atau publikasi.
- Sitasi/BibTeX/CSL lengkap dan verifikasi anti-halusinasi — itu ruang lingkup Fase 3.
- Mengembalikan teks lengkap PDF dari tool pencarian; hanya section yang diminta atau cuplikan hasil pencarian yang dikembalikan.

## Constraints

- Python 3.11+, `uv`, `aiosqlite`, `httpx`, `pytest`, `pytest-asyncio`, `respx`, dan `ruff` yang telah dipakai proyek.
- Parser utama: `pymupdf4llm`; pemecah section: modul lokal `sectionizer.py`.
- Database memakai skema Fase 1 apa adanya: `papers`, `paper_sections`, dan `paper_sections_fts`; jangan menambah tabel tanpa kebutuhan.
- I/O jaringan async dengan timeout eksplisit; provider yang gagal tidak boleh merusak data proyek atau meninggalkan file parsial seolah-olah valid.
- Remote download: HTTPS only (termasuk setiap redirect; tidak mengikuti downgrade ke HTTP), maksimal 5 redirect, timeout 30 detik, maksimum 100 MiB per PDF, stream ke file sementara dan verifikasi signature `%PDF-` sebelum rename.
- Validasi akses filesystem mengikuti aturan AGENTS.md §5.1.4: `target.resolve().is_relative_to(project_dir.resolve())`.
- Tool gagal mengembalikan `ToolError` yang actionable, bukan traceback mentah.
- Unduhan remote memakai sumber yang telah ditandai OA oleh Unpaywall, atau tautan OA dari provider yang secara eksplisit mengidentifikasi sumber sebagai open-access (contohnya arXiv). Jangan menganggap setiap URL PDF dari Crossref sebagai legal/open-access.

## Approaches

1. **PyMuPDF4LLM + sectionizer Markdown lokal (dipilih).** Simpan PDF per proyek, jalankan `pymupdf4llm.to_markdown(..., page_chunks=True)`, gabungkan halaman, lalu pecah heading Markdown menjadi section dan simpan ke SQLite. Ini sesuai stack dan roadmap, lokal, ringan, serta mudah dites dengan PDF fixture.
2. **Docling sebagai parser utama.** Menawarkan pemahaman layout yang lebih kaya, tetapi menambah dependensi/biaya komputasi dan AGENTS.md telah memilih PyMuPDF4LLM sebagai parser utama dengan Docling hanya opsional. Tidak dipilih.
3. **Ekstraksi langsung dengan PyMuPDF tanpa PyMuPDF4LLM.** Mengurangi dependensi, namun kehilangan konversi Markdown berorientasi struktur yang menjadi tujuan fase. Tidak dipilih.

## Design

- `parsers/pdf_extractor.py` mengurus verifikasi PDF, pengunduhan async dari URL yang telah diotorisasi sebagai OA, impor/copy PDF lokal ke folder proyek, ekstraksi via PyMuPDF4LLM, serta penyimpanan Markdown. Unduhan dan impor lokal dibatasi 100 MiB. Unduhan ditulis secara bertahap ke file sementara di direktori tujuan dan di-rename hanya setelah status HTTP, ukuran, dan signature PDF lolos validasi; kegagalan membersihkan file sementara.
- `parsers/sectionizer.py` menerima Markdown dan mengembalikan daftar terurut `{heading, content, word_count}`. Heading Markdown menjadi batas section; teks sebelum heading pertama masuk ke section awal (mis. `Document`), dan dokumen tanpa heading menjadi satu section `Full Text`.
- `db/repository.py` ditambah operasi paper/section yang menyimpan metadata dan section dalam satu transaksi DB. Re-ingest DOI yang sama dalam proyek mempertahankan `paper_id`/citekey dan mengganti metadata/sections; import lokal tanpa DOI dikenali ulang lewat path deterministik berbasis SHA-256, agar pengulangan tidak membuat paper duplikat. File PDF/Markdown diselesaikan terlebih dahulu; kegagalan DB membersihkan output baru agar metadata dan file tidak divergen. FTS5 terisi melalui trigger Fase 1.
- Citekey deterministik diperlukan sebelum DB dapat menyimpan paper karena `papers.citekey` wajib ada. Fase ini membuat utilitas minimal `generate_citekey` dengan format AGENTS.md §5.2.3 dan suffix bentrok; utilitas yang sama akan diperluas untuk ekspor BibTeX/CSL pada Fase 3. PDF lokal tanpa metadata penulis/tahun memakai `unknown`/`0000` secara deterministik, dengan judul dari metadata PDF atau nama file sebagai fallback.
- Untuk metadata:
  - DOI yang disediakan atau ditemukan pada hasil pencarian di-resolve lewat Crossref; abstract/author/year/venue disimpan dari metadata resmi.
  - Identifier non-DOI dicari melalui provider yang telah tersedia dan harus cocok dengan `external_id` sebelum disimpan.
  - Untuk DOI, gunakan URL PDF Unpaywall hanya ketika record menyatakan open-access. Untuk identifier arXiv, gunakan PDF arXiv; hasil Semantic Scholar hanya jika `openAccessPdf` tersedia. PDF URL dari Crossref atau OpenAlex tanpa bukti OA yang jelas tidak diunduh otomatis.
  - Jika metadata berhasil ditemukan tetapi tidak ada tautan PDF legal atau `download_pdf=False`, metadata tetap disimpan dan tool mengembalikan `download_status="unavailable"` atau `"skipped"` beserta warning ringkas; jangan mengarang URL PDF.
  - Unduhan HTTP/parser yang gagal membersihkan file temporer dan section parsial; metadata yang sudah diketahui tetap dapat disimpan sebagai metadata-only dengan warning.
  - `download_pdf=False` tetap menyimpan metadata tanpa PDF/section; import lokal selalu mempertahankan PDF yang diimpor.
- `list_paper_sections(project_id, citekey)` mengembalikan heading, urutan, dan word count. `read_paper_section(...)` memilih heading yang cocok setelah normalisasi case/whitespace; jika tidak ditemukan atau ambigu, kembalikan `ToolError` yang jelas. `search_local_library(...)` memakai FTS5; tokenisasi query menjadi token literal yang di-quote dan digabung dengan `AND` agar operator/sintaks FTS5 tidak bocor dari input pengguna. Hasil dibatasi sesuai `limit` dan mengembalikan citekey, heading, serta cuplikan maksimum 300 karakter — bukan teks section penuh.
- Validasi dilakukan untuk project id yang tidak ada, file yang bukan PDF, PDF kosong/rusak, URL non-HTTPS untuk unduhan, unduhan non-success, batas ukuran, query FTS5 tanpa token, section yang tak ditemukan/ambigu, serta operasi update/re-ingest agar tidak meninggalkan file atau index yang setengah tertulis.

## Calls for the builder

- Menggunakan PyMuPDF4LLM dengan `page_chunks=True` agar keluaran halaman bisa diuji dan digabung sebelum sectionization.
- Membatasi remote download pada HTTPS dan memverifikasi signature `%PDF-` sebelum commit file.
- Memakai identitas lokal `unknown`/`0000` ketika metadata lokal benar-benar tidak memuat author/year; tidak mengarang data bibliografi.
- Menggunakan subagent-driven development per task; baseline Fase 2 dimulai dari 87 test yang lulus pada branch `main`.
