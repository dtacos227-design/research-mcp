# Research Styles & Formatting Registry — Design

_Tanggal: 2026-11-22 · Status: Draft untuk approval · Pendekatan: B (registry) → C (ingest template)_

---

## 1. Intent & Why

Research MCP saat ini hanya mengenal satu bentuk keluaran: artikel akademik gaya
Barat (IMRaD, IEEE, APA). Empat prompt yang ada (`systematic_literature_review`,
`draft_latex_paper`, `write_in_wordcraft`, `peer_review_in_wordcraft`) semuanya
mengasumsikan struktur itu.

Pengguna nyata mencakup hampir semua gaya riset: mahasiswa S1 (skripsi), S2/S3
(tesis/disertasi), dosen (proposal hibah, artikel jurnal), dan praktisi (studi
kasus, sintesis cepat untuk pengambil kebijakan). Setiap gaya punya **tiga hal**
yang berbeda dan saat ini tersebar/tidak ada:

1. **Struktur** — bab dan urutannya (BAB I–V vs IMRaD vs proposal).
2. **Format** — ukuran kertas, margin, spasi, penomoran bab, font, front matter
   (margin skripsi 4-3-3-3 cm, spasi 2×, halaman romawi untuk preamble).
3. **Panduan menulis** — bagaimana menulis tiap bab dengan benar.

Akar masalahnya: format hardcoded di `_style_steps()` WordCraft, template Pandoc
statis, dan struktur hardcoded di teks prompt. Tidak ada satu sumber kebenaran.
Ini juga penyebab BUG-4 (tidak ada setup halaman akademik) dan EXP-3
(tidak ada `doc_type`) di audit 2026-11-22.

**Tujuan desain ini: satu registry gaya riset yang menjadi sumber kebenaran
tunggal untuk struktur, format, dan panduan — dibaca oleh drafting, export
WordCraft, export Pandoc DOCX, dan export LaTeX sekaligus.**

---

## 2. Goals, Non-Goals, Anti-Goals

### Goals
- Satu registry data-driven berisi N gaya riset; tiap gaya mendefinisikan
  outline, numbering, formatting, citation, dan guidance.
- Semua jalur export (WordCraft, DOCX, LaTeX) membaca `FormattingSpec` yang
  sama sehingga margin/spasi/penomoran konsisten lintas engine.
- Tool `start_research(project_id, style_id)` membuat skeleton bab draf +
  mengembalikan panduan menulis.
- Prompt generik tunggal `research_workflow(project_id, style_id)` menggantikan
  duplikasi prompt per gaya.
- Resource `research://styles` dan tool `list_research_styles` /
  `get_research_style` untuk penemuan.
- User bisa menambah/menimpa gaya lewat folder override pribadi (tanpa ubah kode).
- **Intake gate wajib**: server menolak membuat draf / export sebelum *Research
  Brief* lengkap; user harus memenuhi syarat & pilihan dulu (§6.5).

### Non-Goals
- Bukan editor dokumen; tidak mengubah isi draf yang sudah ada.
- Tidak mengonversi draf antar-gaya secara otomatis (di luar cakupan v1).
- Tidak mengklaim mendukung format persis setiap kampus; yang dikirim adalah
  default umum yang bisa diedit.

### Anti-Goals
- Gaya **tidak boleh** mengubah format secara diam-diam tanpa export melaporkan
  `style_id` dan format yang dipakai di response-nya.
- Tidak boleh ada duplikasi definisi format antara WordCraft, Pandoc, dan LaTeX.

---

## 3. Constraints

- 100% gratis & lokal; tanpa dependensi berbayar baru.
- CLI-safe: tidak ada output non-JSON-RPC ke stdout.
- Proteksi path traversal pada semua akses file (validasi
  `target.resolve().is_relative_to(project_dir.resolve())`).
- Backward compatible: tool lama tetap jalan; parameter baru opsional dengan
  default yang meniru perilaku sekarang (`style=None` → `article_imrad`).
- Pydantic v2 untuk validasi spec.

---

## 4. Approaches (ringkas)

| Pendekatan | Ringkas | Keputusan |
|---|---|---|
| A — Prompt per gaya | Tambah 6–8 prompt, format hardcoded di teks | **Ditolak** — duplikasi, format tidak sampai ke export, tidak menyelesaikan BUG-4/EXP-3 |
| **B — Registry gaya** | Satu YAML/style + tool/prompt generik; export baca `FormattingSpec` | **Dipilih** |
| C — B + ingest template | User beri `.docx`/`.pdf` → diekstrak ke entri style yang sama | **Fase 2** setelah B stabil |

Alasan B: menyelesaikan struktur DAN format dengan satu sumber kebenaran, dan
membuat C murah (C hanya menambah sumber input ke registry yang sama).

---

## 5. Skema Registry

### 5.1 Model Pydantic (`styles/models.py`)

```python
class SectionSpec(BaseModel):
    id: str                     # "pendahuluan", "metode"
    title: str                  # "Pendahuluan" / "Tinjauan Pustaka"
    level: int = 1              # 1 = bab, 2 = subbab
    filename: str               # "01_pendahuluan.md"
    order: int
    kind: Literal["front_matter", "body"] = "body"   # front matter = bab nyata
    guidance: str = ""          # bilingual: ID utama + ringkasan EN

class FormattingSpec(BaseModel):
    paper_size: Literal["A4", "Letter"] = "A4"
    margin_cm: dict[str, float] = {"top": 2.5, "right": 2.5, "bottom": 2.5, "left": 2.5}
    line_spacing: float = 1.5
    justify: bool = True
    font_family: str = "Times New Roman"
    font_size_pt: float = 12
    heading_numbering: Literal["none", "arabic", "roman_bab", "numeric_bab"] = "arabic"
    page_number_position: Literal["footer_center", "footer_right", "header_right", "none"] = "footer_center"
    page_number_format: Literal["decimal", "lowerRoman", "upperRoman"] = "decimal"
    two_column: bool = False

class StyleSpec(BaseModel):
    id: str
    label: str
    description: str = ""
    outline: list[SectionSpec]
    formatting: FormattingSpec = FormattingSpec()
    citation: str = "apa"               # default CSL
    export: dict = {}                   # {"docx_template": ..., "latex_template": ...}
    guidance: str = ""                  # panduan menulis tingkat-gaya
```

### 5.2 Lokasi & Loading (`styles/registry.py`)

- Bundled: `src/research_mcp/templates/styles/*.yaml` (via `importlib.resources`).
- Override pengguna: `~/.research-mcp/styles/*.yaml` (di-merge menimpa bundled
  berdasarkan `id`).
- `list_styles() -> list[style summary]`, `get_style(id) -> StyleSpec`,
  cache di memori, validasi saat load; gaya invalid → `ToolError` dengan nama file.
- Resolusi path override memakai validasi traversal.

### 5.3 Gaya bawaan v1 (`templates/styles/`)

| id | Label | Ciri kunci |
|---|---|---|
| `article_imrad` | Artikel Jurnal (IMRaD) | Abstract, Intro, Methods, Results, Discussion, Conclusion; A4, 2.5 cm, 1.5×, APA |
| `ieee_paper` | Paper Konferensi IEEE | IMRaD 2 kolom, 1.0×, IEEE CSL |
| `skripsi_s1` | Skripsi S1 (Indonesia) | BAB I–V, margin 4-3-3-3, 2×, TNR 12, front matter lengkap, `roman_bab` |
| `tesis_s2` | Tesis S2 (Indonesia) | Seperti S1 + pengesahan, pernyataan keaslian, daftar singkatan |
| `proposal_penelitian` | Proposal Penelitian/Hibah | Latar Belakang, Rumusan, Tujuan, Manfaat, Tinjauan, Metode, Jadwal, Anggaran |
| `case_study` | Laporan Studi Kasus | Naratif, ringkas, grafik dominan |
| `rapid_synthesis` | Sintesis Cepat (policy brief) | Ringkasan 3–8 halaman, eksekutif |
| `annotated_bibliography` | Bibliografi Beranotasi | Daftar paper + anotasi per entri |

Nomor bab di `skripsi_s1`/`tesis_s2`: heading bab `BAB I PENDAHULUAN` (centered,
bold), subbab `1.1 Latar Belakang`.

---

## 6. Perubahan Permukaan Tool & Prompt

### 6.1 Tool baru (`server.py`)

| Tool | Fungsi | Response inti |
|---|---|---|
| `list_research_styles()` | Daftar gaya | `[{id, label, description, sections}]` |
| `get_research_style(style_id)` | Detail spec + panduan | `StyleSpec` lengkap |
| `start_research(project_id, style_id, overwrite=False)` | Buat skeleton bab draf | `{style_id, created_files, outline, guidance}` |
| `ingest_style_template(file_path, style_id)` *(Fase 2)* | `.docx`/`.pdf` → style draft di folder user | `{style_id, saved_path, sections}` |

`start_research` menulis `drafts/<filename>` berisi heading judul + komentar
`<!-- guidance: ... -->`; tidak menimpa file non-kosong kecuali `overwrite=True`.

### 6.2 Tool yang diubah (param opsional `style`)

- `export_to_docx(..., style: str | None = None)`
- `export_to_latex(..., style: str | None = None)`
- `prepare_wordcraft_handoff(..., style: str | None = None)`

`style=None` → `article_imrad` (perilaku sekarang). Response **wajib** menyertakan
`style_id` dan ringkasan format yang dipakai.

### 6.3 Prompt baru

- `research_workflow(project_id, style_id)` — generik: baca style → `start_research`
  → pandu isi bab → `verify_citations` → export sesuai `style.citation`.
- Empat prompt lama dipertahankan sebagai pintasan; body-nya men-delegasi ke
  `research_workflow` dengan `style_id` tetap (mis. `draft_latex_paper` →
  `article_imrad`/`ieee_paper`). Tidak ada breaking change.

### 6.4 Resource baru

- `research://styles` — JSON daftar gaya (mirip `list_research_styles`).

---

### 6.5 Intake & Readiness Gate — wajib sebelum eksekusi

**Prinsip:** tidak ada pembuatan skeleton bab, penulisan draf, atau export yang
dijalankan sebelum *Research Brief* proyek lengkap. Server **menolak dan meminta**
(bukan menebak), mengembalikan daftar syarat yang belum dipenuhi beserta
pertanyaan yang harus dijawab user. Ini memastikan hasil sesuai pilihan user.

**Research Brief (field):**

| Field | Wajib | Default | Keterangan |
|---|---|---|---|
| `style_id` | ya | – | dipilih dari `list_research_styles` |
| `title` | ya | – | judul riset |
| `research_question` | ya | – | pertanyaan/tujuan riset |
| `citation` | ya (konfirmasi) | `style.citation` | gaya sitasi; user harus mengonfirmasi |
| `language` | ya | `"id"` | bahasa dokumen (`id`/`en`/`bilingual`) |
| `scope` | tidak | `""` | batasan/cakupan |
| `target_length` | tidak | `""` | perkiraan halaman/kata |
| `deadline` | tidak | `""` | tenggat |

Disimpan di `projects.style_id` + `projects.brief_json`.

**Tool:**

- `save_research_brief(project_id, **fields)` — validasi + simpan; return status.
- `get_research_brief(project_id)` — `{complete, missing, brief}`.
- `start_research(project_id, **overrides)`:
  - brief belum lengkap → `{status:"needs_input", missing:[...], ask_user:[...],
    defaults:{...}}` dan **tidak membuat file apa pun**.
  - brief lengkap → buat skeleton bab front matter + isi, return `{status:"created"}`.
- **Gate export** (`export_to_docx`, `export_to_latex`,
  `prepare_wordcraft_handoff`): wajib brief lengkap, `style` ter-resolve, minimal
  1 paper tersimpan, dan `verify_citations` lulus. Gagal →
  `{status:"precondition_failed", failed:[...], remediation:[...]}` tanpa menulis
  output.

**Enforcement di server**, bukan sekadar instruksi prompt — agent tidak bisa
melewati gate. Bentuk respons `needs_input`:

```json
{
  "status": "needs_input",
  "missing": ["style_id", "title", "research_question", "citation"],
  "ask_user": [
    "Gaya riset apa? Pilihan: article_imrad, skripsi_s1, tesis_s2, ...",
    "Judul riset?",
    "Pertanyaan/tujuan riset?",
    "Gaya sitasi? (default dari gaya: apa)"
  ],
  "defaults": {"citation": "apa", "language": "id"}
}
```

---

## 7. Desain Komponen (bagaimana format benar-benar diterapkan)

Modul juru kunci tunggal: **`exporters/formatting.py`** menerjemahkan satu
`FormattingSpec` menjadi tiga representasi. Ini mencegah format tersebar.

### 7.1 WordCraft (`wordcraft_bridge.py`)

- `_style_steps(style: StyleSpec)` — pakai `style.formatting` untuk `Normal`
  (`line_spacing`, `font_family`, `font_size_pt`) dan `Heading 1/2/3`
  (**perbaiki BUG-2**: `"Heading 1"` bukan `"Heading1"`).
- `_page_setup_steps(style)` *(baru)* — **command sudah diverifikasi** lewat
  `list_commands` WordCraft (lihat §7.4). Urutan: `layout.size` →
  `layout.margins` → `para.lineSpacing`/`para.justify` (gaya Normal) →
  `insert.pageNumber` → `layout.pageNumberFormat` (romawi untuk front matter).
  Margin di WordCraft memakai **poin**, jadi `margin_cm × 28.3465` dikonversi
  di `exporters/formatting.py`.
- **Front matter adalah bab nyata** (`SectionSpec.kind="front_matter"`), bukan
  daftar terpisah di `FormattingSpec`. `start_research` membuat file draf untuk
  tiap front matter (cover/pengesahan/abstrak/kata pengantar/daftar isi), lalu
  ikut ter-merge dan ter-export seperti bab isi. Hanya `daftar_isi` yang butuh
  `references.toc` (diikuti `caret.docEnd`), dan `cover` boleh memakai
  `insert.coverPage` (`{title, subtitle, author}`) bila kosong. Tidak ada
  dual-model front matter.

### 7.4 Command WordCraft terverifikasi (hasil `list_commands`)

| Kebutuhan | Command | Param kunci |
|---|---|---|
| Ukuran kertas | `layout.size` | `{"name": "A4"}` |
| Margin | `layout.margins` | `{"top","bottom","left","right"}` **dalam pt** |
| Spasi baris | `para.lineSpacing` | `{"value": 2.0}` (multiple) |
| Rata kanan-kiri | `para.justify` | `{}` |
| Paragraf baru | `text.newParagraph` | `{}` |
| Nomor halaman | `insert.pageNumber` | `{"position":"bottom","align":"center"}` |
| Format nomor | `layout.pageNumberFormat` | `{"format":"lowerRoman|decimal","start":n}` |
| Halaman sampul | `insert.coverPage` | `{"title","subtitle","author"}` |

Catatan: `layout.pageSetup` menggantikan seluruh section (header/footer
termasuk) — dipakai hanya bila butuh kontrol penuh, bukan default.
- `_block_steps()` — **perbaiki BUG-1**: `text.newParagraph` setelah heading,
  paragraf, tabel, list.
- `_strip_md_inline()` — **perbaiki BUG-3**: buang `---`, `\<`, `\>`, `\~`, `:::`,
  atribut `{...}` Pandoc sebelum `type_text`.

### 7.2 Pandoc DOCX

- `render_docx` tetap memakai `--reference-doc` dari template (`default_academic`
  atau template pilihan gaya).
- Setelah render, `docx_polisher.apply_formatting(docx_path, FormattingSpec)`
  memakai `python-docx` untuk menetapkan margin halaman, spasi baris, justify,
  font, ukuran kertas, dan posisi nomor halaman. Ini menutup celah "reference-doc
  tidak menyimpan margin dengan andal".

### 7.3 LaTeX

- `render_latex` menerima metadata tambahan dari `FormattingSpec`:
  `geometry` (margin), `linestretch` (spasi), `fontsize`, `documentclass` opsi.
  Diteruskan sebagai `--metadata` sehingga template `.tex` tidak perlu diubah.
- Fase 2: tambah template `thesis_id.tex` yang membaca variabel ini.

---

## 8. Alur End-to-End (contoh skripsi)

1. `create_project("Skripsi Saya")` → dapat `project_id`.
2. `list_research_styles()` → agent menampilkan pilihan, **bertanya ke user**.
3. `save_research_brief(project_id, style_id="skripsi_s1", title=..., research_question=..., citation="apa")`
   → brief lengkap.
4. `start_research(project_id)`:
   - brief belum lengkap → `needs_input` (server **berhenti**, menyuruh tanya user);
   - lengkap → dibuat `drafts/00_cover.md`, `01_pengesahan.md`, `02_abstrak.md`,
     … front matter, lalu `10_bab1_pendahuluan.md` … `15_bab5_kesimpulan.md` + panduan.
5. Agent isi tiap bab via `save_draft_section` dengan `[@citekey]`.
6. `verify_citations(project_id)` → gate.
7. `export_to_docx(project_id, "skripsi")` (style diwarisi proyek) →
   DOCX margin 4-3-3-3, spasi 2×, BAB I–V. *atau*
   `prepare_wordcraft_handoff(project_id)` untuk WordCraft. Keduanya menjalankan
   **precondition gate** §6.5 dan menolak bila syarat belum terpenuhi.

---

## 9. Backward Compatibility & Migrasi

- `doc_type` yang diusulkan audit **digantikan** oleh `style`; untuk kompatibilitas
  `doc_type="article"|"thesis"` dipetakan ke `article_imrad`|`skripsi_s1`.
- Tool/prompt lama tidak dihapus di rilis ini.
- Default `style=None` = `article_imrad` = perilaku sekarang.

---

## 10. Testing & Quality Gate

- Unit: load/validasi registry, merge override user, resolusi traversal,
  `start_research` skeleton (tidak menimpa tanpa `overwrite`), translasi
  `FormattingSpec` → WordCraft steps / docx / latex metadata.
- Integrasi: `skripsi_s1` → DOCX margin terverifikasi via `python-docx` reopen;
  WordCraft steps memuat page-setup; LaTeX memuat `geometry`.
- **Gate**: `start_research` tanpa brief → `needs_input` dan tidak membuat file;
  export tanpa brief/style/sitasi → `precondition_failed` dan tidak menulis
  output. Uji khusus untuk memastikan tidak ada bypass.
- `ruff check .`, `ruff format --check .`, `pytest` wajib lulus.

---

## 11. Risiko & Mitigasi

| Risiko | Mitigasi |
|---|---|
| Nama command WordCraft berbeda antar build | Sudah diverifikasi (§7.4 untuk build saat ini); tetap cek `list_commands` saat startup, fallback warning |
| `python-docx` merusak DOCX | Buka ulang + `file.strictReport` setelah apply |
| Nama file bab tidak stabil → urutan merge kacau | `filename` + `order` ditentukan registry; merge pakai `order` |
| Gaya invalid bikin server crash | Validasi saat load; gaya rusak dilewati dengan `ToolError` jelas |

---

## 12. Yang Ditentukan Builder (bukan pengguna)

- Nama modul (`styles/models.py`, `styles/registry.py`, `exporters/formatting.py`).
- Format cache registry dan strategi merge override.
- Detail metadata LaTeX.
- Penamaan field YAML.

---

## 13. Keputusan Builder (hasil builder check)

Keputusan-keputusan ini saya ambil sebagai builder; bisa dikoreksi saat review.

| # | Pertanyaan | Keputusan |
|---|---|---|
| 1 | Registry vs heading draf | **Registry otoritatif.** `exporters/formatting.py` menormalkan tiap bab: buang H1 pembuka bila cocok dengan judul outline, lalu emit heading dari registry (`BAB I PENDAHULUAN`, dst.). Draf tetap menyimpan teks isi. |
| 2 | Style diingat per proyek? | **Ya.** Tambah kolom `projects.style_id`; `start_research` mengisinya. `style=None` → resolve ke `projects.style_id`, fallback `article_imrad`. Satu aturan resolusi dipakai semua tool. |
| 4 | Romawi + restart di v1? | **Best-effort v1.** WordCraft: `layout.pageNumberFormat` (`lowerRoman`) + `layout.break nextPage`. DOCX/LaTeX: degrade + `warning` di response. Fidelity penuh = Fase 2. |
| 5 | Field wajib v1? | **Dijamin** di DOCX & WordCraft: A4, margin, spasi, font, justify. LaTeX: `geometry`+`linestretch`. `two_column` = WordCraft `layout.columns`; LaTeX degrade + warning. Matriks degradasi didokumentasikan. |
| 6 | Precedence citation/template | `csl_style`/`template_name` default jadi `None`. Urutan: param eksplisit > `style.citation`/`style.export` > default lama. Tanpa `style`, perilaku identik sekarang. |
| 7 | Template yang belum ada | v1 hanya memakai template eksisting (`default_academic`, `article_id`, `ieee`). Validasi registry menolak nama template tak dikenal saat load. `thesis_id.tex` + `.docx` Indonesia = Fase 2. |
| 8 | Siapa verifikasi command WordCraft | **Research MCP tidak spawn `wordcraft-cli`.** Blueprint membawa command sebagai data (terverifikasi manual, §7.4) + `warnings` agar agent cek `list_commands` bila build berbeda. Murni modul data, tanpa IPC. |
| 9 | File `.md` ekstra ikut export? | Tanpa `style`: perilaku sekarang (append semua). Dengan `style`: **hanya file outline**; file ekstra dilaporkan di `extra_files`/`warning`, tidak diekspor diam-diam. `start_research` menolak menimpa draf yang ada tanpa `overwrite=True`. |
| 10 | Prompt lama tanpa `project_id` | `systematic_literature_review(topic, year_from)` tetap signature-nya; body-nya menunjuk registry + menyuruh buat proyek dulu, bukan memaksa delegasi. Tiga prompt lain (punya `project_id`) mendelegasi ke `research_workflow`. |

---

## 14. Keputusan Pengguna (final)

| # | Pertanyaan | Keputusan |
|---|---|---|
| 1 | Front matter | **Buat file draf.** Front matter = bab nyata (`kind="front_matter"`), dibuat `start_research`, ikut merge & export. |
| 2 | Bahasa panduan | **Bilingual** — Indonesia utama + ringkasan Inggris singkat. Berlaku untuk `StyleSpec.guidance`, `SectionSpec.guidance`, dan response `start_research`. |
| 3 | Format skripsi | **Keduanya.** Default dikirim = standar umum DIKTI/SNPT; format kampus menimpa lewat ingest template `.docx` (Fase 2) atau override YAML pengguna. |
| 4 | Prioritas ingest Fase 2 | **`.docx` dulu** (Pandoc reference-doc + python-docx); `.pdf` menyusul. |

---

## 15. Status

**DISETUJUI (2026-11-22).** Keputusan builder (§13) dan keputusan pengguna (§14)
sudah tercatat. Langkah berikutnya: `writing-plans` → eksekusi per task dengan TDD.
