"""Task 16: full skripsi pipeline through the real Pandoc DOCX export.

Brief -> `start_research` skeleton -> one cited chapter -> `export_to_docx`
must produce an A4 .docx with the style's margins, without leaking the
skeleton's `<!-- guidance: ... -->` comments into the document text.

Nothing about the rendering pipeline is monkeypatched: this is the real
Pandoc + python-docx formatting path. Skipped when pandoc is missing.
"""

import shutil

import pytest

from research_mcp import server
from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import save_paper_and_sections
from research_mcp.models import PaperModel

pytestmark = pytest.mark.skipif(
    shutil.which("pandoc") is None,
    reason="pandoc is required for the real style export test",
)


def _paper(project_id: str) -> PaperModel:
    return PaperModel(
        id="p-doe2024x",
        project_id=project_id,
        citekey="doe2024x",
        doi="10.1234/doe2024x",
        title="Deteksi Parkir Kendaraan dengan YOLO",
        authors=["Doe"],
        authors_structured=[{"given": "Jane", "family": "Doe", "literal": None}],
        year=2024,
        venue="Journal of Parking Vision",
        item_type="journal-article",
        created_at="2024-01-01T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_skripsi_style_export_end_to_end(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspaces"
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(workspace_root))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    settings = get_settings()

    project = await server.create_project("Skripsi Deteksi Parkir")
    pid = project["id"]

    conn = await get_connection(settings.db_path)
    try:
        await save_paper_and_sections(conn, pid, _paper(pid), sections=None)
    finally:
        await conn.close()

    brief = await server.save_research_brief(
        pid, "skripsi_s1", "Deteksi Parkir", "YOLO vs RT-DETR?", citation="apa"
    )
    assert brief["status"] == "complete"

    created = await server.start_research(pid)
    assert created["status"] == "created"
    assert "10_bab1_pendahuluan.md" in created["created_files"]

    await server.save_draft_section(
        pid, "10_bab1_pendahuluan.md", "# Pendahuluan\n\nIsi [@doe2024x].", 1
    )

    # The untouched outline skeletons really do carry guidance comments, so the
    # "no guidance in the .docx" assertion below proves they were dropped.
    drafts_dir = workspace_root / project["slug"] / "drafts"
    assert "<!-- guidance:" in (drafts_dir / "00_cover.md").read_text()

    result = await server.export_to_docx(pid, "skripsi")

    assert result["style_id"] == "skripsi_s1"
    assert result["cited_but_missing"] == []
    # The DOCX engine cannot renumber BAB headings automatically; that caveat is
    # the only warning this otherwise clean export should carry.
    assert len(result["warnings"]) == 1
    assert "numbering" in result["warnings"][0].lower()

    docx_path = workspace_root / project["slug"] / "exports" / "skripsi.docx"
    assert result["output_path"] == str(docx_path)
    assert docx_path.is_file()

    from docx import Document

    doc = Document(str(docx_path))
    section = doc.sections[0]
    assert round(section.left_margin.cm, 1) == 4.0
    assert round(section.top_margin.cm, 1) == 3.0
    assert round(section.right_margin.cm, 1) == 3.0
    assert round(section.bottom_margin.cm, 1) == 3.0
    assert round(section.page_width.cm, 1) == 21.0
    assert round(section.page_height.cm, 1) == 29.7

    body_text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    assert "guidance:" not in body_text
    assert "<!--" not in body_text
    # The cited chapter survived the skeleton merge and Citeproc resolved it.
    assert "Pendahuluan" in body_text
    assert "[@" not in body_text
    assert "Doe" in body_text

    # Front matter precedes the body, and both follow the skripsi_s1 outline order
    # (registry titles, as the skeleton headings written by start_research).
    front_matter = ["Sampul", "Lembar Pengesahan", "Abstrak", "Kata Pengantar", "Daftar Isi"]
    chapters = [
        "BAB I Pendahuluan",
        "BAB II Tinjauan Pustaka",
        "BAB III Metode Penelitian",
        "BAB IV Hasil dan Pembahasan",
        "BAB V Kesimpulan dan Saran",
    ]
    for heading in front_matter + chapters:
        assert heading in body_text, f"{heading!r} missing from the exported document"

    positions = [body_text.index(heading) for heading in front_matter + chapters]
    assert positions == sorted(positions), "chapters are not in skripsi_s1 outline order"
    assert body_text.index("Sampul") < body_text.index("BAB I Pendahuluan")
    assert body_text.index("Daftar Isi") < body_text.index("BAB I Pendahuluan")
