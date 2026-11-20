import pytest
import pytest_asyncio

from research_mcp.db.database import get_connection
from research_mcp.db.repository import (
    PaperNotFoundError,
    ProjectNotFoundError,
    SectionNotFoundError,
    create_project,
    get_paper_by_doi,
    get_paper_by_pdf_path,
    get_section,
    list_sections,
    save_paper_and_sections,
    search_sections,
)
from research_mcp.models import PaperModel
from research_mcp.parsers.sectionizer import SectionData


@pytest_asyncio.fixture
async def db(tmp_path):
    connection = await get_connection(tmp_path / "research_mcp.db")
    project = await create_project(connection, tmp_path / "workspaces", "Paper Repo")
    yield connection, project, tmp_path
    await connection.close()


def make_paper(
    project_id: str,
    *,
    paper_id: str = "paper-1",
    citekey: str = "doe2024study",
    doi: str | None = "10.1/study",
    pdf_path: str | None = None,
    title: str = "A Study",
    created_at: str = "2024-01-01T00:00:00+00:00",
) -> PaperModel:
    return PaperModel(
        id=paper_id,
        project_id=project_id,
        citekey=citekey,
        doi=doi,
        title=title,
        authors=["Jane Doe"],
        year=2024,
        venue="Journal",
        abstract="Abstract",
        citation_count=12,
        pdf_path=pdf_path,
        source_provider="crossref",
        created_at=created_at,
    )


def section(order: int, heading: str, content: str) -> SectionData:
    return SectionData(
        section_order=order,
        heading=heading,
        content=content,
        word_count=len(content.split()),
    )


@pytest.mark.asyncio
async def test_save_paper_and_sections_roundtrips_metadata(db):
    conn, project, _ = db
    paper = make_paper(project.id)
    saved = await save_paper_and_sections(
        conn,
        project.id,
        paper,
        [section(1, "Abstract", "A concise abstract.")],
    )

    found = await get_paper_by_doi(conn, project.id, "10.1/study")
    sections = await list_sections(conn, project.id, paper.citekey)

    assert saved == paper
    assert found == paper
    assert sections == [
        {
            "section_order": 1,
            "heading": "Abstract",
            "word_count": 3,
        }
    ]


@pytest.mark.asyncio
async def test_doi_reingest_preserves_identity_and_replaces_sections(db):
    conn, project, _ = db
    original = make_paper(project.id, pdf_path="/old/paper.pdf")
    await save_paper_and_sections(
        conn, project.id, original, [section(1, "Old", "obsolete phrase")]
    )
    incoming = make_paper(
        project.id,
        paper_id="new-id-ignored",
        citekey="changed2025title",
        title="Updated Study",
        created_at="2025-01-01T00:00:00+00:00",
        pdf_path="/new/paper.pdf",
    )

    updated = await save_paper_and_sections(
        conn, project.id, incoming, [section(1, "Results", "replacement phrase")]
    )
    old_hits = await search_sections(conn, project.id, "obsolete", 10)
    new_hits = await search_sections(conn, project.id, "replacement", 10)

    assert updated.id == original.id
    assert updated.citekey == original.citekey
    assert updated.created_at == original.created_at
    assert updated.title == "Updated Study"
    assert updated.pdf_path == "/new/paper.pdf"
    assert old_hits == []
    assert len(new_hits) == 1
    assert new_hits[0]["heading"] == "Results"


@pytest.mark.asyncio
async def test_metadata_only_reingest_preserves_existing_pdf_and_sections(db):
    conn, project, _ = db
    original = make_paper(project.id, pdf_path="/project/pdfs/existing.pdf")
    await save_paper_and_sections(
        conn, project.id, original, [section(1, "Methods", "keep indexed content")]
    )
    metadata_refresh = make_paper(
        project.id,
        paper_id="new-id",
        pdf_path=None,
        title="Refreshed metadata",
    )

    updated = await save_paper_and_sections(conn, project.id, metadata_refresh, sections=None)
    hits = await search_sections(conn, project.id, "indexed", 10)

    assert updated.pdf_path == original.pdf_path
    assert updated.title == "Refreshed metadata"
    assert len(hits) == 1
    assert "indexed" in hits[0]["snippet"]


@pytest.mark.asyncio
async def test_local_paper_without_doi_can_be_found_by_pdf_path(db):
    conn, project, tmp_path = db
    pdf_path = str(tmp_path / "workspaces" / project.slug / "pdfs" / "local-abcd.pdf")
    paper = make_paper(project.id, doi=None, pdf_path=pdf_path, citekey="unknown0000local")
    await save_paper_and_sections(conn, project.id, paper, [])

    found = await get_paper_by_pdf_path(conn, project.id, pdf_path)

    assert found is not None
    assert found.doi is None
    assert found.citekey == "unknown0000local"


@pytest.mark.asyncio
async def test_list_sections_returns_order_and_word_counts(db):
    conn, project, _ = db
    paper = make_paper(project.id)
    await save_paper_and_sections(
        conn,
        project.id,
        paper,
        [
            section(2, "Methods", "three words"),
            section(1, "Abstract", "two words"),
        ],
    )

    listed = await list_sections(conn, project.id, paper.citekey)

    assert listed == [
        {"section_order": 1, "heading": "Abstract", "word_count": 2},
        {"section_order": 2, "heading": "Methods", "word_count": 2},
    ]


@pytest.mark.asyncio
async def test_get_section_normalizes_heading_and_reports_missing_or_ambiguous(db):
    conn, project, _ = db
    paper = make_paper(project.id)
    await save_paper_and_sections(
        conn,
        project.id,
        paper,
        [
            section(1, "Methods", "first method"),
            section(2, "Methods", "second method"),
        ],
    )

    # Duplicate normalized headings are ambiguous by design.
    with pytest.raises(SectionNotFoundError, match="ambiguous"):
        await get_section(conn, project.id, paper.citekey, " methods ")
    with pytest.raises(SectionNotFoundError, match="not found"):
        await get_section(conn, project.id, paper.citekey, "Discussion")
    with pytest.raises(PaperNotFoundError):
        await get_section(conn, project.id, "missing2024paper", "Methods")
    with pytest.raises(ProjectNotFoundError):
        await get_section(conn, "missing-project", paper.citekey, "Methods")


@pytest.mark.asyncio
async def test_reingest_updates_fts_and_removes_old_terms(db):
    conn, project, _ = db
    paper = make_paper(project.id)
    await save_paper_and_sections(
        conn, project.id, paper, [section(1, "Abstract", "alpha obsolete")]
    )
    await save_paper_and_sections(conn, project.id, paper, [section(1, "Abstract", "beta current")])

    assert await search_sections(conn, project.id, "obsolete", 10) == []
    current = await search_sections(conn, project.id, "current", 10)
    assert len(current) == 1
    assert "current" in current[0]["snippet"]


@pytest.mark.asyncio
async def test_search_sections_is_project_scoped_and_caps_snippets(db):
    conn, project, tmp_path = db
    first = make_paper(project.id, paper_id="paper-one", doi="10.1/one", citekey="doe2024one")
    await save_paper_and_sections(
        conn,
        project.id,
        first,
        [section(1, "Results", "needle " + "ordinary " * 100)],
    )
    other_project = await create_project(conn, tmp_path / "workspaces", "Other Project")
    second = make_paper(
        other_project.id,
        paper_id="paper-two",
        doi="10.1/two",
        citekey="doe2024two",
    )
    await save_paper_and_sections(
        conn,
        other_project.id,
        second,
        [section(1, "Results", "needle in another project")],
    )

    results = await search_sections(conn, project.id, "needle", 10)

    assert len(results) == 1
    assert results[0]["citekey"] == first.citekey
    assert results[0]["heading"] == "Results"
    assert len(results[0]["snippet"]) <= 300
