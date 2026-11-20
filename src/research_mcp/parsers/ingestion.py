import hashlib
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from research_mcp.citations.bibtex import generate_citekey
from research_mcp.config import get_settings
from research_mcp.db.repository import (
    get_paper_by_doi,
    get_paper_by_external_id,
    get_paper_by_pdf_path,
    get_project,
    list_sections,
    save_paper_and_sections,
)
from research_mcp.models import PaperModel
from research_mcp.parsers.pdf_extractor import (
    MAX_PDF_BYTES,
    PdfMetadata,
    download_pdf,
    extract_markdown,
    extract_pdf_metadata,
    import_pdf,
)
from research_mcp.parsers.sectionizer import SectionData, sectionize_markdown
from research_mcp.providers.aggregator import search_all
from research_mcp.providers.base import SearchResult, UnpaywallResult, normalize_doi
from research_mcp.providers.crossref import get_bibtex, get_by_doi
from research_mcp.providers.unpaywall import get_oa_location

SEARCH_SOURCES = ["openalex", "semantic_scholar", "arxiv", "pubmed", "crossref"]
MAX_IDENTIFIER_SEARCH_RESULTS = 50
_DOI_PREFIX = re.compile(r"^10\.\S+$")


class IngestionError(Exception):
    """Raised when a paper identifier cannot be resolved or ingested."""


async def resolve_identifier(identifier: str) -> SearchResult:
    normalized = normalize_doi(identifier)
    if _DOI_PREFIX.match(normalized):
        try:
            result = await get_by_doi(normalized)
        except Exception as exc:
            raise IngestionError(
                f"Could not resolve DOI {identifier!r}: {type(exc).__name__}: {exc}"
            ) from exc
        if result is None:
            raise IngestionError(f"Crossref has no metadata for DOI {identifier!r}.")
        return result

    query_identifier = identifier.strip()
    if query_identifier.upper().startswith("ARXIV:"):
        query_identifier = query_identifier.split(":", 1)[1]
    outcome = await search_all(
        query_identifier,
        SEARCH_SOURCES,
        year_from=None,
        year_to=None,
        limit=MAX_IDENTIFIER_SEARCH_RESULTS,
    )
    result = next(
        (candidate for candidate in outcome.results if candidate.external_id == query_identifier),
        None,
    )
    if result is None:
        error_details = "; ".join(
            f"{provider}: {message}" for provider, message in outcome.errors.items()
        )
        suffix = f" Provider errors: {error_details}" if error_details else ""
        raise IngestionError(
            f"No paper exactly matching identifier {identifier!r} was found.{suffix}"
        )
    return result


def _project_directory(workspace_root: Path, project_slug: str) -> Path:
    root = workspace_root.expanduser().resolve()
    project_dir = (root / project_slug).resolve()
    if not project_dir.is_relative_to(root):
        raise IngestionError("Resolved project directory escaped the workspace root.")
    return project_dir


async def _existing_paper(
    conn: aiosqlite.Connection,
    project_id: str,
    doi: str | None,
    pdf_path: Path | None = None,
    *,
    source_provider: str | None = None,
    external_id: str | None = None,
):
    paper = await get_paper_by_doi(conn, project_id, doi) if doi else None
    if paper is None and source_provider and external_id:
        paper = await get_paper_by_external_id(conn, project_id, source_provider, external_id)
    if paper is None and pdf_path is not None:
        paper = await get_paper_by_pdf_path(conn, project_id, str(pdf_path))
    return paper


async def _make_paper(
    conn: aiosqlite.Connection,
    project_id: str,
    metadata: SearchResult,
    pdf_path: Path | None,
    *,
    lookup_pdf_path: Path | None = None,
    bibtex: str | None = None,
    paper_id: str | None = None,
) -> PaperModel:
    existing = await _existing_paper(
        conn,
        project_id,
        metadata.doi,
        lookup_pdf_path or pdf_path,
        source_provider=metadata.source_provider,
        external_id=metadata.external_id,
    )
    async with conn.execute(
        "SELECT citekey FROM papers WHERE project_id = ?",
        (project_id,),
    ) as cursor:
        existing_citekeys = {row[0] for row in await cursor.fetchall()}

    citekey = (
        existing.citekey
        if existing is not None
        else generate_citekey(
            metadata.authors,
            metadata.year,
            metadata.title,
            existing_citekeys,
        )
    )
    return PaperModel(
        id=existing.id if existing else paper_id or uuid.uuid4().hex,
        project_id=project_id,
        citekey=citekey,
        doi=metadata.doi,
        title=metadata.title or "Untitled paper",
        authors=metadata.authors,
        year=metadata.year,
        venue=metadata.venue,
        abstract=metadata.abstract,
        citation_count=metadata.citation_count,
        pdf_path=str(pdf_path) if pdf_path is not None else None,
        source_provider=metadata.source_provider,
        external_id=metadata.external_id,
        bibtex_raw=bibtex,
        created_at=existing.created_at if existing else datetime.now(UTC).isoformat(),
    )


def _hash_file(path: Path) -> str:
    if path.stat().st_size > MAX_PDF_BYTES:
        raise ValueError(f"PDF size exceeds {MAX_PDF_BYTES} bytes")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_destination(path: Path, project_dir: Path) -> Path:
    resolved_root = project_dir.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise IngestionError(f"Artifact path escaped the project workspace: {resolved_path}")
    return resolved_path


def _write_markdown(path: Path, project_dir: Path, markdown: str) -> Path:
    resolved_path = _safe_destination(path, project_dir)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved_path.with_name(f".{resolved_path.name}.{uuid.uuid4().hex}.part")
    try:
        temporary.write_text(markdown, encoding="utf-8")
        os.replace(temporary, resolved_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return resolved_path


def _metadata_from_local_pdf(
    pdf_metadata: PdfMetadata,
    file_path: Path,
    doi: str | None,
    crossref: SearchResult | None,
    digest: str,
) -> SearchResult:
    title = (crossref.title if crossref and crossref.title else None) or pdf_metadata.title
    if not title:
        title = file_path.stem or "Untitled paper"
    authors = crossref.authors if crossref and crossref.authors else []
    if not authors and pdf_metadata.author:
        authors = [pdf_metadata.author]
    year = crossref.year if crossref and crossref.year is not None else pdf_metadata.year
    return SearchResult(
        source_provider=crossref.source_provider if crossref else "local_pdf",
        external_id=crossref.external_id if crossref else f"local:{digest}",
        doi=crossref.doi if crossref and crossref.doi else doi,
        title=title,
        authors=authors,
        year=year,
        venue=crossref.venue if crossref else None,
        abstract=crossref.abstract if crossref else None,
        citation_count=crossref.citation_count if crossref else 0,
        pdf_url=None,
        landing_page_url=crossref.landing_page_url if crossref else None,
    )


def _finalize_result(
    paper: PaperModel,
    download_status: str,
    warnings: list[str],
    sections: list[SectionData] | None,
    import_status: str | None = None,
    section_count: int | None = None,
) -> dict:
    result = {
        **paper.model_dump(),
        "download_status": download_status,
        "section_count": (section_count if section_count is not None else len(sections or [])),
        "warnings": warnings,
    }
    if import_status is not None:
        result["import_status"] = import_status
    return result


async def _get_bibtex(doi: str | None, warnings: list[str]) -> str | None:
    if not doi:
        return None
    try:
        return await get_bibtex(doi)
    except Exception as exc:
        warnings.append(f"BibTeX lookup failed: {type(exc).__name__}: {exc}")
        return None


def _verified_pdf_url(
    metadata: SearchResult,
    oa_result: UnpaywallResult | None,
) -> str | None:
    if metadata.source_provider == "arxiv" and metadata.pdf_url:
        return metadata.pdf_url
    if metadata.source_provider == "semantic_scholar" and metadata.pdf_url:
        return metadata.pdf_url
    if metadata.doi and oa_result and oa_result.is_oa and oa_result.pdf_url:
        return oa_result.pdf_url
    return None


async def ingest_remote_paper(
    conn: aiosqlite.Connection,
    project_id: str,
    workspace: Path,
    identifier: str,
    download: bool,
) -> dict:
    project = await get_project(conn, project_id)
    project_dir = _project_directory(workspace, project.slug)
    metadata = await resolve_identifier(identifier)
    warnings: list[str] = []
    bibtex = await _get_bibtex(metadata.doi, warnings)
    previous = await _existing_paper(
        conn,
        project_id,
        metadata.doi,
        source_provider=metadata.source_provider,
        external_id=metadata.external_id,
    )
    remote_paper_id = previous.id if previous else uuid.uuid4().hex

    pdf_path = Path(previous.pdf_path) if previous and previous.pdf_path else None
    sections: list[SectionData] | None = None
    download_status = "skipped" if not download else "unavailable"
    new_artifacts: list[Path] = []

    if download:
        oa_result = None
        if metadata.source_provider not in {"arxiv", "semantic_scholar"} and metadata.doi:
            try:
                oa_result = await get_oa_location(metadata.doi, get_settings().email)
            except Exception as exc:
                warnings.append(f"Open-access lookup failed: {type(exc).__name__}: {exc}")
        pdf_url = _verified_pdf_url(metadata, oa_result)
        if pdf_url is None:
            warnings.append("No verified legal open-access PDF URL is available.")
        else:
            project_pdf_dir = _safe_destination(project_dir / "pdfs", project_dir)
            staging_path = project_pdf_dir / f".download-{uuid.uuid4().hex}.pdf"
            try:
                await download_pdf(pdf_url, staging_path, project_dir)
                digest = _hash_file(staging_path)
                artifact_stem = f"{remote_paper_id}-{digest}"
                new_pdf_path = project_pdf_dir / f"{artifact_stem}.pdf"
                new_markdown_path = project_dir / "parsed" / f"{artifact_stem}.md"
                markdown = extract_markdown(staging_path, project_dir)
                parsed_sections = sectionize_markdown(markdown)
                if not markdown.strip() or not parsed_sections:
                    warnings.append("PDF downloaded but no extractable text sections were found.")
                else:
                    sections = parsed_sections
                    markdown_existed = new_markdown_path.exists()
                    _write_markdown(new_markdown_path, project_dir, markdown)
                    if not markdown_existed:
                        new_artifacts.append(new_markdown_path)
                pdf_existed = new_pdf_path.exists()
                os.replace(staging_path, new_pdf_path)
                if not pdf_existed:
                    new_artifacts.append(new_pdf_path)
                pdf_path = new_pdf_path
                download_status = "downloaded"
            except Exception as exc:
                staging_path.unlink(missing_ok=True)
                for artifact in new_artifacts:
                    artifact.unlink(missing_ok=True)
                new_artifacts.clear()
                warnings.append(f"PDF download or parsing failed: {type(exc).__name__}: {exc}")
                sections = None
                download_status = "unavailable"

    paper = await _make_paper(
        conn,
        project_id,
        metadata,
        pdf_path,
        bibtex=bibtex,
        paper_id=remote_paper_id,
    )
    try:
        saved = await save_paper_and_sections(conn, project_id, paper, sections)
    except Exception:
        for artifact in new_artifacts:
            artifact.unlink(missing_ok=True)
        raise

    if previous and previous.pdf_path and saved.pdf_path != previous.pdf_path:
        _remove_old_artifacts(Path(previous.pdf_path), project_dir)
    persisted_section_count = (
        len(sections)
        if sections is not None
        else len(await list_sections(conn, project_id, saved.citekey))
    )
    return _finalize_result(
        saved,
        download_status,
        warnings,
        sections,
        section_count=persisted_section_count,
    )


def _remove_old_artifacts(pdf_path: Path, project_dir: Path) -> None:
    try:
        pdf_path = _safe_destination(pdf_path, project_dir)
        parsed_path = _safe_destination(
            project_dir / "parsed" / f"{pdf_path.stem}.md",
            project_dir,
        )
    except IngestionError:
        return
    pdf_path.unlink(missing_ok=True)
    parsed_path.unlink(missing_ok=True)


async def ingest_local_pdf(
    conn: aiosqlite.Connection,
    project_id: str,
    workspace: Path,
    file_path: Path,
    doi: str | None,
) -> dict:
    project = await get_project(conn, project_id)
    project_dir = _project_directory(workspace, project.slug)
    source = file_path.expanduser().resolve()
    digest = _hash_file(source)

    crossref_metadata: SearchResult | None = None
    warnings: list[str] = []
    normalized_doi = normalize_doi(doi) if doi else None
    if normalized_doi:
        try:
            crossref_metadata = await get_by_doi(normalized_doi)
            if crossref_metadata is None:
                warnings.append(
                    f"Crossref has no metadata for DOI {doi!r}; using local PDF metadata."
                )
        except Exception as exc:
            warnings.append(
                f"Crossref lookup failed; using local PDF metadata: {type(exc).__name__}: {exc}"
            )

    existing_doi = (
        await get_paper_by_doi(conn, project_id, crossref_metadata.doi)
        if crossref_metadata and crossref_metadata.doi
        else None
    )
    local_destination = project_dir / "pdfs" / f"local-{digest}.pdf"
    existing_local = (
        None
        if existing_doi is not None
        else await get_paper_by_pdf_path(conn, project_id, str(local_destination.resolve()))
    )
    existing = existing_doi or existing_local
    pdf_path = Path(existing.pdf_path) if existing and existing.pdf_path else local_destination
    pdf_existed = pdf_path.exists()
    import_pdf(source, pdf_path, project_dir)
    pdf_metadata = extract_pdf_metadata(pdf_path, project_dir)
    metadata = _metadata_from_local_pdf(
        pdf_metadata, source, normalized_doi, crossref_metadata, digest
    )
    bibtex = await _get_bibtex(metadata.doi, warnings)

    sections: list[SectionData] | None
    markdown_path = project_dir / "parsed" / f"{pdf_path.stem}.md"
    try:
        markdown = extract_markdown(pdf_path, project_dir)
        sections = sectionize_markdown(markdown) if markdown.strip() else None
        if sections:
            markdown_existed = markdown_path.exists()
            _write_markdown(markdown_path, project_dir, markdown)
        else:
            markdown_existed = markdown_path.exists()
            warnings.append("PDF imported but no extractable text sections were found.")
    except Exception as exc:
        sections = None
        markdown_existed = markdown_path.exists()
        warnings.append(f"PDF imported but parsing failed: {type(exc).__name__}: {exc}")

    paper = await _make_paper(
        conn,
        project_id,
        metadata,
        pdf_path,
        lookup_pdf_path=pdf_path,
        bibtex=bibtex,
    )
    try:
        saved = await save_paper_and_sections(conn, project_id, paper, sections)
    except Exception:
        if not pdf_existed:
            pdf_path.unlink(missing_ok=True)
        if not markdown_existed:
            markdown_path.unlink(missing_ok=True)
        raise
    persisted_section_count = (
        len(sections)
        if sections is not None
        else len(await list_sections(conn, project_id, saved.citekey))
    )
    if existing and existing.pdf_path and existing.pdf_path != str(pdf_path):
        _remove_old_artifacts(Path(existing.pdf_path), project_dir)
    return _finalize_result(
        saved,
        download_status="not_applicable",
        warnings=warnings,
        sections=sections,
        import_status="imported",
        section_count=persisted_section_count,
    )
