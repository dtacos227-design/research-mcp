# Research MCP

**MCP Server untuk riset ilmiah end-to-end — 100% gratis, local-first, anti-halusinasi.**

Hubungkan asisten AI-mu ke 250 juta+ paper ilmiah, kelola sitasi otomatis, dan ekspor dokumen siap terbit — semua tersimpan di komputermu sendiri, tanpa berlangganan API apa pun.

---

## Dokumentasi Lengkap

🌐 **[research-mcp.belum.online](https://research-mcp.belum.online)**

---

## Fitur Utama

- **Cari literatur** dari 6 database gratis: OpenAlex, Semantic Scholar, arXiv, PubMed, Crossref, Unpaywall
- **Baca PDF otomatis** — unduh & ekstrak per bagian (Methods, Results, Discussion)
- **Kelola sitasi** — citekey deterministik, ekspor `.bib` & `.csl.json`, format APA/IEEE/Vancouver
- **Gate anti-halusinasi** — blokir sitasi palsu sebelum masuk dokumen
- **Ekspor** ke `.docx`, `.tex`, `.pdf` via Pandoc & Tectonic
- **Integrasi WordCraft MCP** untuk penulisan & layout langsung

## Research Styles & Intake Gate

Setiap proyek mengikuti satu **research style**: kerangka bab, format halaman (margin, spasi, font), dan gaya sitasi bawaan. Delapan gaya bawaan:

| `style_id` | Cocok untuk |
| :--- | :--- |
| `article_imrad` | Artikel jurnal IMRaD (A4, 2.5 cm, 1.5×, APA) |
| `ieee_paper` | Paper konferensi IEEE dua kolom (`ieee`) |
| `skripsi_s1` | Skripsi S1 Indonesia — BAB I–V, margin 4-3-3-3, 2×, TNR 12 |
| `tesis_s2` | Tesis S2 Indonesia — skripsi S1 + pernyataan keaslian & daftar singkatan |
| `proposal_penelitian` | Proposal penelitian/hibah (latar belakang s.d. anggaran) |
| `case_study` | Laporan studi kasus naratif mendalam |
| `rapid_synthesis` | Sintesis cepat / policy brief (`vancouver`) |
| `annotated_bibliography` | Bibliografi beranotasi per entri |

Pakai `list_research_styles` untuk daftar id + outline tiap gaya, dan `get_research_style` untuk detail satu gaya (outline, formatting, sitasi, template ekspor).

**Gaya kustom.** Tambahkan file YAML di `~/.research-mcp/styles/*.yaml`, atau arahkan env var `RESEARCH_MCP_STYLES_DIR` ke folder lain. Gaya pengguna di-merge berdasarkan `id`, sehingga `id` yang sama menimpa gaya bawaan. Restart server agar cache gaya dimuat ulang; YAML tidak valid dilaporkan sebagai error yang menyebut nama filenya.

**Alur kerja.**

1. `list_research_styles` → pilih `style_id`.
2. `save_research_brief(project_id, style_id, title, research_question, citation)` — empat field itu wajib; `language` default `id` (opsi `id`, `en`, `bilingual`); `scope`, `target_length`, `deadline` opsional. Respons menyebut field yang masih kurang.
3. `start_research(project_id)` — selama brief belum lengkap mengembalikan `needs_input` berisi satu pertanyaan bilingual per field yang kosong dan **tidak** membuat file. Setelah lengkap, tiap bab pada outline dibuatkan skeleton `drafts/<filename>`.
4. Tulis bab dengan `save_draft_section`.
5. `verify_citations` sebelum ekspor.
6. `export_to_docx`, `export_to_latex`, atau `prepare_wordcraft_handoff` — ketiganya mewarisi style proyek (urutan gabung bab, page setup, sitasi default); `style=` menimpa untuk satu pemanggilan, `csl_style=` menimpa sitasi saja.

**Gate.** Ketiga exporter menolak berjalan dan mengembalikan `status: "precondition_failed"` beserta daftar `failed` dan langkah `remediation` bila research brief belum lengkap **atau** belum ada paper tersimpan. Tidak ada file yang ditulis sebelum gate lolos.

**Pemakaian modular.** Setiap bagian bisa dipakai sendiri-sendiri; tidak semua membutuhkan research brief.

| Kebutuhan | Cara pakai |
| :--- | :--- |
| Cari literatur saja | `search_papers`, `get_citation_graph`, `resolve_doi` — tanpa brief |
| Kelola paper/PDF saja | `save_paper_to_project`, `import_local_pdf`, `list_paper_sections`, `read_paper_section`, `search_local_library` — tanpa brief |
| Sitasi/bibliografi saja | `generate_bibliography`, `verify_citations` — tanpa brief |
| Grafik saja | `generate_chart` — tanpa brief |
| Tulis bab | `save_draft_section` butuh brief lengkap, atau lewati dengan `format_only=True` |
| Format dokumen saja | beri `format_only=True` pada `export_to_docx` / `export_to_latex` / `export_to_markdown_bundle` / `prepare_wordcraft_handoff` — gate brief & paper dilewati, gaya tetap diterapkan (`style=` eksplisit, atau default `article_imrad`) |

> **Catatan:** ingesti template `.docx`/`.pdf` milik pengguna (mis. template skripsi kampus) belum tersedia — itu masuk **Phase 2**. Saat ini template ekspor hanya yang dibundel: `.docx` `default_academic` dan LaTeX `article_id` / `ieee`.

## Instalasi Cepat

```bash
git clone https://github.com/dtacos227-design/research-mcp.git
cd research-mcp
uv sync
uv run research-mcp
```

Kompatibel dengan **Claude Code**, **OpenCode**, **Codex**, dan **Agy**.

### Folder output riset

Semua hasil (PDF jurnal, Markdown parsed, draf, sitasi, dan dokumen ekspor) disimpan
di folder output. Ada dua tingkat penentuan:

1. **Default global**, ditentukan saat server dijalankan:

   ```bash
   uv run research-mcp --output-dir ~/Riset        # disimpan ke ~/.research-mcp/config.toml
   uv run research-mcp --output-dir ~/Riset --no-persist   # sesi ini saja
   uv run research-mcp --show-config               # lihat konfigurasi efektif lalu keluar
   ```

2. **Per proyek**, lewat tool `create_project(..., output_dir="~/Kerja/Klien-X")`. Pilihan
   ini disimpan bersama proyek, jadi mengubah default global tidak memindahkan proyek yang
   sudah ada.

`output_dir` adalah **folder induk**: setiap proyek mendapat subfolder sendiri
(`<output_dir>/<slug>/`) berisi `pdfs/`, `parsed/`, `drafts/`, `assets/`, `scripts/`, dan
`exports/`. Untuk melihat ke mana hasil akan masuk beserta asal nilainya
(`cli`/`env`/`env-legacy`/`config`/`default`), panggil tool `get_output_config()`.

| Env var | Default | Arti |
| :--- | :--- | :--- |
| `RESEARCH_MCP_OUTPUT_ROOT` | `~/.research-mcp/workspaces` | Folder induk seluruh proyek |
| `RESEARCH_MCP_WORKSPACE` | *(legacy)* | Nama lama `RESEARCH_MCP_OUTPUT_ROOT`; masih dihormati |
| `RESEARCH_MCP_DB_PATH` | `~/.research-mcp/research_mcp.db` | Database metadata (tidak ikut pindah saat folder output berubah) |
| `RESEARCH_MCP_CONFIG_FILE` | `~/.research-mcp/config.toml` | Lokasi file konfigurasi yang ditulis `--output-dir` |

Presedensi: `--output-dir` → `RESEARCH_MCP_OUTPUT_ROOT` → `RESEARCH_MCP_WORKSPACE` →
`config.toml` → default. Proyek yang dibuat sebelum fitur ini tidak punya folder tersimpan
sendiri, jadi ia tetap mengikuti default global sampai dipindahkan manual.

### OCR PDF hasil scan

`uv sync` sudah memasang **RapidOCR** (model ONNX PP-OCRv6 ikut di dalam wheel), jadi
PDF hasil scan ter-OCR otomatis — tidak ada unduhan model saat runtime dan tidak ada
API key. Varian `rapidocr-onnxruntime` tidak dipakai karena butuh Python < 3.13.

Opsional, sebagai lapis kedua: pasang **Tesseract** agar halaman yang teksnya tipis
(< 40 karakter setelah OCR default) di-OCR ulang dengan model bahasa Tesseract.

```bash
brew install tesseract tesseract-lang   # macOS: menyediakan data bahasa ind + eng
# Debian/Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-ind
```

| Env var | Default | Arti |
| :--- | :--- | :--- |
| `RESEARCH_MCP_OCR_LANGUAGE` | `ind+eng` | Bahasa Tesseract untuk lapis kedua |
| `RESEARCH_MCP_OCR_FALLBACK` | `true` | Set `false` untuk mematikan lapis kedua |

Tanpa Tesseract terpasang, semuanya tetap jalan: lapis kedua dilewati tanpa error.

## Lisensi

MIT
