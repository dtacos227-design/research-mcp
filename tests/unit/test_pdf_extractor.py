from pathlib import Path

import httpx
import pymupdf
import pytest
import respx

from research_mcp.parsers.pdf_extractor import (
    download_pdf,
    extract_markdown,
    extract_pdf_metadata,
    import_pdf,
)


@pytest.fixture
def pdf_bytes() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "PDF fixture text")
    content = doc.tobytes()
    doc.close()
    return content


def test_import_pdf_copies_valid_pdf_into_project(tmp_path: Path, pdf_bytes: bytes):
    project_dir = tmp_path / "workspace" / "project"
    project_dir.mkdir(parents=True)
    source = tmp_path / "licensed-paper.pdf"
    source.write_bytes(pdf_bytes)
    destination = project_dir / "pdfs" / "paper.pdf"

    result = import_pdf(source, destination, project_dir)

    assert result == destination
    assert destination.read_bytes() == pdf_bytes
    assert source.read_bytes() == pdf_bytes


def test_import_pdf_rejects_non_pdf_and_does_not_leave_output(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    source = tmp_path / "not-a-pdf.pdf"
    source.write_text("not a PDF")
    destination = project_dir / "pdfs" / "paper.pdf"

    with pytest.raises(ValueError, match="PDF"):
        import_pdf(source, destination, project_dir)

    assert not destination.exists()
    assert not destination.with_suffix(".pdf.part").exists()


def test_import_pdf_rejects_path_escape(tmp_path: Path, pdf_bytes: bytes):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    source = tmp_path / "source.pdf"
    source.write_bytes(pdf_bytes)
    destination = tmp_path / "outside.pdf"

    with pytest.raises(ValueError, match="project"):
        import_pdf(source, destination, project_dir)

    assert not destination.exists()


@pytest.mark.asyncio
@respx.mock
async def test_download_pdf_streams_valid_pdf_and_uses_https_only(tmp_path: Path, pdf_bytes: bytes):
    final_url = "https://cdn.example.test/paper.pdf"
    respx.get(final_url).mock(
        return_value=httpx.Response(
            200, content=pdf_bytes, headers={"content-type": "application/pdf"}
        )
    )
    project_dir = tmp_path / "project"
    destination = project_dir / "pdfs" / "paper.pdf"

    result = await download_pdf(final_url, destination, project_dir)

    assert result == destination
    assert destination.read_bytes() == pdf_bytes
    assert destination.read_bytes().startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_download_pdf_rejects_http_url(tmp_path: Path):
    project_dir = tmp_path / "project"
    with pytest.raises(ValueError, match="HTTPS"):
        await download_pdf(
            "http://example.test/paper.pdf",
            project_dir / "pdfs" / "paper.pdf",
            project_dir,
        )


@pytest.mark.asyncio
@respx.mock
async def test_download_pdf_follows_only_https_redirects(tmp_path: Path, pdf_bytes: bytes):
    start_url = "https://repo.example.test/paper"
    final_url = "https://cdn.example.test/paper.pdf"
    respx.get(start_url).mock(return_value=httpx.Response(302, headers={"location": final_url}))
    respx.get(final_url).mock(return_value=httpx.Response(200, content=pdf_bytes))
    project_dir = tmp_path / "project"
    destination = project_dir / "pdfs" / "paper.pdf"

    result = await download_pdf(start_url, destination, project_dir)

    assert result == destination
    assert destination.read_bytes().startswith(b"%PDF-")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start_url", "responses", "message"),
    [
        (
            "https://repo.example.test/paper",
            [httpx.Response(302, headers={"location": "http://cdn.example.test/paper.pdf"})],
            "HTTPS",
        ),
        (
            "https://repo.example.test/paper",
            [
                httpx.Response(302, headers={"location": f"https://repo.example.test/{i}"})
                for i in range(6)
            ],
            "redirect",
        ),
    ],
)
@respx.mock
async def test_download_pdf_rejects_redirect_downgrade_or_more_than_five_redirects(
    tmp_path: Path,
    start_url: str,
    responses: list[httpx.Response],
    message: str,
):
    routes = []
    if message == "redirect":
        current_url = start_url
        for index, response in enumerate(responses):
            next_url = f"https://repo.example.test/{index}"
            routes.append(respx.get(current_url).mock(return_value=response))
            current_url = next_url
    else:
        routes.append(respx.get(start_url).mock(side_effect=responses))
    project_dir = tmp_path / "project"

    with pytest.raises(ValueError, match=message):
        await download_pdf(
            start_url,
            project_dir / "pdfs" / "paper.pdf",
            project_dir,
        )

    assert not (project_dir / "pdfs" / "paper.pdf").exists()
    assert not (project_dir / "pdfs" / "paper.pdf.part").exists()
    if message == "redirect":
        assert sum(route.call_count for route in routes) == 6


@pytest.mark.asyncio
@respx.mock
async def test_download_pdf_rejects_oversized_content_and_cleans_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "research_mcp.parsers.pdf_extractor.MAX_PDF_BYTES",
        4,
    )
    url = "https://repo.example.test/large.pdf"
    respx.get(url).mock(
        return_value=httpx.Response(
            200, content=b"%PDF-12345", headers={"content-type": "application/pdf"}
        )
    )
    project_dir = tmp_path / "project"
    destination = project_dir / "pdfs" / "large.pdf"

    with pytest.raises(ValueError, match="size"):
        await download_pdf(url, destination, project_dir)

    assert not destination.exists()
    assert not destination.with_suffix(".pdf.part").exists()


@pytest.mark.asyncio
@respx.mock
async def test_download_pdf_rejects_non_pdf_body_and_cleans_temp_file(tmp_path: Path):
    url = "https://repo.example.test/not-a-pdf"
    respx.get(url).mock(return_value=httpx.Response(200, content=b"HTML error page"))
    project_dir = tmp_path / "project"
    destination = project_dir / "pdfs" / "paper.pdf"

    with pytest.raises(ValueError, match="PDF"):
        await download_pdf(url, destination, project_dir)

    assert not destination.exists()
    assert not destination.with_suffix(".pdf.part").exists()


def test_extract_markdown_uses_pymupdf4llm_page_chunks_and_joins_text(
    tmp_path: Path, pdf_bytes: bytes, monkeypatch: pytest.MonkeyPatch
):
    project_dir = tmp_path / "project"
    pdf_path = project_dir / "pdfs" / "paper.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(pdf_bytes)
    seen: dict[str, object] = {}

    def fake_to_markdown(path: str, **kwargs):
        seen["path"] = path
        seen.update(kwargs)
        return [{"text": "# Abstract\nPart one."}, {"text": "# Methods\nPart two."}]

    monkeypatch.setattr("pymupdf4llm.to_markdown", fake_to_markdown)

    result = extract_markdown(pdf_path, project_dir)

    assert result == "# Abstract\nPart one.\n\n# Methods\nPart two."
    assert seen["path"] == str(pdf_path)
    assert seen["page_chunks"] is True
    assert seen["show_progress"] is False


def test_extract_pdf_metadata_reads_title_author_and_year_from_document_metadata(
    tmp_path: Path,
):
    project_dir = tmp_path / "project"
    pdf_path = project_dir / "pdfs" / "metadata.pdf"
    pdf_path.parent.mkdir(parents=True)
    doc = pymupdf.open()
    doc.new_page()
    doc.set_metadata(
        {
            "title": "Local Study",
            "author": "Jane Doe",
            "creationDate": "D:20240203112233Z",
        }
    )
    doc.save(pdf_path)
    doc.close()

    metadata = extract_pdf_metadata(pdf_path, project_dir)

    assert metadata.title == "Local Study"
    assert metadata.author == "Jane Doe"
    assert metadata.year == 2024


def test_extract_pdf_metadata_returns_none_for_missing_author_and_year(
    tmp_path: Path,
):
    project_dir = tmp_path / "project"
    pdf_path = project_dir / "pdfs" / "no-metadata.pdf"
    pdf_path.parent.mkdir(parents=True)
    doc = pymupdf.open()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()

    metadata = extract_pdf_metadata(pdf_path, project_dir)

    assert metadata.title is None
    assert metadata.author is None
    assert metadata.year is None
