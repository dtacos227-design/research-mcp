"""Fase 4 verification: a real Pandoc + Tectonic export of a complete project.

Skipped when either binary is missing (for example inside the dev container);
run it on a host that has pandoc 3+ and tectonic installed.
"""

import shutil
import zipfile

import pytest
import pytest_asyncio

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import create_project, save_paper_and_sections, set_project_brief
from research_mcp.models import PaperModel, ResearchBrief
from research_mcp.server import (
    export_to_docx,
    export_to_latex,
    export_to_markdown_bundle,
    generate_chart,
    save_draft_section,
)

pytestmark = pytest.mark.skipif(
    shutil.which("pandoc") is None or shutil.which("tectonic") is None,
    reason="pandoc and tectonic are required for the real export test",
)

INTRO = """# Introduction

Deep learning has advanced rapidly [@doe2024valid; @roe2024plain].

| Model | Accuracy |
|:------|---------:|
| A     | 0.910    |
| B     | 0.930    |

The measured trend is shown below.

![Accuracy curve](assets/fig1.png)
"""

METHODS = """# Methods

We follow the protocol of prior work [@poe2024third] and reuse the
baseline hyperparameters.
"""

RESULTS = """# Results

The best configuration reaches 0.93 accuracy [@doe2024valid].
"""


def _paper(project_id: str, citekey: str, title: str, family: str) -> PaperModel:
    return PaperModel(
        id=f"p-{citekey}",
        project_id=project_id,
        citekey=citekey,
        doi=f"10.1234/{citekey}",
        title=title,
        authors=[family],
        authors_structured=[{"given": "Alex", "family": family, "literal": None}],
        year=2024,
        venue="Journal of Reproducible Science",
        item_type="journal-article",
        created_at="2024-01-01T00:00:00Z",
    )


@pytest_asyncio.fixture
async def exportable_project(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspaces"
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(workspace_root))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    project = await create_project(conn, settings.workspace_dir, "E2E Export")
    for paper in (
        _paper(project.id, "doe2024valid", "Valid Paper", "Doe"),
        _paper(project.id, "roe2024plain", "Plain Paper", "Roe"),
        _paper(project.id, "poe2024third", "Third Paper", "Poe"),
    ):
        await save_paper_and_sections(conn, project.id, paper, sections=None)
    brief = ResearchBrief(
        style_id="article_imrad",
        title="E2E Export",
        research_question="Does a real Pandoc export embed charts and resolve citations?",
        citation="apa",
    )
    await set_project_brief(conn, project.id, brief.model_dump_json(), "article_imrad")
    await conn.close()

    chart = await generate_chart(
        project.id,
        "fig1.png",
        "plt.plot([1, 2, 3], [0.88, 0.91, 0.93])\nplt.xlabel('epoch')\nplt.ylabel('accuracy')",
        dpi=300,
    )
    assert chart["assets"][0]["path"].endswith("assets/fig1.png")

    await save_draft_section(project.id, "01_intro.md", INTRO)
    await save_draft_section(project.id, "02_methods.md", METHODS)
    await save_draft_section(project.id, "03_results.md", RESULTS)
    return project, workspace_root


@pytest.mark.asyncio
async def test_export_docx_embeds_chart_and_citations(exportable_project):
    project, workspace_root = exportable_project

    result = await export_to_docx(project.id, "paper", csl_style="apa")

    docx_path = workspace_root / project.slug / "exports" / "paper.docx"
    assert result["output_path"] == str(docx_path)
    assert docx_path.stat().st_size > 5000
    with zipfile.ZipFile(docx_path) as archive:
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
        document_xml = archive.read("word/document.xml").decode()
    assert media, "chart image was not embedded in the .docx"
    assert "Accuracy curve" in document_xml
    assert "[@" not in document_xml


@pytest.mark.asyncio
async def test_export_latex_compiles_pdf_with_figure(exportable_project):
    project, workspace_root = exportable_project

    result = await export_to_latex(project.id, "paper", template_name="ieee", compile_pdf=True)

    tex_path = workspace_root / project.slug / "exports" / "paper.tex"
    pdf_path = workspace_root / project.slug / "exports" / "paper.pdf"
    assert result["tex_path"] == str(tex_path)
    assert result["pdf_path"] == str(pdf_path)
    assert result["output_path"] == str(pdf_path)
    tex = tex_path.read_text()
    assert "IEEEtran" in tex
    assert "\\graphicspath" in tex
    assert "\\includegraphics" in tex
    assert pdf_path.read_bytes()[:4] == b"%PDF"
    assert pdf_path.stat().st_size > 10_000


@pytest.mark.asyncio
async def test_export_markdown_bundle_renders_reference_list(exportable_project):
    project, workspace_root = exportable_project

    result = await export_to_markdown_bundle(project.id, "paper", csl_style="apa")

    bundle_path = workspace_root / project.slug / "exports" / "paper.md"
    assert result["output_path"] == str(bundle_path)
    text = bundle_path.read_text()
    assert "Introduction" in text
    assert "Methods" in text
    assert "Results" in text
    assert "[@" not in text
    assert "References" in text
    assert "valid paper" in text.lower()  # APA style applies sentence case
