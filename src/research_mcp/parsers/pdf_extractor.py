import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
import pymupdf
import pymupdf4llm
from pydantic import BaseModel

MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_REDIRECTS = 5
PDF_SIGNATURE = b"%PDF-"
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class PdfMetadata(BaseModel):
    title: str | None = None
    author: str | None = None
    year: int | None = None


def _project_path(path: Path, project_dir: Path) -> Path:
    resolved_root = project_dir.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"PDF path must stay inside project workspace: {resolved_path}")
    return resolved_path


def _temporary_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.{uuid4().hex}.part")


def _validate_pdf_file(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"PDF file does not exist: {path}")
    size = path.stat().st_size
    if size == 0 or size > MAX_PDF_BYTES:
        raise ValueError(f"PDF size must be between 1 and {MAX_PDF_BYTES} bytes")
    with path.open("rb") as stream:
        if stream.read(len(PDF_SIGNATURE)) != PDF_SIGNATURE:
            raise ValueError(f"File does not have a valid PDF signature: {path}")


def import_pdf(source: Path, destination: Path, project_dir: Path) -> Path:
    source_path = source.expanduser().resolve()
    destination_path = _project_path(destination, project_dir)
    _validate_pdf_file(source_path)

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = _temporary_path(destination_path)
    try:
        with source_path.open("rb") as source_stream, temporary_path.open("wb") as target_stream:
            copied = 0
            while chunk := source_stream.read(1024 * 1024):
                copied += len(chunk)
                if copied > MAX_PDF_BYTES:
                    raise ValueError(f"PDF size exceeds {MAX_PDF_BYTES} bytes")
                target_stream.write(chunk)
        _validate_pdf_file(temporary_path)
        os.replace(temporary_path, destination_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return destination_path


def _validate_https_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("PDF download URLs and redirects must use HTTPS")


async def download_pdf(url: str, destination: Path, project_dir: Path) -> Path:
    destination_path = _project_path(destination, project_dir)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = _temporary_path(destination_path)
    current_url = url

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            for redirect_count in range(MAX_REDIRECTS + 1):
                _validate_https_url(current_url)
                async with client.stream("GET", current_url) as response:
                    if response.status_code in REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("PDF download redirect is missing a Location header")
                        if redirect_count == MAX_REDIRECTS:
                            raise ValueError(f"PDF download exceeded {MAX_REDIRECTS} redirects")
                        current_url = urljoin(current_url, location)
                        _validate_https_url(current_url)
                        continue

                    response.raise_for_status()
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > MAX_PDF_BYTES:
                        raise ValueError(f"PDF size exceeds {MAX_PDF_BYTES} bytes")

                    copied = 0
                    prefix = bytearray()
                    with temporary_path.open("wb") as target_stream:
                        async for chunk in response.aiter_bytes():
                            if len(prefix) < len(PDF_SIGNATURE):
                                needed = len(PDF_SIGNATURE) - len(prefix)
                                prefix.extend(chunk[:needed])
                            copied += len(chunk)
                            if copied > MAX_PDF_BYTES:
                                raise ValueError(f"PDF size exceeds {MAX_PDF_BYTES} bytes")
                            target_stream.write(chunk)
                    if bytes(prefix) != PDF_SIGNATURE:
                        raise ValueError("Downloaded response does not have a valid PDF signature")
                    if copied == 0:
                        raise ValueError("Downloaded PDF is empty")
                    os.replace(temporary_path, destination_path)
                    return destination_path

        raise ValueError(f"PDF download exceeded {MAX_REDIRECTS} redirects")
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _markdown_text(result: str | list[dict]) -> str:
    if isinstance(result, str):
        return result.strip()
    chunks = [
        chunk["text"].strip()
        for chunk in result
        if isinstance(chunk.get("text"), str) and chunk["text"].strip()
    ]
    return "\n\n".join(chunks)


def extract_markdown(pdf_path: Path, project_dir: Path) -> str:
    resolved_path = _project_path(pdf_path, project_dir)
    _validate_pdf_file(resolved_path)
    result = pymupdf4llm.to_markdown(
        str(resolved_path),
        page_chunks=True,
        show_progress=False,
    )
    return _markdown_text(result)


def extract_pdf_metadata(pdf_path: Path, project_dir: Path) -> PdfMetadata:
    resolved_path = _project_path(pdf_path, project_dir)
    _validate_pdf_file(resolved_path)
    with pymupdf.open(resolved_path) as document:
        raw = document.metadata or {}

    creation_date = raw.get("creationDate") or ""
    year_match = re.match(r"^(?:D:)?(\d{4})", creation_date)
    year = int(year_match.group(1)) if year_match else None
    title = (raw.get("title") or "").strip() or None
    author = (raw.get("author") or "").strip() or None
    return PdfMetadata(title=title, author=author, year=year)
