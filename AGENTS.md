# AGENTS.md — Panduan Pengembangan Research MCP

Dokumen ini adalah panduan teknis utama (*Single Source of Truth*) bagi AI coding agent dan developer dalam merancang, membangun, menguji, serta memelihara **Research MCP**: sebuah *Model Context Protocol (MCP) Server* untuk riset ilmiah *end-to-end* yang **100% gratis**, kompatibel lintas CLI (**Claude Code, OpenCode, Codex, Agy**), serta memiliki interoperabilitas mendalam dengan **WordCraft MCP** dari ekosistem **ArtCraft Launcher** ([`storytold/craft-launcher`](https://github.com/storytold/craft-launcher) & [`storytold/wordcraft`](https://github.com/storytold/wordcraft)).

---

## 1. Penjelasan Proyek

### 1.1 Visi & Cakupan Sistem
Membangun satu server MCP riset yang menangani seluruh siklus kerja ilmiah secara terstruktur, terverifikasi, dan tanpa biaya langganan API:
1. **Literature Discovery & Citation Mapping**: Pencarian paralel ke database ilmiah gratis (250M+ paper), deduplikasi otomatis, validasi DOI, dan penelusuran grafik sitasi (*backward/forward*).
2. **PDF Ingestion & Deep Reading**: Unduh otomatis PDF *open-access* legal, ekstraksi teks menjadi Markdown terstruktur per *section*, dan pencarian teks penuh lokal (*full-text search*).
3. **Project & Citation Management**: Isolasi proyek riset, generator `citekey` deterministik, ekspor `references.bib` + `references.csl.json`, serta validasi anti-halusinasi sitasi.
4. **Modular Drafting & Scientific Visualization**: Penulisan draf per bab dalam format Markdown dan pembuatan grafik ilmiah resolusi tinggi (300 DPI PNG/SVG).
5. **Dual-Engine Publishing (WordCraft MCP + Standalone Pandoc/Tectonic)**:
   * **Jika WordCraft MCP aktif**: Berkolaborasi langsung dengan `wordcraft-cli mcp` (mode *headless* maupun *live desktop* via `--connect`) untuk menulis, mengatur *styles*, menyisipkan tabel/grafik/referensi, mengaktifkan *Track Changes* atas nama agen, melakukan inspeksi struktur (`inspect_document`), validasi *strict rendering* (`file.strictReport`), dan inspeksi visual halaman (`render_page`).
   * **Jika Mode Standalone / Publikasi LaTeX**: Mengekspor secara deterministik ke `.docx` (Pandoc + `python-docx`), `.tex` (template IEEE/Springer/Skripsi), dan `.pdf` (Tectonic).

### 1.2 Prinsip Inti (*Core Philosophy*)
* **100% Gratis & Local-First**: Seluruh API literatur menggunakan jalur gratis/open-access. Database, PDF, aset, dan draf tersimpan di mesin lokal pengguna.
* **Single Source of Truth**: Metadata paper, daftar pustaka, dan draf sumber dikelola oleh `Research MCP` dalam format **Markdown + Pandoc Citation (`[@citekey]`) + BibTeX + CSL-JSON**.
* **Zero-Hallucination Gate**: Tidak ada sitasi yang boleh masuk ke dokumen akhir (baik via WordCraft maupun Pandoc/LaTeX) tanpa lolos `verify_citations` terhadap database lokal proyek.
* **Context-Window Friendly**: Jangan pernah mengembalikan teks penuh PDF pada tahap pencarian. Terapkan alur bertahap: *Search -> Inspect Metadata -> Save & Parse -> Read Specific Section*.
* **Graceful Interoperability**: `Research MCP` berdiri mandiri tanpa mewajibkan WordCraft, namun otomatis meningkatkan kemampuan penulisan & *layouting* ketika **WordCraft MCP** tersedia di AI Client.

---

## 2. Arsitektur & Tech Stack (100% Gratis)

### 2.1 Ringkasan Tech Stack

| Lapisan | Teknologi / Library | Peran & Alasan Pemilihan |
| :--- | :--- | :--- |
| **Runtime & Packaging** | Python 3.11+ & `uv` | Cepat, deterministik, mudah di-install global (`uv tool install -e .`) untuk semua CLI. |
| **MCP Core** | `mcp` (`FastMCP`) | Server MCP standar (`stdio` transport) dengan validasi schema via Pydantic v2. |
| **HTTP & Resilience** | `httpx` (async) + `tenacity` | Panggilan paralel ke banyak provider akademik + *exponential backoff* untuk HTTP 429/503. |
| **Database Lokal** | **SQLite + FTS5** (`aiosqlite`) | Menyimpan proyek, paper, potongan *section* PDF, draf, dan *full-text search* lokal tanpa server eksternal. |
| **Literature APIs (Gratis)** | OpenAlex, Semantic Scholar, arXiv, PubMed, Crossref, Unpaywall | Cakupan 250M+ paper, grafik sitasi, metadata DOI resmi, dan tautan PDF *open-access* legal. |
| **PDF Parser** | `pymupdf4llm` (utama), `docling` (opsional) | Konversi cepat PDF ilmiah menjadi Markdown dengan mempertahankan heading dan struktur paragraf. |
| **Citation Engine** | `bibtexparser` + **Pandoc Citeproc** | Pembuatan `.bib`, `.csl.json`, serta pemformatan otomatis APA 7, IEEE, Vancouver, Chicago, MLA. |
| **Visualisasi Data** | `matplotlib`, `seaborn`, `numpy`, `pandas` | Membuat grafik ilmiah 300 DPI (PNG/SVG) di folder `assets/`. |
| **Standalone Exporters** | **Pandoc**, `python-docx`, **Tectonic** | Ekspor `.docx` berbasis template, `.tex`, dan kompilasi `.pdf` ringan (tanpa instalasi TeX Live 5 GB). |
| **WordCraft Integration** | **WordCraft (`wordcraft` & `wordcraft-cli`)** via ArtCraft Launcher | Word processor *clean-room* open-source (pure Rust, MIT/Apache-2.0) dengan MCP server native (`craft-mcp-server`) untuk penulisan `.docx`/`.pdf` langsung. |

### 2.2 Arsitektur Sistem & Kolaborasi Lintas-MCP

```text
          [AI Client / CLI: Claude Code | OpenCode | Codex | Agy]
                 │ (MCP stdio)                    │ (MCP stdio — opsional)
                 ▼                                ▼
      [Research MCP Server]             [WordCraft MCP Server]
      (Otak Riset & Sitasi)             (Mesin Penulisan, Layout & Review)
 ┌──────────┬──────────┬──────────┐     ┌──────────────────────────────────┐
 ▼          ▼          ▼          ▼     │ Binary: wordcraft-cli mcp        │
[Providers][Parsers] [SQLite]  [Bridge] │ Mode 1: Headless (confined roots)│
- OpenAlex - pymupdf - Projects- Pandoc │ Mode 2: --connect 127.0.0.1:7981 │
- SemSchol - Section - Papers  - Tectonic│ Tools: list_commands, execute,  │
- arXiv      Split   - FTS5    - Handoff│  batch, insert_table, type_text, │
- PubMed             - BibTeX    Engine │  inspect_document, render_page,  │
- Crossref           - CSL-JSON         │  save_document, strictReport     │
- Unpaywall                       │     └─────────────────┬────────────────┘
                                  ▼                       ▼
              [Shared Workspace: ~/.research-mcp/workspaces/<slug>/]
              ├── pdfs/          (PDF sumber yang diunduh / diimpor)
              ├── parsed/        (Markdown hasil ekstraksi per paper)
              ├── drafts/        (Draf bab .md terverifikasi)
              ├── assets/        (Grafik 300 DPI PNG/SVG)
              ├── scripts/       (Opsional: job.json untuk wordcraft-cli run)
              ├── references.bib & references.csl.json
              └── exports/       (Output .docx / .tex / .pdf / page_*.png)
```

### 2.3 Skema Database Lokal (SQLite + FTS5)
Agent wajib mengimplementasikan tabel berikut di `src/research_mcp/db/database.py`:

* `projects`:
  * `id TEXT PRIMARY KEY`, `slug TEXT UNIQUE NOT NULL`, `name TEXT NOT NULL`, `description TEXT`, `created_at TEXT`, `updated_at TEXT`.
* `papers`:
  * `id TEXT PRIMARY KEY`, `project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE`, `citekey TEXT NOT NULL`, `doi TEXT`, `title TEXT NOT NULL`, `authors_json TEXT NOT NULL`, `year INTEGER`, `venue TEXT`, `abstract TEXT`, `citation_count INTEGER DEFAULT 0`, `pdf_path TEXT`, `source_provider TEXT`, `bibtex_raw TEXT`, `csl_json TEXT`, `created_at TEXT`, `UNIQUE(project_id, citekey)`, `UNIQUE(project_id, doi)`.
* `paper_sections`:
  * `id INTEGER PRIMARY KEY AUTOINCREMENT`, `paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE`, `section_order INTEGER NOT NULL`, `heading TEXT NOT NULL`, `content TEXT NOT NULL`, `word_count INTEGER DEFAULT 0`.
* `paper_sections_fts` (Virtual Table `FTS5`):
  * Mengindeks `heading` dan `content` beserta `paper_id` dan `project_id` (`UNINDEXED`) untuk pencarian kutipan cepat dalam satu proyek.
* `draft_sections`:
  * `id INTEGER PRIMARY KEY AUTOINCREMENT`, `project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE`, `filename TEXT NOT NULL`, `section_order INTEGER NOT NULL`, `title TEXT`, `word_count INTEGER DEFAULT 0`, `updated_at TEXT`, `UNIQUE(project_id, filename)`.

### 2.4 Struktur Direktori Proyek

```text
research-mcp/
├── AGENTS.md                       # Dokumen panduan ini
├── pyproject.toml                  # Konfigurasi proyek, dependensi, & script entry point (uv)
├── .env.example                    # Variabel lingkungan opsional
├── src/
│   └── research_mcp/
│       ├── __init__.py
│       ├── server.py               # Entry point FastMCP (registrasi tools, resources, prompts)
│       ├── config.py               # Path absolut workspace, env vars, logging ke stderr
│       ├── models.py               # Pydantic v2 schemas (Paper, HandoffPayload, ExportResult)
│       ├── db/
│       │   ├── database.py         # Init SQLite, WAL mode, migrasi skema, FTS5
│       │   └── repository.py       # Data access layer (projects, papers, sections, drafts)
│       ├── providers/
│       │   ├── base.py             # Abstract Base Class provider literatur
│       │   ├── aggregator.py       # Parallel search + deduplikasi (DOI / normalized title)
│       │   ├── openalex.py
│       │   ├── semantic_scholar.py
│       │   ├── arxiv.py
│       │   ├── pubmed.py
│       │   ├── crossref.py
│       │   └── unpaywall.py
│       ├── parsers/
│       │   ├── pdf_extractor.py    # Download PDF legal & konversi via pymupdf4llm
│       │   └── sectionizer.py      # Pemecah Markdown PDF per heading/section
│       ├── citations/
│       │   ├── bibtex.py           # Citekey deterministik, generator .bib & .csl.json
│       │   └── verifier.py         # Audit [@citekey] pada draf vs database lokal
│       ├── writers/
│       │   ├── draft_manager.py    # Manajemen bab draf Markdown per proyek
│       │   └── chart_runner.py     # Runner grafik matplotlib/seaborn terkunci ke assets/
│       ├── exporters/
│       │   ├── pandoc_engine.py    # Wrapper Pandoc + Citeproc
│       │   ├── docx_polisher.py    # Post-processing .docx via python-docx
│       │   ├── latex_engine.py     # Render template .tex & kompilasi via Tectonic
│       │   └── wordcraft_bridge.py # Deteksi ArtCraft/WordCraft, handoff packager, & job.json builder
│       └── templates/
│           ├── csl/                # apa.csl, ieee.csl, vancouver.csl, chicago.csl
│           ├── docx/               # default_academic.docx, thesis_id.docx
│           └── latex/              # ieee.tex, springer.tex, article_id.tex
└── tests/
    ├── unit/
    └── integration/
```

---

## 3. Detail Riset & Integrasi Ekosistem ArtCraft Launcher / WordCraft MCP

Bagian ini merangkum spesifikasi teknis aktual dari **`storytold/craft-launcher`** dan **`storytold/wordcraft`** agar *agent* dapat membangun integrasi yang 100% akurat tanpa asumsi yang keliru.

### 3.1 Konteks ArtCraft Launcher (`storytold/craft-launcher`)
* **ArtCraft Launcher** adalah *native Rust launcher* (`egui`) yang menginstal, memverifikasi checksum SHA-256, dan memperbarui seluruh **Craft App Suite** (WordCraft, PdfCraft, DeckCraft, GridCraft, VectorCraft, PhotoCraft, DesignCraft, dll.).
* **Lokasi Instalasi Aplikasi oleh ArtCraft Launcher (Wajib Dideteksi oleh `wordcraft_bridge.py`)**:
  * **macOS**: `/Applications/WordCraft.app` (binary CLI dapat berada di PATH atau di dalam bundle/instalasi terpisah `wordcraft-cli`).
  * **Windows**: `%LOCALAPPDATA%\Programs\ArtCraft\WordCraft` (default *per-user*) atau `C:\Program Files\ArtCraft\WordCraft` (*all-users*).
  * **Linux**: `/usr/bin` (`.deb`/`.rpm`), AppImage di `~/Applications`, atau sandbox Flatpak di `~/.var/app/ai.storyteller.artcraft-launcher/data/artcraft-launcher/apps/wordcraft`.
  * **FreeBSD**: `/usr/local` atau `~/.local`.
* Seluruh CLI di Craft App Suite berbagi protokol inti (`craft-mcp-server`) dan flag isolasi folder yang seragam (`--automation-read-root` & `--automation-write-root`).

### 3.2 Spesifikasi Aktual WordCraft MCP (`storytold/wordcraft`)
WordCraft (`wordcraft` & `wordcraft-cli`) adalah *word processor* *clean-room* open-source berbasis Rust yang dirancang khusus untuk dapat dikendalikan manusia maupun AI Agent melalui 422+ *engine commands*.

#### A. Dua Mode Eksekusi MCP
1. **Mode Headless (Direkomendasikan untuk Otomasi & Keamanan)**:
   ```bash
   wordcraft-cli mcp \
     --author "Research Agent" \
     --automation-read-root "$HOME/.research-mcp/workspaces" \
     --automation-write-root "$HOME/.research-mcp/workspaces"
   ```
   * Tidak membuka jendela GUI, cepat, dan terisolasi ketat pada folder *read/write root* yang diizinkan.
2. **Mode Live Desktop Bridge (Pengguna Melihat AI Menulis Langsung)**:
   ```bash
   # Terminal 1: jalankan aplikasi desktop dengan control channel
   wordcraft --control 7981

   # Terminal 2 / Konfigurasi MCP Client:
   wordcraft-cli mcp --connect 127.0.0.1:7981
   ```
   * Bridge otomatis membaca *control key* dari env var `WORDCRAFT_CONTROL_KEY` atau file key yang dibuat aplikasi desktop (`~/.config/wordcraft/control-key.local-7981` di Linux, `~/Library/Application Support/WordCraft/` di macOS, `%APPDATA%\WordCraft\` di Windows).
   * Bridge tetap bisa start sebelum aplikasi desktop dibuka (akan mengembalikan pesan *"not reachable … will retry on each call"* dan langsung terhubung begitu WordCraft dibuka).
   * **ATURAN KRITIS**: Flag `--connect` **TIDAK BOLEH** digabungkan dengan `--automation-read-root` atau `--automation-write-root` (WordCraft akan menolak dengan error karena aplikasi desktop yang berjalan belum mendukung *confinement*).

#### B. Daftar Tool & Resource Aktual WordCraft MCP
Saat AI Client mengorkestrasi `Research MCP` + `WordCraft MCP`, *handoff instructions* yang dihasilkan oleh `Research MCP` **wajib** merujuk pada nama tool WordCraft yang sebenarnya:
* **Manajemen Dokumen**: `new_document` (`blank`, `sample`, `letter`, `resume`, `report`), `open_document`, `save_document` (mendukung `.docx`, `.odt`, `.rtf`, `.html`, `.md`, `.tex`, `.txt`, `.json`, serta ekspor `.pdf` & `.png`), `list_documents` (mendukung multi-dokumen lewat parameter `doc: <id>`).
* **Penulisan & Penyuntingan**: `type_text` (`paragraphs: true`, batas 10.000 baris per panggilan), `select_text`, `insert_table` (`{values: [[...]], header: true, style?}` untuk tabel terisi dalam 1 panggilan), `execute` (`{command, params, doc?}`), `batch` (menjalankan daftar command berurutan), `list_commands` (mencari dari 270–422 command).
* **Inspeksi & Verifikasi**: `get_text`, `inspect_document` (verifikasi struktur tanpa screenshot), `render_page` (render halaman menjadi PNG untuk inspeksi visual), `parity`.
* **Khusus Mode `--connect` (Desktop)**: `screenshot`, `click`, `key`, `ui_inspect`.
* **Resources**: `wordcraft://document`, `wordcraft://commands`.

#### C. Fitur Akademik & Keamanan Kritis di WordCraft
1. **Format File Native**: WordCraft dapat membuka dan menyimpan `.docx`, `.md`, `.tex`, serta mengekspor `.pdf` (teks dapat diseleksi, tautan & bookmark aktif).
2. **Track Changes oleh Agen**: Selalu set identitas penulis dengan `--author "Research Agent"` atau `execute("file.setAuthor", {"name": "Research Agent"})`, dan aktifkan *track changes* secara eksplisit dengan `execute("review.trackChanges", {"value": true})` (**JANGAN** panggil tanpa `value`, karena tanpa `value` perintah ini bersifat *toggle* yang justru bisa mematikannya).
3. **Strict Render Quality Gate (`file.strictReport` & `strict: true`)**: WordCraft menyediakan `file.strictReport` dan parameter `strict: true` pada `file.save` / `file.exportPdf` / `file.exportPng` (serta CLI `wordcraft-cli convert in.docx out.pdf --strict` dengan exit code `3`) yang menolak hasil ekspor secara tegas bila ada karakter/glyph yang hilang (*missing glyphs*) atau gambar yang gagal dimuat (*missing pictures*).
4. **Styling Terstruktur**: Gunakan `styles.modify`, `format.set` (`CharProps`), dan `para.set` (`ParaProps`) dengan satuan *points*.
5. **Integrasi Referensi & Zotero**: WordCraft memiliki dukungan *References* (`references.citationStyle`, `references.bibliography`, `references.toc`, `references.caption`, `references.tableOfFigures`), impor `.bib`/`.ris`/`.csl.json` & `.csl`, serta integrasi Zotero lokal.
6. **Batasan & Edge Cases yang Harus Ditangani Agent**:
   * **Delivery Failure (`outcome unknown`)**: Pada mode `--connect`, jika terjadi kegagalan tulis/balasan pada operasi mutasi, WordCraft **tidak** mengulang perintah otomatis dan mengembalikan error *outcome unknown*. Agent **wajib** memanggil `inspect_document` terlebih dahulu sebelum memutuskan mengulang perintah.
   * **Caret setelah Generated List**: Menyisipkan `references.bibliography`, `references.toc`, atau `references.tableOfFigures` dapat meninggalkan kursor (*caret*) di dalam judul daftar pada beberapa build alpha; selalu panggil `caret.docEnd` setelahnya atau sisipkan daftar tersebut pada urutan akhir.
   * **Batas Confinement**: Jika menggunakan `--automation-read-root` saja tanpa `--automation-write-root`, WordCraft tidak memiliki izin menulis file apa pun. Selalu arahkan **kedua** flag ke folder root workspace `Research MCP`.

### 3.3 Tiga Jalur Kolaborasi `Research MCP` -> `WordCraft MCP`

1. **Jalur A — *Live Authoring* (Penulisan Langsung di WordCraft via MCP)**:
   * `Research MCP` memverifikasi sitasi (`verify_citations`), memformat teks sitasi & daftar pustaka via Pandoc Citeproc, lalu melalui `prepare_wordcraft_handoff(..., mode="live_authoring")` mengembalikan **Writing Blueprint** (urutan heading, paragraf, tabel, path absolut grafik di `assets/`, serta daftar pustaka final).
   * AI Agent menggunakan tool `WordCraft MCP` (`new_document` -> `styles.modify` -> `type_text` / `insert_table` / `execute("insert.picture")` -> `execute("file.strictReport")` -> `render_page` -> `save_document` ke `.docx` & `.pdf`).
2. **Jalur B — *Seed DOCX + WordCraft Polish* (Ideal untuk Skripsi/Tesis Panjang)**:
   * `Research MCP` mengekspor draf awal `.docx` beserta sitasi & daftar pustaka rapi via Pandoc (`prepare_wordcraft_handoff(..., mode="seed_docx")`) ke `workspaces/<slug>/exports/`.
   * AI Agent memanggil `wordcraft.open_document` pada file tersebut untuk menyempurnakan *Styles*, menambahkan *Table of Contents*, *Track Changes* / komentar review, memverifikasi tampilan dengan `render_page` + `file.strictReport`, lalu mengekspor `.pdf` final.
3. **Jalur C — *Headless Script Pipeline* (`wordcraft-cli run --script job.json`)**:
   * Untuk ekspor batch deterministik tanpa konsumsi token berlebih, `Research MCP` dapat menghasilkan file `scripts/job.json` (maksimal 4 MiB) berisi rangkaian command WordCraft dan daftar output `["out.docx", "out.pdf"]`, lalu menjalankannya via `wordcraft-cli run --script` (atau menyerahkannya ke user/agent).
4. **Fallback Otomatis**:
   * Bila WordCraft MCP tidak aktif atau `wordcraft-cli` tidak terpasang, `Research MCP` otomatis menggunakan `export_to_docx` (Pandoc + `python-docx`) atau `export_to_latex` (Tectonic).

---

## 4. Kompatibilitas Multi-CLI (Claude Code, OpenCode, Codex, Agy)

### 4.1 Aturan Wajib Kompatibilitas CLI
1. **Haram `print()` ke `stdout`**: `stdout` dikhususkan untuk frame JSON-RPC MCP. Seluruh log wajib menggunakan `logging.StreamHandler(sys.stderr)`.
2. **Workspace Path Absolut**: Jangan bergantung pada CWD. Gunakan default `~/.research-mcp/workspaces` yang dapat ditimpa lewat env var `RESEARCH_MCP_WORKSPACE`.
3. **Entry Point Standar**: Daftarkan `[project.scripts] research-mcp = "research_mcp.server:main"` di `pyproject.toml`.

### 4.2 Contoh Konfigurasi Gabungan (`research-mcp` + `wordcraft`)

#### Claude Code
```bash
# 1. Pasang Research MCP
claude mcp add research-mcp \
  -e RESEARCH_MCP_EMAIL="email@kamu.com" \
  -e RESEARCH_MCP_WORKSPACE="$HOME/.research-mcp/workspaces" \
  -- uv --directory /path/to/research-mcp run research-mcp

# 2a. Pasang WordCraft MCP (Mode Headless + Shared Workspace Confinement)
claude mcp add wordcraft -- wordcraft-cli mcp \
  --author "Research Agent" \
  --automation-read-root "$HOME/.research-mcp/workspaces" \
  --automation-write-root "$HOME/.research-mcp/workspaces"

# 2b. (Alternatif) Pasang WordCraft MCP Mode Live Desktop (tanpa flag root)
# Jalankan `wordcraft --control 7981` lebih dulu
claude mcp add wordcraft-app -- wordcraft-cli mcp --connect 127.0.0.1:7981
```

#### OpenCode (`~/.config/opencode/opencode.json`)
```json
{
  "mcp": {
    "research-mcp": {
      "type": "local",
      "command": ["uv", "--directory", "/path/to/research-mcp", "run", "research-mcp"],
      "environment": {
        "RESEARCH_MCP_EMAIL": "email@kamu.com",
        "RESEARCH_MCP_WORKSPACE": "/home/user/.research-mcp/workspaces"
      },
      "enabled": true
    },
    "wordcraft": {
      "type": "local",
      "command": [
        "wordcraft-cli", "mcp",
        "--author", "Research Agent",
        "--automation-read-root", "/home/user/.research-mcp/workspaces",
        "--automation-write-root", "/home/user/.research-mcp/workspaces"
      ],
      "enabled": true
    }
  }
}
```

#### Codex CLI (`~/.codex/config.toml`)
```toml
[mcp_servers.research-mcp]
command = "uv"
args = ["--directory", "/path/to/research-mcp", "run", "research-mcp"]

[mcp_servers.research-mcp.env]
RESEARCH_MCP_EMAIL = "email@kamu.com"
RESEARCH_MCP_WORKSPACE = "/home/user/.research-mcp/workspaces"

[mcp_servers.wordcraft]
command = "wordcraft-cli"
args = [
  "mcp",
  "--author", "Research Agent",
  "--automation-read-root", "/home/user/.research-mcp/workspaces",
  "--automation-write-root", "/home/user/.research-mcp/workspaces"
]
```

#### Agy & Client MCP JSON Standar
```json
{
  "mcpServers": {
    "research-mcp": {
      "command": "uv",
      "args": ["--directory", "/path/to/research-mcp", "run", "research-mcp"],
      "env": {
        "RESEARCH_MCP_EMAIL": "email@kamu.com",
        "RESEARCH_MCP_WORKSPACE": "/home/user/.research-mcp/workspaces"
      }
    },
    "wordcraft": {
      "command": "wordcraft-cli",
      "args": [
        "mcp",
        "--author", "Research Agent",
        "--automation-read-root", "/home/user/.research-mcp/workspaces",
        "--automation-write-root", "/home/user/.research-mcp/workspaces"
      ]
    }
  }
}
```

---

## 5. Rules & Standar Pengembangan (Wajib Dipatuhi Agent)

### 5.1 Kode, Jaringan, & Keamanan
1. **Strict Typing & Validation**: Semua fungsi publik dan parameter MCP tool wajib menggunakan *type hints* Python 3.11+ dan model Pydantic v2.
2. **Async I/O & Fault Isolation**: Panggilan multi-provider wajib menggunakan `httpx.AsyncClient` + `asyncio.gather(..., return_exceptions=True)` dengan timeout eksplisit (default 30 detik). Kegagalan satu API tidak boleh menggagalkan seluruh pencarian.
3. **Polite Pool & Rate Limit**: OpenAlex, Crossref, dan Unpaywall wajib mengirim header `User-Agent: ResearchMCP/0.1 (mailto:<RESEARCH_MCP_EMAIL>)`. Gunakan `tenacity` (*exponential backoff*) untuk HTTP 429/503.
4. **Path Traversal Protection**: Semua akses file wajib memvalidasi `target.resolve().is_relative_to(project_dir.resolve())` agar operasi tidak pernah keluar dari folder proyek.
5. **Chart Sandbox**: `generate_chart` harus membatasi eksekusi kode visualisasi hanya untuk `matplotlib`/`seaborn`/`numpy`/`pandas` dengan output terkunci ke folder `assets/`.

### 5.2 Desain MCP Tool & Anti-Halusinasi
1. **Respons Ringkas**: `search_papers` default 10–15 hasil dengan abstrak terpotong maksimal ~300 karakter.
2. **Actionable Errors**: Tangkap semua exception pada level tool dan kembalikan pesan terstruktur berisi penyebab + rekomendasi perbaikan bagi LLM.
3. **Citekey Deterministik**: Format `authorlastnameYYYYfirstword` (contoh: `vaswani2017attention`), dengan akhiran `a`, `b`, dst. bila terjadi bentrok dalam proyek yang sama.
4. **Citation Verification Gate**: `export_to_docx`, `export_to_latex`, dan `prepare_wordcraft_handoff` wajib menjalankan `verify_citations` lebih dulu (kecuali `strict_citations=False`). Bila ada sitasi tidak terdaftar, hentikan proses dan laporkan daftarnya.
5. **Deteksi Binary Sistem**: Sebelum memanggil `pandoc`, `tectonic`, atau `wordcraft-cli`, cek dengan `shutil.which()` (plus lokasi standar ArtCraft Launcher). Jika tidak ditemukan, kembalikan pesan diagnostik + petunjuk instalasi.

### 5.3 Testing & Quality Gate
* Setiap provider wajib memiliki unit test dengan mock HTTP (`pytest`, `pytest-asyncio`, `respx`).
* Setiap fase wajib lulus `ruff check .`, `ruff format --check .`, dan `pytest` sebelum berlanjut ke fase berikutnya.

---

## 6. Spesifikasi Lengkap Fitur (MCP Tools, Resources, & Prompts)

### 6.1 MCP Tools (21 Tools Terorganisir)

#### A. Project & Workspace Management
1. `create_project(name: str, description: str = "")` — Membuat proyek baru dan struktur direktori terisolasi di `workspaces/<slug>/`.
2. `list_projects()` — Daftar semua proyek beserta jumlah paper, PDF, dan bab draf.
3. `get_project_status(project_id: str)` — Status lengkap proyek: path absolut workspace, daftar sitasi, bab draf, grafik, dan hasil ekspor.

#### B. Discovery & Literature Search
4. `search_papers(query: str, sources: list[str] = ["openalex", "semantic_scholar", "arxiv"], year_from: int | None = None, year_to: int | None = None, limit: int = 10)` — Pencarian paralel multi-sumber + deduplikasi DOI/judul.
5. `get_citation_graph(identifier: str, direction: Literal["references", "citations"] = "citations", limit: int = 15)` — Pelacakan referensi/sitasi via Semantic Scholar atau OpenAlex.
6. `resolve_doi(doi: str)` — Metadata resmi & BibTeX dari Crossref + cek ketersediaan PDF legal via Unpaywall.

#### C. PDF Ingestion, Parsing, & Local Search
7. `save_paper_to_project(project_id: str, identifier: str, download_pdf: bool = True)` — Menyimpan metadata, generate `citekey` + BibTeX + CSL-JSON, serta mengunduh & mem-parse PDF ke tabel `paper_sections` & FTS5.
8. `import_local_pdf(project_id: str, file_path: str, doi: str | None = None)` — Mengimpor PDF lokal milik pengguna (misal dari langganan kampus) ke proyek.
9. `list_paper_sections(project_id: str, citekey: str)` — Daftar heading dan jumlah kata per bagian paper.
10. `read_paper_section(project_id: str, citekey: str, section_name: str)` — Membaca bagian spesifik (*Methods*, *Results*, dll.) tanpa memboroskan context window.
11. `search_local_library(project_id: str, query: str, limit: int = 10)` — Pencarian teks penuh lintas seluruh PDF proyek via SQLite FTS5.

#### D. Citation & Verification Manager
12. `generate_bibliography(project_id: str, formats: list[str] = ["bibtex", "csl_json"])` — Membangun ulang `references.bib` dan `references.csl.json`.
13. `verify_citations(project_id: str, markdown_text: str | None = None)` — Audit semua `[@citekey]` pada draf vs database lokal; mendeteksi sitasi palsu dan paper yang belum disitir.

#### E. Drafting & Scientific Assets
14. `save_draft_section(project_id: str, filename: str, content: str, order: int | None = None)` — Menyimpan/memperbarui bab draf Markdown (`01_abstract.md`, `02_intro.md`, dll.).
15. `read_draft_section(project_id: str, filename: str)` — Membaca isi bab draf tertentu.
16. `generate_chart(project_id: str, filename: str, python_code: str, dpi: int = 300)` — Menjalankan skrip grafik ke `assets/` dan mengembalikan path absolut + sintaks Markdown.

#### F. WordCraft MCP Interoperability Tools
17. `check_wordcraft_env(project_id: str | None = None)` — Memeriksa:
    * Ketersediaan binary `wordcraft-cli` (di `PATH` maupun direktori instalasi ArtCraft Launcher per OS).
    * Status live desktop port `127.0.0.1:7981` dan keberadaan file control key.
    * Mengembalikan path absolut workspace root serta snippet konfigurasi `--automation-read-root` & `--automation-write-root` yang tepat.
18. `prepare_wordcraft_handoff(project_id: str, csl_style: str = "apa", mode: Literal["live_authoring", "seed_docx", "batch_script"] = "live_authoring", strict_citations: bool = True)` — Menyiapkan paket *handoff* terverifikasi untuk WordCraft:
    * **`live_authoring`**: Menjalankan `verify_citations`, merender sitasi *in-text* & daftar pustaka final via Pandoc Citeproc, mengumpulkan path absolut grafik di `assets/`, dan mengembalikan **Writing Blueprint** lengkap dengan urutan pemanggilan tool WordCraft (`new_document` -> `styles.modify` -> `type_text` -> `insert_table` -> `execute("insert.picture")` -> `execute("file.strictReport")` -> `render_page` -> `save_document`).
    * **`seed_docx`**: Mengekspor `.docx` awal via Pandoc ke `exports/` dan mengembalikan path absolutnya untuk langsung dibuka via `wordcraft.open_document` dan dipoles (TOC, Styles, Track Changes, `render_page`, PDF export).
    * **`batch_script`**: Menghasilkan `scripts/job.json` siap eksekusi untuk `wordcraft-cli run --script` (menghasilkan `.docx` & `.pdf` sekaligus secara deterministik).

#### G. Multi-Format Standalone Exporters (Word, LaTeX, PDF, Markdown)
19. `export_to_docx(project_id: str, output_name: str, csl_style: str = "apa", template_name: str = "default_academic", polish: bool = True, strict_citations: bool = True)` — Pandoc Citeproc + `--reference-doc` + `docx_polisher`.
20. `export_to_latex(project_id: str, output_name: str, template_name: str = "ieee", compile_pdf: bool = True, strict_citations: bool = True)` — Render template `.tex` + `.bib` dan opsional kompilasi `.pdf` via Tectonic.
21. `export_to_markdown_bundle(project_id: str, output_name: str)` — Menggabungkan seluruh bab menjadi 1 Markdown utuh dengan daftar pustaka terformat.

### 6.2 MCP Resources
* `research://projects` — Daftar semua proyek riset.
* `research://project/{project_id}/bibliography` — Isi `references.bib` terkini.
* `research://project/{project_id}/csl-json` — Isi `references.csl.json`.
* `research://project/{project_id}/outline` — Urutan bab draf + statistik jumlah kata.
* `research://templates` — Daftar template `.csl`, `.docx`, dan `.tex` yang tersedia.

### 6.3 MCP Prompts (Workflow Bawaan)
* `systematic_literature_review(topic, year_from)` — Memandu pencarian multi-sumber, seleksi, ekstraksi section kunci, matriks sintesis, dan draf tinjauan pustaka.
* `write_in_wordcraft(project_id, csl_style, target_filename)` — **Workflow lintas-MCP**: memandu AI mengecek `check_wordcraft_env`, memverifikasi sitasi, mengambil *handoff payload* dari `Research MCP`, menulis langsung ke **WordCraft MCP** (dengan `file.setAuthor` + `review.trackChanges {"value": true}` bila mode revisi), memverifikasi dengan `file.strictReport` & `render_page`, lalu menyimpan `.docx` + `.pdf`. Jika WordCraft tidak tersedia, otomatis *fallback* ke `export_to_docx`.
* `draft_latex_paper(project_id, template_name)` — Memandu penyusunan struktur IMRaD, validasi sitasi, dan kompilasi PDF via Tectonic.
* `peer_review_in_wordcraft(project_id, docx_path)` — Membaca draf/dokumen, mengaudit klaim & sitasi terhadap library lokal, lalu menulis saran revisi langsung di WordCraft menggunakan **Track Changes** dan komentar margin.

---

## 7. Rencana Pembuatan (Implementation Roadmap)

Kerjakan proyek secara berurutan per fase. Setiap fase wajib memenuhi **Kriteria Verifikasi** sebelum melanjutkan ke fase berikutnya.

### Fase 0: Fondasi Proyek, Config CLI-Safe, & Kerangka Server
- [ ] Inisialisasi proyek dengan `uv` (`pyproject.toml`, Python 3.11+, `[project.scripts] research-mcp = "research_mcp.server:main"`).
- [ ] Pasang dependensi inti: `mcp`, `httpx`, `pydantic`, `pydantic-settings`, `tenacity`, `aiosqlite`, `pytest`, `pytest-asyncio`, `respx`, `ruff`.
- [ ] Implementasi `config.py` (path absolut `~/.research-mcp/workspaces`, logging eksklusif ke `stderr`), `models.py`, dan `server.py`.
- [ ] **Verifikasi**: `uv run research-mcp` berjalan mulus tanpa 1 byte pun output non-JSON-RPC di `stdout`, terdeteksi normal di MCP Inspector.

### Fase 1: Database Lokal (SQLite + FTS5) & Literature Providers
- [ ] Implementasi `db/database.py` (WAL mode + FTS5) dan `db/repository.py`.
- [ ] Implementasi tools: `create_project`, `list_projects`, `get_project_status`.
- [ ] Implementasi providers: `openalex.py`, `semantic_scholar.py`, `arxiv.py`, `pubmed.py`, `crossref.py`, `unpaywall.py`, serta `aggregator.py` (parallel + deduplikasi).
- [ ] Implementasi tools: `search_papers`, `get_citation_graph`, `resolve_doi`.
- [ ] **Verifikasi**: Unit test mock (`respx`) untuk tiap provider + tes integrasi pencarian nyata lintas sumber tanpa duplikasi.

### Fase 2: PDF Ingestion, Parsing, & Local Full-Text Search
- [ ] Tambahkan `pymupdf4llm` ke dependensi.
- [ ] Implementasi `parsers/pdf_extractor.py` dan `parsers/sectionizer.py`.
- [ ] Implementasi tools: `save_paper_to_project`, `import_local_pdf`, `list_paper_sections`, `read_paper_section`, `search_local_library`.
- [ ] **Verifikasi**: Berhasil mengunduh 1 paper *open-access*, menyimpannya per section ke SQLite, dan menemukan frasa spesifik lewat `search_local_library`.

### Fase 3: Citation Manager & Anti-Hallucination Engine
- [ ] Tambahkan `bibtexparser` ke dependensi.
- [ ] Implementasi `citations/bibtex.py` (citekey deterministik, generator `.bib` dan `.csl.json`).
- [ ] Implementasi `citations/verifier.py` (parser sintaks Pandoc `[@key]`, `[@key1; @key2]`, `@key`).
- [ ] Implementasi tools: `generate_bibliography`, `verify_citations`.
- [ ] **Verifikasi**: Unit test mendeteksi 100% sitasi valid dan memblokir sitasi palsu/typo sebelum ekspor.

### Fase 4: Drafting, Visualisasi, & Standalone Exporters (DOCX & LaTeX)
- [ ] Tambahkan `python-docx`, `matplotlib`, `seaborn`, `numpy`, `pandas` ke dependensi.
- [ ] Sediakan template bawaan: `.csl` (APA, IEEE, Vancouver, Chicago), `.docx` (`default_academic.docx`), dan `.tex` (`ieee.tex`, `article_id.tex`).
- [ ] Implementasi `writers/draft_manager.py`, `writers/chart_runner.py`, `exporters/pandoc_engine.py`, `exporters/docx_polisher.py`, dan `exporters/latex_engine.py`.
- [ ] Implementasi tools: `save_draft_section`, `read_draft_section`, `generate_chart`, `export_to_docx`, `export_to_latex`, `export_to_markdown_bundle`.
- [ ] **Verifikasi**: Dari draf Markdown berisi tabel, 1 grafik 300 DPI, dan 3 sitasi, berhasil menghasilkan `.docx` rapi serta `.tex` + `.pdf` (Tectonic) tanpa error.

### Fase 5: WordCraft MCP Interoperability & Handoff Engine
- [ ] Implementasi `exporters/wordcraft_bridge.py`:
  * Deteksi `wordcraft-cli` di `PATH` dan jalur instalasi ArtCraft Launcher per OS (macOS, Windows, Linux, FreeBSD).
  * Pengecekan opsional port live desktop `127.0.0.1:7981` & file control key.
  * Builder *Writing Blueprint* (`live_authoring`), *Seed DOCX* (`seed_docx`), dan generator `job.json` (`batch_script`).
- [ ] Implementasi tools: `check_wordcraft_env`, `prepare_wordcraft_handoff`.
- [ ] Implementasi prompts: `write_in_wordcraft`, `peer_review_in_wordcraft`.
- [ ] **Verifikasi**: Unit test untuk semua mode handoff + uji coba integrasi di CLI saat `research-mcp` dan `wordcraft` aktif bersamaan (berhasil menulis dokumen, lolos `file.strictReport`, menghasilkan `render_page` PNG, dan menyimpan `.docx` + `.pdf` di shared workspace).

### Fase 6: MCP Resources, Prompts, & Packaging Multi-CLI
- [ ] Implementasi seluruh MCP Resources (`research://...`) dan sisa MCP Prompts (`systematic_literature_review`, `draft_latex_paper`).
- [ ] Buat `README.md` lengkap + skrip helper opsional untuk registrasi cepat di Claude Code, OpenCode, Codex, dan Agy.
- [ ] Jalankan full test suite (`pytest`) dan linting (`ruff check .`, `ruff format --check .`).
- [ ] **Verifikasi**: Simulasi *end-to-end* siklus riset penuh dari pencarian literatur hingga publikasi akhir (.docx via WordCraft & .pdf via LaTeX/Tectonic).

---

## 8. Prasyarat Lingkungan Eksternal

1. **Pandoc (v3.0+)** — Wajib untuk Citeproc, ekspor `.docx`/`.tex`, dan pemformatan sitasi *handoff*.
2. **Tectonic** — Wajib untuk kompilasi LaTeX ke PDF (single binary ringan, otomatis mengunduh paket TeX saat dibutuhkan).
3. **ArtCraft Launcher & WordCraft (`wordcraft` + `wordcraft-cli`)** *(Opsional, sangat disarankan)* — Diinstal via [ArtCraft Launcher](https://github.com/storytold/craft-launcher) atau [WordCraft Releases](https://github.com/storytold/wordcraft/releases) untuk penulisan langsung `.docx`, styling live, *Track Changes*, `file.strictReport`, dan inspeksi visual halaman (`render_page`) melalui MCP.
4. **Font Akademik Standar** — Disarankan *Liberation Serif* atau *TeX Gyre Termes* agar *strict glyph check* di WordCraft dan kompilasi Tectonic konsisten lintas OS.