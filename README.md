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
| `tesis_s2` | Tesis S2 Indonesia — skripsi S1 + pengesahan & pernyataan keaslian |
| `proposal_penelitian` | Proposal penelitian/hibah (latar belakang s.d. anggaran) |
| `case_study` | Laporan studi kasus naratif yang ringkas |
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

> **Catatan:** ingesti template `.docx`/`.pdf` milik pengguna (mis. template skripsi kampus) belum tersedia — itu masuk **Phase 2**. Saat ini template ekspor hanya yang dibundel: `.docx` `default_academic` dan LaTeX `article_id` / `ieee`.

## Instalasi Cepat

```bash
git clone https://github.com/dtacos227-design/research-mcp.git
cd research-mcp
uv sync
uv run research-mcp
```

Kompatibel dengan **Claude Code**, **OpenCode**, **Codex**, dan **Agy**.

## Lisensi

MIT
