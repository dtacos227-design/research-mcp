# Research Styles & Formatting Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a data-driven research-style registry plus a mandatory intake gate so Research MCP produces skripsi/tesis/proposal/case-study documents with correct structure and academic formatting across WordCraft, Pandoc DOCX, and LaTeX.

**Architecture:** A bundled YAML registry (`templates/styles/*.yaml`) is loaded into Pydantic `StyleSpec` models and read by one translation module (`exporters/formatting.py`) that feeds three engines. A persisted `ResearchBrief` (in `projects.brief_json` + `projects.style_id`) gates every drafting/export tool: incomplete brief → `needs_input`, never a silent guess.

**Tech Stack:** Python 3.11+, FastMCP, Pydantic v2, aiosqlite/SQLite, PyYAML, python-docx, Pandoc, Tectonic, WordCraft MCP.

**Spec:** `docs/design/2026-11-22-research-styles-registry-design.md`

## Global Constraints

- 100% free/local; no new paid dependency. Add `pyyaml` only.
- CLI-safe: never write to stdout; logs go to stderr.
- Every filesystem path: validate `target.resolve().is_relative_to(project_dir.resolve())`.
- Backward compatible: `style=None` resolves to `projects.style_id` then `article_imrad`; without a style, export behavior is unchanged.
- Intake gate enforced **in server code**, not prompts.
- Mandatory brief fields: `style_id`, `title`, `research_question`, `citation`, `language`.
- WordCraft margins are in **points**; `CM_TO_PT = 28.3464567`.
- WordCraft style names are `"Heading 1"`, `"Heading 2"`, `"Heading 3"` (with space).
- `ruff check .`, `ruff format --check .`, `pytest` must pass before any task is marked done.

## Review Focus

- Exporting a project whose brief is incomplete must return `precondition_failed`, never write a file.
- A draft chapter with its own `# Heading` must not duplicate the registry heading in output.
- `skripsi_s1` export must show 4-3-3-3 cm margins (verified by reopening the DOCX), 2× spacing, and `BAB I` heading.
- `style=None` on a project started with `skripsi_s1` must inherit `skripsi_s1`, not fall back to `article_imrad`.
- Switching a project from `article_imrad` to `skripsi_s1` must not silently merge two chapter sets into one document.

---

### Task 1: Style Pydantic models

**Files:**
- Create: `src/research_mcp/styles/__init__.py`
- Create: `src/research_mcp/styles/models.py`
- Test: `tests/unit/test_style_models.py`

**Interfaces:**
- Produces: `SectionSpec`, `FormattingSpec`, `StyleSpec` (Pydantic v2) with fields exactly as in spec §5.1.

- [ ] **Step 1: Write the failing test**

```python
from research_mcp.styles.models import FormattingSpec, SectionSpec, StyleSpec


def test_formatting_defaults():
    fmt = FormattingSpec()
    assert fmt.paper_size == "A4"
    assert fmt.margin_cm == {"top": 2.5, "right": 2.5, "bottom": 2.5, "left": 2.5}
    assert fmt.line_spacing == 1.5
    assert fmt.page_number_format == "decimal"


def test_style_requires_outline_sections():
    spec = StyleSpec(
        id="x", label="X",
        outline=[SectionSpec(id="a", title="A", filename="01_a.md", order=1)],
    )
    assert spec.outline[0].kind == "body"
    assert spec.citation == "apa"


def test_section_kind_front_matter():
    s = SectionSpec(id="cover", title="Cover", filename="00_cover.md", order=0, kind="front_matter")
    assert s.kind == "front_matter"


def test_margin_rejects_negative():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        FormattingSpec(margin_cm={"top": -1, "right": 2.5, "bottom": 2.5, "left": 2.5})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_style_models.py -v`
Expected: FAIL with `ModuleNotFoundError: research_mcp.styles`

- [ ] **Step 3: Implement `styles/models.py`**

Models per spec §5.1. Add `field_validator` on `margin_cm` rejecting negatives and missing keys (`top/right/bottom/left`). `SectionSpec.order: int`. `StyleSpec.export: dict[str, str] = Field(default_factory=dict)`. Create empty `styles/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_style_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/styles tests/unit/test_style_models.py
git commit -m "feat(styles): add style Pydantic models"
```

---

### Task 2: Registry loader with user overrides

**Files:**
- Create: `src/research_mcp/styles/registry.py`
- Test: `tests/unit/test_style_registry.py`

**Interfaces:**
- Consumes: `StyleSpec`, `FormattingSpec`, `SectionSpec` (Task 1).
- Produces:
  - `list_styles() -> list[dict]` — `[{id,label,description,sections}]`
  - `get_style(style_id: str) -> StyleSpec` — raises `ToolError` if unknown
  - `reload_styles() -> None`
  - `USER_STYLES_DIR_ENV = "RESEARCH_MCP_STYLES_DIR"`

- [ ] **Step 1: Write the failing test**

```python
import textwrap
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from research_mcp.styles import registry


def _write_style(tmp_path, name, body):
    d = tmp_path / "styles"; d.mkdir(exist_ok=True)
    (d / name).write_text(textwrap.dedent(body), encoding="utf-8")
    return d


def test_load_bundled_style(tmp_path, monkeypatch):
    spec = registry.get_style("article_imrad")
    assert spec.id == "article_imrad"
    assert spec.outline, "outline must not be empty"


def test_unknown_style_raises():
    with pytest.raises(ToolError):
        registry.get_style("does_not_exist")


def test_user_override_wins(tmp_path, monkeypatch):
    d = _write_style(tmp_path, "article_imrad.yaml", """
        id: article_imrad
        label: Custom Article
        outline:
          - {id: a, title: A, filename: 01_a.md, order: 1}
    """)
    monkeypatch.setenv(registry.USER_STYLES_DIR_ENV, str(d))
    registry.reload_styles()
    assert registry.get_style("article_imrad").label == "Custom Article"


def test_unknown_export_template_rejected(tmp_path, monkeypatch):
    d = _write_style(tmp_path, "bad.yaml", """
        id: bad
        label: Bad
        export: {latex_template: nope}
        outline:
          - {id: a, title: A, filename: 01_a.md, order: 1}
    """)
    monkeypatch.setenv(registry.USER_STYLES_DIR_ENV, str(d))
    with pytest.raises(ToolError):
        registry.reload_styles()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_style_registry.py -v`
Expected: FAIL (`registry` has no styles / no module)

- [ ] **Step 3: Implement `styles/registry.py`**

Load `resources.files("research_mcp.templates") / "styles"` YAML via `yaml.safe_load`, merge user dir (env override, default `~/.research-mcp/styles`), validate each `StyleSpec`, reject `export` template names not in `templates.DOCX_TEMPLATES`/`LATEX_TEMPLATES` (import from `research_mcp.exporters.templates`), cache dict in module global, `reload_styles()` clears it. Wrap parse errors in `ToolError` naming the file.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_style_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/styles/registry.py tests/unit/test_style_registry.py
git commit -m "feat(styles): add YAML registry with user overrides"
```

---

### Task 3: Built-in style YAML files

**Files:**
- Create: `src/research_mcp/templates/styles/article_imrad.yaml`
- Create: `src/research_mcp/templates/styles/ieee_paper.yaml`
- Create: `src/research_mcp/templates/styles/skripsi_s1.yaml`
- Create: `src/research_mcp/templates/styles/tesis_s2.yaml`
- Create: `src/research_mcp/templates/styles/proposal_penelitian.yaml`
- Create: `src/research_mcp/templates/styles/case_study.yaml`
- Create: `src/research_mcp/templates/styles/rapid_synthesis.yaml`
- Create: `src/research_mcp/templates/styles/annotated_bibliography.yaml`
- Test: `tests/unit/test_builtin_styles.py`

**Interfaces:**
- Consumes: `registry.list_styles()`, `registry.get_style()` (Task 2).

- [ ] **Step 1: Write the failing test**

```python
import pytest
from research_mcp.styles import registry

EXPECTED = {
    "article_imrad", "ieee_paper", "skripsi_s1", "tesis_s2",
    "proposal_penelitian", "case_study", "rapid_synthesis", "annotated_bibliography",
}


def test_all_builtin_styles_present():
    ids = {s["id"] for s in registry.list_styles()}
    assert EXPECTED <= ids


@pytest.mark.parametrize("style_id", sorted(EXPECTED))
def test_style_outline_is_ordered_and_unique(style_id):
    spec = registry.get_style(style_id)
    assert spec.outline
    orders = [s.order for s in spec.outline]
    assert orders == sorted(orders)
    filenames = [s.filename for s in spec.outline]
    assert len(filenames) == len(set(filenames))


def test_skripsi_has_front_matter_and_five_bab():
    spec = registry.get_style("skripsi_s1")
    front = [s for s in spec.outline if s.kind == "front_matter"]
    body = [s for s in spec.outline if s.kind == "body"]
    assert front, "skripsi must have front matter"
    assert len(body) == 5
    assert spec.formatting.margin_cm == {"top": 3.0, "right": 3.0, "bottom": 3.0, "left": 4.0}
    assert spec.formatting.line_spacing == 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_builtin_styles.py -v`
Expected: FAIL (styles missing)

- [ ] **Step 3: Author the 8 YAML files**

Each file is a `StyleSpec` dict. `skripsi_s1`: front matter (`00_cover`, `01_pengesahan`, `02_abstrak`, `03_kata_pengantar`, `04_daftar_isi`) then `10_bab1_pendahuluan` … `14_bab5_kesimpulan`; formatting A4, margins `{top:3,right:3,bottom:3,left:4}`, `line_spacing: 2.0`, `heading_numbering: roman_bab`, `page_number_format: lowerRoman`. `tesis_s2` adds `05_pernyataan`, `06_daftar_singkatan`. `ieee_paper`: `two_column: true`, `line_spacing: 1.0`, citation `ieee`. `proposal_penelitian`: Latar Belakang, Rumusan Masalah, Tujuan, Manfaat, Tinjauan Pustaka, Metode, Jadwal, Anggaran. Guidance fields bilingual: Indonesian sentence followed by `EN:` summary.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_builtin_styles.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/templates/styles tests/unit/test_builtin_styles.py
git commit -m "feat(styles): add eight built-in research styles"
```

---

### Task 4: DB migration + brief/style repository accessors

**Files:**
- Modify: `src/research_mcp/db/database.py` (`_migrate_legacy_schema`)
- Modify: `src/research_mcp/db/repository.py`
- Test: `tests/unit/test_project_brief_repository.py`

**Interfaces:**
- Produces:
  - `projects.style_id TEXT`, `projects.brief_json TEXT`
  - `async def set_project_brief(conn, project_id: str, brief_json: str, style_id: str) -> None`
  - `async def get_project_brief(conn, project_id: str) -> dict | None`
  - `async def set_project_style(conn, project_id: str, style_id: str | None) -> None`

- [ ] **Step 1: Write the failing test**

```python
import pytest, pytest_asyncio
from research_mcp.db.database import get_connection
from research_mcp.db.repository import create_project, get_project_brief, set_project_brief, get_project


@pytest_asyncio.fixture
async def db(tmp_path):
    conn = await get_connection(tmp_path / "r.db")
    project = await create_project(conn, tmp_path / "ws", "Brief")
    yield conn, project
    await conn.close()


@pytest.mark.asyncio
async def test_brief_roundtrip(db):
    conn, project = db
    await set_project_brief(conn, project.id, '{"title": "T"}', "skripsi_s1")
    brief = await get_project_brief(conn, project.id)
    assert brief == {"title": "T"}
    refreshed = await get_project(conn, project.id)
    assert refreshed.style_id == "skripsi_s1"


@pytest.mark.asyncio
async def test_brief_missing_returns_none(db):
    conn, project = db
    assert await get_project_brief(conn, project.id) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_project_brief_repository.py -v`
Expected: FAIL (`set_project_brief` not defined)

- [ ] **Step 3: Implement**

Extend `_migrate_legacy_schema` to inspect `PRAGMA table_info(projects)` and `ALTER TABLE projects ADD COLUMN style_id TEXT` / `brief_json TEXT`. Add the three repository functions: `set_project_brief` writes both `brief_json` and `style_id`; `get_project_brief` parses JSON or returns `None`; extend the `ProjectModel` dataclass with `style_id: str | None = None` and make `get_project` read it.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_project_brief_repository.py tests/unit/test_repository.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/db tests/unit/test_project_brief_repository.py
git commit -m "feat(db): persist research brief and style per project"
```

---

### Task 5: ResearchBrief model + completeness

**Files:**
- Modify: `src/research_mcp/models.py`
- Test: `tests/unit/test_research_brief.py`

**Interfaces:**
- Produces:
  - `class ResearchBrief(BaseModel)` with `style_id, title, research_question, citation, language, scope, target_length, deadline`
  - `ResearchBrief.missing() -> list[str]`
  - `ResearchBrief.complete() -> bool`

- [ ] **Step 1: Write the failing test**

```python
from research_mcp.models import ResearchBrief


def test_missing_required_fields():
    b = ResearchBrief()
    assert b.missing() == ["style_id", "title", "research_question"]


def test_citation_defaults_to_none_but_counts_when_given_style():
    b = ResearchBrief(style_id="skripsi_s1", title="T", research_question="Q")
    assert "citation" in b.missing()
    b2 = b.model_copy(update={"citation": "apa"})
    assert b2.complete() is True


def test_language_default_is_indonesian():
    assert ResearchBrief().language == "id"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_research_brief.py -v`
Expected: FAIL (no `ResearchBrief`)

- [ ] **Step 3: Implement**

Add model. Required set = `style_id`, `title`, `research_question`, `citation`. `missing()` returns that order filtered by falsy. `complete()` = `not missing()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_research_brief.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/models.py tests/unit/test_research_brief.py
git commit -m "feat: add ResearchBrief model and completeness check"
```

---

### Task 6: Intake gate + `start_research`

**Files:**
- Modify: `src/research_mcp/server.py`
- Test: `tests/unit/test_intake_gate.py`

**Interfaces:**
- Consumes: `ResearchBrief` (Task 5), `set_project_brief`/`get_project_brief` (Task 4), `get_style` (Task 2).
- Produces:
  - `async def save_research_brief(project_id, style_id, title, research_question, citation=None, language="id", scope="", target_length="", deadline="") -> dict`
  - `async def get_research_brief(project_id) -> dict`
  - `async def start_research(project_id) -> dict` — `{status:"needs_input",...}` or `{status:"created", created_files, guidance}`
  - Helper `async def _resolve_style_for_export(conn, project_id, style) -> StyleSpec`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from research_mcp import server


@pytest.mark.asyncio
async def test_start_research_without_brief_needs_input(tmp_path, monkeypatch):
    pid = await _make_project(tmp_path, monkeypatch)
    result = await server.start_research(pid)
    assert result["status"] == "needs_input"
    assert "style_id" in result["missing"]


@pytest.mark.asyncio
async def test_start_research_incomplete_brief_creates_nothing(tmp_path, monkeypatch):
    pid = await _make_project(tmp_path, monkeypatch)
    await server.save_research_brief(pid, "skripsi_s1", "Judul", "Rumusan?")
    result = await server.start_research(pid)
    assert result["status"] == "needs_input"
    drafts = _project_dir(pid) / "drafts"
    assert not drafts.exists() or not list(drafts.glob("*.md"))
```

(`_make_project` creates a project via the existing tool and returns its id; `_project_dir` resolves the workspace. Copy the pattern from `tests/integration/test_project_tools.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_intake_gate.py -v`
Expected: FAIL (tools not defined)

- [ ] **Step 3: Implement**

`save_research_brief` resolves the style, fills `citation` from `style.citation` when `None`, persists `brief_json` + `style_id`. `start_research` loads brief; if `not complete()` returns `needs_input` with `missing`, `ask_user` (bilingual prompt per field), `defaults`. Do not create files. Full skeleton creation is Task 7.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_intake_gate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/server.py tests/unit/test_intake_gate.py
git commit -m "feat: add intake gate and brief tools"
```

---

### Task 7: `start_research` skeleton creation

**Files:**
- Modify: `src/research_mcp/server.py`
- Test: `tests/unit/test_start_research_skeleton.py`

**Interfaces:**
- Consumes: `get_style`, `upsert_draft_section`.
- Produces: on complete brief, `start_research` writes `drafts/<filename>` for every outline section and upserts a `draft_sections` row; refuses to overwrite existing non-empty files unless `overwrite=True`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_skeleton_creates_all_outline_files(tmp_path, monkeypatch):
    pid = await _make_project(tmp_path, monkeypatch)
    await server.save_research_brief(pid, "skripsi_s1", "Judul", "Rumusan?", citation="apa")
    result = await server.start_research(pid)
    assert result["status"] == "created"
    drafts = _project_dir(pid) / "drafts"
    files = {p.name for p in drafts.glob("*.md")}
    assert "00_cover.md" in files and "10_bab1_pendahuluan.md" in files
    # guidance appears
    text = (drafts / "00_cover.md").read_text()
    assert "<!-- guidance:" in text


@pytest.mark.asyncio
async def test_skeleton_refuses_overwrite(tmp_path, monkeypatch):
    pid = await _make_project(tmp_path, monkeypatch)
    await server.save_research_brief(pid, "skripsi_s1", "Judul", "Rumusan?", citation="apa")
    await server.start_research(pid)
    again = await server.start_research(pid)
    assert again["status"] == "exists"
    assert again["conflicts"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_start_research_skeleton.py -v`
Expected: FAIL (no files created)

- [ ] **Step 3: Implement**

For each `SectionSpec` write `# {title}\n\n<!-- guidance: {bilingual guidance} -->\n`. Use `_safe_destination` for path validation. Upsert draft rows with `order` and title. If any target exists non-empty and `overwrite=False`, return `{"status":"exists","conflicts":[...]}` without writing.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_start_research_skeleton.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/server.py tests/unit/test_start_research_skeleton.py
git commit -m "feat: create draft skeleton from style outline"
```

---

### Task 8: `formatting.py` — WordCraft translations

**Files:**
- Create: `src/research_mcp/exporters/formatting.py`
- Test: `tests/unit/test_formatting_wordcraft.py`

**Interfaces:**
- Consumes: `FormattingSpec`.
- Produces:
  - `CM_TO_PT = 28.3464567`
  - `wordcraft_style_steps(fmt) -> list[dict]`
  - `wordcraft_page_steps(fmt) -> list[dict]`
  - `wordcraft_column_steps(fmt) -> list[dict]`

- [ ] **Step 1: Write the failing test**

```python
from research_mcp.styles.models import FormattingSpec
from research_mcp.exporters import formatting


def test_style_steps_use_spaced_heading_names():
    steps = formatting.wordcraft_style_steps(FormattingSpec(line_spacing=2.0))
    names = [s["arguments"]["params"].get("style") for s in steps if s["arguments"]["command"] == "styles.modify"]
    assert "Heading 1" in names
    assert "Heading1" not in names


def test_page_steps_convert_cm_to_pt():
    fmt = FormattingSpec(margin_cm={"top": 3, "right": 3, "bottom": 3, "left": 4})
    steps = formatting.wordcraft_page_steps(fmt)
    margins = next(s for s in steps if s["arguments"]["command"] == "layout.margins")
    assert round(margins["arguments"]["params"]["left"], 1) == 113.4
    size = next(s for s in steps if s["arguments"]["command"] == "layout.size")
    assert size["arguments"]["params"] == {"name": "A4"}


def test_columns_only_when_two_column():
    assert formatting.wordcraft_column_steps(FormattingSpec(two_column=True))
    assert formatting.wordcraft_column_steps(FormattingSpec(two_column=False)) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_formatting_wordcraft.py -v`
Expected: FAIL (no module)

- [ ] **Step 3: Implement**

`_execute(command, params)` helper. `wordcraft_style_steps` emits Normal + `"Heading 1/2/3"` with `line_spacing`→`para.lineSpacing {"value": fmt.line_spacing}`. `wordcraft_page_steps` emits `layout.size {"name":"A4"}`, `layout.margins` with pt values, `insert.pageNumber` from `page_number_position`, `layout.pageNumberFormat` from `page_number_format`. `wordcraft_column_steps` emits `layout.columns {"count":2}` only when `two_column`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_formatting_wordcraft.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/exporters/formatting.py tests/unit/test_formatting_wordcraft.py
git commit -m "feat(export): translate FormattingSpec to WordCraft steps"
```

---

### Task 9: `formatting.py` — DOCX, LaTeX, heading normalization

**Files:**
- Modify: `src/research_mcp/exporters/formatting.py`
- Modify: `src/research_mcp/exporters/docx_polisher.py`
- Test: `tests/unit/test_formatting_docx_latex.py`

**Interfaces:**
- Produces:
  - `apply_docx_formatting(path: Path, fmt: FormattingSpec) -> None`
  - `latex_metadata(fmt: FormattingSpec) -> dict[str, str]`
  - `normalize_chapter_markdown(markdown: str, section: SectionSpec) -> str`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from docx import Document
from research_mcp.styles.models import FormattingSpec, SectionSpec
from research_mcp.exporters import formatting


def test_apply_docx_sets_a4_and_margins(tmp_path):
    import docx
    path = tmp_path / "d.docx"
    doc = Document(); doc.add_paragraph("x"); doc.save(str(path))
    ft = FormattingSpec(margin_cm={"top": 3, "right": 3, "bottom": 3, "left": 4})
    formatting.apply_docx_formatting(path, ft)
    d = Document(str(path))
    sec = d.sections[0]
    assert round(sec.left_margin.cm, 1) == 4.0
    assert round(sec.top_margin.cm, 1) == 3.0


def test_latex_metadata_has_geometry_and_linestretch():
    ft = FormattingSpec(margin_cm={"top": 3, "right": 3, "bottom": 3, "left": 4}, line_spacing=2.0)
    meta = formatting.latex_metadata(ft)
    assert "4cm" in meta["geometry"]
    assert meta["linestretch"] == "2.0"


def test_normalize_strips_matching_h1():
    sec = SectionSpec(id="b1", title="Pendahuluan", filename="10.md", order=1)
    out = formatting.normalize_chapter_markdown("# Pendahuluan\n\nIsi.", sec)
    assert out.startswith("## ")
    assert "Isi." in out


def test_normalize_keeps_unmatched_heading():
    sec = SectionSpec(id="b1", title="Metode", filename="10.md", order=1)
    out = formatting.normalize_chapter_markdown("# Lain\n\nIsi.", sec)
    assert "# Lain" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_formatting_docx_latex.py -v`
Expected: FAIL (functions missing)

- [ ] **Step 3: Implement**

`apply_docx_formatting`: `python-docx`, set `section.page_width/height` (A4 = 210×297 mm), margins in cm, `style.font.name/size` for Normal, `paragraph_format.line_spacing` and `alignment = WD_ALIGN_PARAGRAPH.JUSTIFY`. `latex_metadata`: `{"geometry": "top=3cm,right=3cm,bottom=3cm,left=4cm", "linestretch": str(fmt.line_spacing), "fontsize": f"{int(fmt.font_size_pt)}pt"}`. `normalize_chapter_markdown`: if the first non-empty line is `# <title>` case-insensitively matching `section.title`, replace with `## <title>`; else unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_formatting_docx_latex.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/exporters/formatting.py src/research_mcp/exporters/docx_polisher.py tests/unit/test_formatting_docx_latex.py
git commit -m "feat(export): docx formatting, latex metadata, heading normalization"
```

---

### Task 10: Fix WordCraft bridge bugs

**Files:**
- Modify: `src/research_mcp/exporters/wordcraft_bridge.py`
- Test: `tests/unit/test_wordcraft_fixes.py`

**Interfaces:**
- Produces:
  - `_NEW_PARA` emitted after every block in `_block_steps`
  - `_strip_md_inline(text: str) -> str`
  - `_style_steps(fmt: FormattingSpec | None = None)` accepts formatting

- [ ] **Step 1: Write the failing test**

```python
from research_mcp.exporters import wordcraft_bridge as wb


def test_new_paragraph_after_heading():
    steps = wb._block_steps({"kind": "heading", "level": 1, "text": "BAB I"})
    assert steps[-1]["arguments"]["command"] == "text.newParagraph"


def test_new_paragraph_after_paragraph():
    steps = wb._block_steps({"kind": "paragraph", "text": "Isi"})
    assert steps[-1]["arguments"]["command"] == "text.newParagraph"


def test_strip_md_inline():
    raw = "---\n\\<div\\>\nText \\~ here {#refs .csl-bib-body} :::"
    cleaned = wb._strip_md_inline(raw)
    assert "---" not in cleaned
    assert "\\<" not in cleaned
    assert "{#refs" not in cleaned
    assert "Text" in cleaned


def test_style_steps_use_spaced_heading():
    names = [s["arguments"]["params"].get("style") for s in wb._style_steps()]
    assert "Heading 1" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_wordcraft_fixes.py -v`
Expected: FAIL (no newParagraph, `Heading1`, no `_strip_md_inline`)

- [ ] **Step 3: Implement**

Add `_NEW_PARA = _execute("text.newParagraph")`. Append to heading/paragraph/table/list returns. Rename styles to `"Heading 1/2/3"`. Add `_strip_md_inline` (drop `---` lines, `\<`/`\>`→`<>`, `\~`→`~`, remove `{...}` Pandoc attribute blocks and `:::` fences) and apply it to paragraph/heading/list text before `type_text`. Make `_style_steps` accept optional `fmt` and use its `line_spacing`/font.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_wordcraft_fixes.py tests/unit/test_wordcraft_blueprint.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/exporters/wordcraft_bridge.py tests/unit/test_wordcraft_fixes.py
git commit -m "fix(wordcraft): paragraph breaks, style names, markdown stripping"
```

---

### Task 11: Wire style into WordCraft blueprint

**Files:**
- Modify: `src/research_mcp/exporters/wordcraft_bridge.py` (`build_blueprint`, `build_polish_sequence`)
- Test: `tests/unit/test_wordcraft_style_blueprint.py`

**Interfaces:**
- Consumes: `wordcraft_style_steps`, `wordcraft_page_steps`, `wordcraft_column_steps` (Task 8).
- Produces: `build_blueprint(..., style: StyleSpec | None = None, ...)`; page-setup steps precede content; `warnings` includes a note when a style field degrades.

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from research_mcp.exporters import wordcraft_bridge as wb
from research_mcp.styles import registry


def test_blueprint_includes_page_setup(tmp_path):
    (tmp_path / "exports").mkdir()
    md = tmp_path / "d.md"; md.write_text("# BAB I\n\nIsi.")
    style = registry.get_style("skripsi_s1")
    bp = wb.build_blueprint("P", md, md.read_text(), [], "apa", tmp_path / "exports", "out", style=style)
    commands = [s["arguments"].get("command") for s in bp["tool_sequence"] if s["tool"] == "execute"]
    assert "layout.size" in commands
    assert "layout.margins" in commands
    assert "text.newParagraph" in commands
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_wordcraft_style_blueprint.py -v`
Expected: FAIL (unexpected kwarg `style`)

- [ ] **Step 3: Implement**

Add `style` kwarg. When present: `*wordcraft_style_steps(style.formatting)`, `*wordcraft_page_steps(style.formatting)`, `*wordcraft_column_steps(style.formatting)`. Keep default path unchanged when `style is None`. Append degradation warnings when `two_column` is set (WordCraft supports it) but note LaTeX limitation only in export tools.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_wordcraft_style_blueprint.py tests/unit/test_wordcraft_blueprint.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/exporters/wordcraft_bridge.py tests/unit/test_wordcraft_style_blueprint.py
git commit -m "feat(wordcraft): apply style page setup in blueprint"
```

---

### Task 12: Style-aware DOCX export + precondition gate

**Files:**
- Modify: `src/research_mcp/server.py` (`export_to_docx`)
- Modify: `src/research_mcp/exporters/pandoc_engine.py` (`render_docx` accepts extra metadata)
- Test: `tests/unit/test_export_style_gate.py`

**Interfaces:**
- Consumes: `_resolve_style_for_export`, `apply_docx_formatting`, `normalize_chapter_markdown`, `latex_metadata`.
- Produces: `export_to_docx(project_id, output_name, csl_style: str | None = None, template_name="default_academic", polish=True, strict_citations=True, style: str | None = None)`; returns `{..., style_id}`; raises/returns `precondition_failed` when gate fails.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_export_without_brief_is_blocked(tmp_path, monkeypatch):
    pid = await _make_project(tmp_path, monkeypatch)
    result = await server.export_to_docx(pid, "paper")
    assert result.get("status") == "precondition_failed"
    assert "brief" in str(result["failed"]).lower()


@pytest.mark.asyncio
async def test_export_reports_style_id(tmp_path, monkeypatch):
    pid = await _seed_ready_project(tmp_path, monkeypatch, style="skripsi_s1")
    result = await server.export_to_docx(pid, "skripsi")
    assert result["style_id"] == "skripsi_s1"
```

(`_seed_ready_project` creates project, full brief, one paper row, and a draft; reuse existing ingestion helpers.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_export_style_gate.py -v`
Expected: FAIL (no gate / no style_id)

- [ ] **Step 3: Implement**

Add `_precondition_failed(conn, project_id, style)` helper: checks brief complete, style resolvable, `papers` count ≥ 1, and citations verified. In `export_to_docx`, resolve style (param → project → `article_imrad`), run gate, `csl_style or style.citation`; apply `normalize_chapter_markdown` per section during merge; after `render_docx`, call `apply_docx_formatting`. Add `style_id` to response.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_export_style_gate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/server.py src/research_mcp/exporters/pandoc_engine.py tests/unit/test_export_style_gate.py
git commit -m "feat(export): style-aware docx export with precondition gate"
```

---

### Task 13: Style-aware LaTeX export

**Files:**
- Modify: `src/research_mcp/server.py` (`export_to_latex`)
- Modify: `src/research_mcp/exporters/latex_engine.py` (`render_latex` accepts `metadata`)
- Test: `tests/unit/test_export_latex_style.py`

**Interfaces:**
- Produces: `export_to_latex(..., style: str | None = None)`; passes `latex_metadata` as `--metadata`; `two_column` emits a warning when the template can't honor it.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_latex_export_warns_on_two_column(tmp_path, monkeypatch):
    pid = await _seed_ready_project(tmp_path, monkeypatch, style="ieee_paper")
    result = await server.export_to_latex(pid, "paper", compile_pdf=False)
    assert result["style_id"] == "ieee_paper"
    assert any("two" in w.lower() or "column" in w.lower() for w in result.get("warnings", []))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_export_latex_style.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Add `metadata: dict[str,str] | None = None` to `render_latex`, forwarding each key as `--metadata key=value`. In `export_to_latex`, resolve style, gate, pass `latex_metadata(style.formatting)`, append warning for `two_column` (template hardcodes one column). Add `style_id`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_export_latex_style.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/server.py src/research_mcp/exporters/latex_engine.py tests/unit/test_export_latex_style.py
git commit -m "feat(export): style-aware latex export"
```

---

### Task 14: Style-aware WordCraft handoff + new tools/resource

**Files:**
- Modify: `src/research_mcp/server.py` (`prepare_wordcraft_handoff`, add `list_research_styles`, `get_research_style`, resource `research://styles`)
- Test: `tests/unit/test_style_tools.py`

**Interfaces:**
- Produces:
  - `prepare_wordcraft_handoff(..., style: str | None = None)` with gate and `style_id`
  - `list_research_styles()`, `get_research_style(style_id)`
  - resource `research://styles`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_list_and_get_style_tools():
    listing = await server.list_research_styles()
    assert {s["id"] for s in listing["styles"]} >= {"skripsi_s1", "article_imrad"}
    detail = await server.get_research_style("skripsi_s1")
    assert detail["style"]["formatting"]["line_spacing"] == 2.0


@pytest.mark.asyncio
async def test_handoff_blocked_without_brief(tmp_path, monkeypatch):
    pid = await _make_project(tmp_path, monkeypatch)
    result = await server.prepare_wordcraft_handoff(pid, mode="live_authoring")
    assert result.get("status") == "precondition_failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_style_tools.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Add the two tools and the resource (serialize `list_styles()`). In `prepare_wordcraft_handoff`, resolve style, run the same gate, pass `style=spec` into `build_blueprint`/`build_polish_sequence`, add `style_id` to output.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_style_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/server.py tests/unit/test_style_tools.py
git commit -m "feat: add style discovery tools and style-aware handoff"
```

---

### Task 15: `research_workflow` prompt + legacy delegation

**Files:**
- Modify: `src/research_mcp/server.py`
- Test: `tests/unit/test_research_workflow_prompt.py`

**Interfaces:**
- Produces: `@mcp.prompt() research_workflow(project_id, style_id="article_imrad")`; `draft_latex_paper` and `write_in_wordcraft` bodies reference `research_workflow`; `systematic_literature_review` keeps its signature but instructs `list_research_styles` + `save_research_brief` first.

- [ ] **Step 1: Write the failing test**

```python
from research_mcp import server


def test_research_workflow_mentions_brief_and_start():
    text = server.research_workflow_text("p1", "skripsi_s1")
    assert "save_research_brief" in text
    assert "start_research" in text
    assert "verify_citations" in text


def test_legacy_prompt_keeps_signature():
    assert "list_research_styles" in server._systematic_literature_review_text("topic", 2000)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_research_workflow_prompt.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Add `_research_workflow_text(project_id, style_id)` and expose `research_workflow`. Update legacy prompt bodies. Keep signatures.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_research_workflow_prompt.py tests/unit/test_wordcraft_prompts.py tests/unit/test_remaining_prompts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_mcp/server.py tests/unit/test_research_workflow_prompt.py
git commit -m "feat: add generic research_workflow prompt and legacy delegation"
```

---

### Task 16: End-to-end skripsi integration test

**Files:**
- Test: `tests/integration/test_style_export_e2e.py`

**Interfaces:**
- Consumes: all tools above.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_skripsi_end_to_end(tmp_path, monkeypatch):
    pid = await _make_project(tmp_path, monkeypatch)
    await server.save_research_brief(pid, "skripsi_s1", "Deteksi Parkir", "YOLO vs RT-DETR?", citation="apa")
    created = await server.start_research(pid)
    assert created["status"] == "created"
    # one paper saved via existing ingestion helper
    await _save_one_paper(pid)
    await server.save_draft_section(pid, "10_bab1_pendahuluan.md", "# Pendahuluan\n\nIsi [@doe2024x].", 1)
    result = await server.export_to_docx(pid, "skripsi")
    assert result["style_id"] == "skripsi_s1"
    from docx import Document
    doc = Document(result["path"])
    sec = doc.sections[0]
    assert round(sec.left_margin.cm, 1) == 4.0
    assert round(sec.top_margin.cm, 1) == 3.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_style_export_e2e.py -v`
Expected: FAIL until Tasks 1–15 land.

- [ ] **Step 3: Fix integration gaps**

Adjust merge to use `order`, ensure front-matter files precede body, and confirm the gate passes only with a saved paper + verified citations.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_style_export_e2e.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_style_export_e2e.py
git commit -m "test: end-to-end skripsi export"
```

---

### Task 17: Final quality gate

**Files:**
- Modify: `README.md` (document styles + intake gate)
- Test: full suite

- [ ] **Step 1: Update README**

Add a "Research styles" section listing `list_research_styles`, the brief fields, and the gate behavior; note `.docx` template ingestion is Phase 2.

- [ ] **Step 2: Run lint**

Run: `ruff check . && ruff format --check .`
Expected: no errors

- [ ] **Step 3: Run full suite**

Run: `pytest`
Expected: all pass (existing 311 + new)

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document research styles and intake gate"
```

---

## Self-Review Notes

- Spec §5–§8 → Tasks 1–3, 6–14. Intake gate §6.5 → Tasks 5–6, 12–14, 16.
- WordCraft bugs BUG-1/2/3 → Task 10. Page setup BUG-4 → Tasks 8, 11.
- Backward compat (`style=None`) → Tasks 12–14 tests.
- Phase 2 (`.docx` ingest) intentionally excluded; add a follow-up plan.
- Type consistency: `StyleSpec`, `FormattingSpec`, `SectionSpec`, `_resolve_style_for_export`, `precondition_failed` used consistently.
