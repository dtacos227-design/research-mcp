from pathlib import Path
from unittest.mock import AsyncMock

import pymupdf
import pytest
import pytest_asyncio

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import (
    create_project,
    get_paper_by_doi,
    get_paper_by_pdf_path,
    search_sections,
)
from research_mcp.parsers import ingestion
from research_mcp.parsers.ingestion import (
    IngestionError,
    ingest_local_pdf,
    ingest_remote_paper,
    resolve_identifier,
)
from research_mcp.parsers.pdf_extractor import MAX_PDF_BYTES
from research_mcp.providers.aggregator import AggregatedSearch
from research_mcp.providers.base import SearchResult, UnpaywallResult


@pytest_asyncio.fixture
async def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.get_bibtex",
        AsyncMock(return_value=None),
    )
    settings = get_settings()
    connection = await get_connection(settings.db_path)
    project = await create_project(connection, settings.workspace_dir, "Ingestion")
    yield connection, project, settings.workspace_dir, tmp_path
    await connection.close()


def search_result(
    *,
    source_provider: str = "crossref",
    external_id: str = "10.1/study",
    doi: str | None = "10.1/study",
    title: str = "A Study",
    authors: list[str] | None = None,
    year: int | None = 2024,
    pdf_url: str | None = None,
) -> SearchResult:
    return SearchResult(
        source_provider=source_provider,
        external_id=external_id,
        doi=doi,
        title=title,
        authors=authors or ["Jane Doe"],
        year=year,
        venue="Journal",
        abstract="A research abstract.",
        citation_count=5,
        pdf_url=pdf_url,
        landing_page_url=None,
    )


def make_local_pdf(path: Path, *, metadata: dict[str, str] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Local PDF abstract phrase")
    if metadata:
        document.set_metadata(metadata)
    document.save(path)
    document.close()
    return path


def patch_parser(monkeypatch: pytest.MonkeyPatch, content: str = "# Abstract\nParsed words."):
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.extract_markdown",
        lambda path, project_dir: content,
    )


def patch_download(
    monkeypatch: pytest.MonkeyPatch,
    contents: bytes = b"%PDF-1.7\n",
) -> AsyncMock:
    async def fake_download(url: str, destination: Path, project_dir: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(contents)
        return destination

    download = AsyncMock(side_effect=fake_download)
    monkeypatch.setattr("research_mcp.parsers.ingestion.download_pdf", download)
    return download


@pytest.mark.asyncio
async def test_doi_ingestion_downloads_only_unpaywall_verified_oa(
    db, monkeypatch: pytest.MonkeyPatch
):
    conn, project, workspace, _ = db
    metadata = search_result()
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.get_by_doi",
        AsyncMock(return_value=metadata),
    )
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.get_bibtex",
        AsyncMock(return_value="@article{doe2024study}"),
    )
    oa_lookup = AsyncMock(
        return_value=UnpaywallResult(
            is_oa=True,
            pdf_url="https://repo.example.test/study.pdf",
            landing_page_url="https://repo.example.test/study",
        )
    )
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.get_oa_location",
        oa_lookup,
    )
    download = patch_download(monkeypatch)
    patch_parser(monkeypatch)

    result = await ingest_remote_paper(conn, project.id, workspace, "10.1/study", True)

    assert result["download_status"] == "downloaded"
    assert result["section_count"] == 1
    assert result["doi"] == "10.1/study"
    assert result["pdf_path"].endswith(".pdf")
    oa_lookup.assert_awaited_once_with("10.1/study", get_settings().email)
    assert download.await_args.args[0] == "https://repo.example.test/study.pdf"


@pytest.mark.asyncio
async def test_unpaywall_without_oa_pdf_saves_metadata_only(db, monkeypatch: pytest.MonkeyPatch):
    conn, project, workspace, _ = db
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.get_by_doi",
        AsyncMock(return_value=search_result()),
    )
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.get_oa_location",
        AsyncMock(return_value=UnpaywallResult(is_oa=False, pdf_url=None)),
    )
    download = AsyncMock()
    monkeypatch.setattr("research_mcp.parsers.ingestion.download_pdf", download)
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.extract_markdown",
        lambda path, project_dir: pytest.fail("no PDF should be parsed"),
    )

    result = await ingest_remote_paper(conn, project.id, workspace, "10.1/study", True)

    assert result["download_status"] == "unavailable"
    assert result["warnings"]
    assert result["pdf_path"] is None
    download.assert_not_awaited()


@pytest.mark.asyncio
async def test_arxiv_identifier_requires_exact_external_id_and_downloads_arxiv_pdf(
    db, monkeypatch: pytest.MonkeyPatch
):
    conn, project, workspace, _ = db
    found = search_result(
        source_provider="arxiv",
        external_id="1706.03762v7",
        doi=None,
        title="Attention Is All You Need",
        pdf_url="https://export.arxiv.org/pdf/1706.03762v7",
    )
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.search_all",
        AsyncMock(return_value=AggregatedSearch(results=[found], errors={})),
    )
    download = patch_download(monkeypatch)
    patch_parser(monkeypatch, "# Introduction\nTransformer text.")

    result = await ingest_remote_paper(conn, project.id, workspace, "1706.03762v7", download=True)

    assert result["source_provider"] == "arxiv"
    assert result["download_status"] == "downloaded"
    assert result["section_count"] == 1
    assert download.await_args.args[0] == "https://export.arxiv.org/pdf/1706.03762v7"


@pytest.mark.asyncio
async def test_reingest_without_doi_same_content_reuses_paper(db, monkeypatch: pytest.MonkeyPatch):
    conn, project, workspace, _ = db
    found = search_result(
        source_provider="arxiv",
        external_id="1706.03762v7",
        doi=None,
        title="Attention Is All You Need",
        pdf_url="https://export.arxiv.org/pdf/1706.03762v7",
    )
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.search_all",
        AsyncMock(return_value=AggregatedSearch(results=[found], errors={})),
    )
    patch_download(monkeypatch)
    patch_parser(monkeypatch, "# Introduction\nTransformer text.")

    first = await ingest_remote_paper(conn, project.id, workspace, "1706.03762v7", download=True)
    second = await ingest_remote_paper(conn, project.id, workspace, "1706.03762v7", download=True)
    pdf_dir = workspace / project.slug / "pdfs"
    pdf_files = list(pdf_dir.glob("*.pdf"))

    assert second["id"] == first["id"]
    assert second["citekey"] == first["citekey"]
    assert second["pdf_path"] == first["pdf_path"]
    assert len(pdf_files) == 1


@pytest.mark.asyncio
async def test_unknown_identifier_raises_actionable_ingestion_error(monkeypatch):
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.get_by_doi",
        AsyncMock(return_value=None),
    )

    with pytest.raises(IngestionError, match="Crossref"):
        await resolve_identifier("10.1/missing")


@pytest.mark.asyncio
async def test_download_false_saves_metadata_without_oa_or_extraction(
    db, monkeypatch: pytest.MonkeyPatch
):
    conn, project, workspace, _ = db
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.get_by_doi",
        AsyncMock(return_value=search_result()),
    )
    oa_lookup = AsyncMock()
    monkeypatch.setattr("research_mcp.parsers.ingestion.get_oa_location", oa_lookup)
    download = AsyncMock()
    monkeypatch.setattr("research_mcp.parsers.ingestion.download_pdf", download)
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.extract_markdown",
        lambda path, project_dir: pytest.fail("download=False must not parse a PDF"),
    )

    result = await ingest_remote_paper(conn, project.id, workspace, "10.1/study", False)

    assert result["download_status"] == "skipped"
    assert result["pdf_path"] is None
    oa_lookup.assert_not_awaited()
    download.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "expected_title", "expected_authors", "expected_year", "expected_prefix"),
    [
        (
            {
                "title": "Local Study",
                "author": "Jane Doe",
                "creationDate": "D:20240203112233Z",
            },
            "Local Study",
            ["Jane Doe"],
            2024,
            "doe2024local",
        ),
        ({}, "study", [], None, "unknown0000study"),
    ],
)
async def test_local_import_uses_embedded_metadata_or_filename_fallback(
    db,
    monkeypatch: pytest.MonkeyPatch,
    metadata: dict[str, str],
    expected_title: str,
    expected_authors: list[str],
    expected_year: int | None,
    expected_prefix: str,
):
    conn, project, workspace, tmp_path = db
    source = make_local_pdf(tmp_path / "source" / "study.pdf", metadata=metadata or None)
    patch_parser(monkeypatch, "# Results\nLocal abstract phrase appears here.")

    result = await ingest_local_pdf(conn, project.id, workspace, source, doi=None)

    assert result["title"] == expected_title
    assert result["authors"] == expected_authors
    assert result["year"] == expected_year
    assert result["citekey"].startswith(expected_prefix)
    assert result["import_status"] == "imported"
    assert result["download_status"] == "not_applicable"


@pytest.mark.asyncio
async def test_reingest_same_doi_preserves_identity_and_replaces_sections(
    db, monkeypatch: pytest.MonkeyPatch
):
    conn, project, workspace, _ = db
    current_metadata = search_result()
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.get_by_doi",
        AsyncMock(side_effect=lambda doi: current_metadata),
    )
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.get_oa_location",
        AsyncMock(
            return_value=UnpaywallResult(is_oa=True, pdf_url="https://repo.example.test/study.pdf")
        ),
    )
    content_counter = 0

    async def changing_download(url: str, destination: Path, project_dir: Path) -> Path:
        nonlocal content_counter
        content_counter += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"%PDF-1.7\n{content_counter}".encode())
        return destination

    monkeypatch.setattr("research_mcp.parsers.ingestion.download_pdf", changing_download)
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.extract_markdown",
        lambda path, project_dir: (
            "# Abstract\ninitial text"
            if path.read_bytes().endswith(b"1")
            else "# Results\nupdated text"
        ),
    )

    first = await ingest_remote_paper(conn, project.id, workspace, "10.1/study", True)
    current_metadata = search_result(title="Updated Study")
    second = await ingest_remote_paper(conn, project.id, workspace, "10.1/study", True)
    old_hits = await search_sections(conn, project.id, "initial", 10)
    new_hits = await search_sections(conn, project.id, "updated", 10)

    assert second["id"] == first["id"]
    assert second["citekey"] == first["citekey"]
    assert second["title"] == "Updated Study"
    assert old_hits == []
    assert len(new_hits) == 1


@pytest.mark.asyncio
async def test_failed_same_content_reingest_keeps_existing_artifacts(
    db, monkeypatch: pytest.MonkeyPatch
):
    conn, project, workspace, _ = db
    monkeypatch.setattr(
        ingestion,
        "get_by_doi",
        AsyncMock(return_value=search_result()),
    )
    monkeypatch.setattr(
        ingestion,
        "get_oa_location",
        AsyncMock(
            return_value=UnpaywallResult(is_oa=True, pdf_url="https://repo.example.test/study.pdf")
        ),
    )
    patch_download(monkeypatch)
    patch_parser(monkeypatch, "# Abstract\nstable content")
    first = await ingest_remote_paper(conn, project.id, workspace, "10.1/study", True)
    stored = await get_paper_by_doi(conn, project.id, "10.1/study")
    assert stored is not None
    assert stored.id == first["id"]
    existing_pdf = Path(first["pdf_path"])
    existing_markdown = workspace / project.slug / "parsed" / f"{existing_pdf.stem}.md"
    pdf_bytes = existing_pdf.read_bytes()
    markdown_text = existing_markdown.read_text(encoding="utf-8")
    failing_save = AsyncMock(side_effect=RuntimeError("simulated database failure"))
    monkeypatch.setattr(ingestion, "save_paper_and_sections", failing_save)

    with pytest.raises(RuntimeError, match="simulated database failure"):
        await ingest_remote_paper(conn, project.id, workspace, "10.1/study", True)

    attempted_paper = failing_save.await_args.args[2]
    assert attempted_paper.id == first["id"]
    assert attempted_paper.pdf_path == str(existing_pdf)
    assert existing_pdf.exists()
    assert existing_pdf.read_bytes() == pdf_bytes
    assert existing_markdown.exists()
    assert existing_markdown.read_text(encoding="utf-8") == markdown_text


@pytest.mark.asyncio
async def test_local_import_hashes_source_file_only_once(db, monkeypatch: pytest.MonkeyPatch):
    conn, project, workspace, tmp_path = db
    source = make_local_pdf(tmp_path / "source" / "hash_once.pdf", metadata={})
    patch_parser(monkeypatch)
    calls: list[Path] = []
    original_hash_file = ingestion._hash_file

    def counting_hash_file(path: Path) -> str:
        calls.append(path)
        return original_hash_file(path)

    monkeypatch.setattr(ingestion, "_hash_file", counting_hash_file)

    await ingest_local_pdf(conn, project.id, workspace, source, doi=None)

    assert calls.count(source) == 1


def test_hash_file_rejects_oversized_input_before_reading(tmp_path: Path):
    oversized = tmp_path / "oversized.pdf"
    with oversized.open("wb") as stream:
        stream.truncate(MAX_PDF_BYTES + 1)

    with pytest.raises(ValueError, match="size"):
        ingestion._hash_file(oversized)


@pytest.mark.asyncio
async def test_repeat_local_import_same_hash_reuses_paper(db, monkeypatch: pytest.MonkeyPatch):
    conn, project, workspace, tmp_path = db
    source = make_local_pdf(tmp_path / "source" / "repeat.pdf", metadata={})
    patch_parser(monkeypatch)

    first = await ingest_local_pdf(conn, project.id, workspace, source, doi=None)
    second = await ingest_local_pdf(conn, project.id, workspace, source, doi=None)
    found = await get_paper_by_pdf_path(conn, project.id, first["pdf_path"])

    assert second["id"] == first["id"]
    assert second["citekey"] == first["citekey"]
    assert found is not None


@pytest.mark.asyncio
async def test_repeat_local_import_with_doi_reuses_same_path_without_churn(
    db, monkeypatch: pytest.MonkeyPatch
):
    conn, project, workspace, tmp_path = db
    source = make_local_pdf(tmp_path / "source" / "doi_repeat.pdf", metadata={})
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.get_by_doi",
        AsyncMock(return_value=search_result(doi="10.1/repeat")),
    )
    patch_parser(monkeypatch)

    first = await ingest_local_pdf(conn, project.id, workspace, source, doi="10.1/repeat")
    second = await ingest_local_pdf(conn, project.id, workspace, source, doi="10.1/repeat")
    pdf_dir = workspace / project.slug / "pdfs"
    pdf_files = list(pdf_dir.glob("*.pdf"))

    assert second["id"] == first["id"]
    assert second["citekey"] == first["citekey"]
    assert second["pdf_path"] == first["pdf_path"]
    assert len(pdf_files) == 1


@pytest.mark.asyncio
async def test_metadata_only_reingest_preserves_existing_pdf_and_sections(
    db, monkeypatch: pytest.MonkeyPatch
):
    conn, project, workspace, _ = db
    monkeypatch.setattr(
        "research_mcp.parsers.ingestion.get_by_doi",
        AsyncMock(return_value=search_result()),
    )
    oa = AsyncMock(
        side_effect=[
            UnpaywallResult(is_oa=True, pdf_url="https://repo.example.test/paper.pdf"),
            UnpaywallResult(is_oa=False, pdf_url=None),
        ]
    )
    monkeypatch.setattr("research_mcp.parsers.ingestion.get_oa_location", oa)
    patch_download(monkeypatch)
    patch_parser(monkeypatch, "# Methods\nretain this section")

    first = await ingest_remote_paper(conn, project.id, workspace, "10.1/study", True)
    second = await ingest_remote_paper(conn, project.id, workspace, "10.1/study", True)
    hits = await search_sections(conn, project.id, "retain", 10)
    stored = await get_paper_by_doi(conn, project.id, "10.1/study")

    assert first["pdf_path"] == second["pdf_path"]
    assert second["download_status"] == "unavailable"
    assert stored is not None
    assert stored.pdf_path == first["pdf_path"]
    assert len(hits) == 1
