import re
from abc import ABC, abstractmethod
from typing import ClassVar

import httpx
from pydantic import BaseModel, Field
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

RETRY_STATUS_CODES = {429, 503}


class AuthorName(BaseModel):
    given: str | None = None
    family: str | None = None
    literal: str | None = None


class SearchResult(BaseModel):
    source_provider: str
    external_id: str
    doi: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    authors_structured: list[AuthorName] | None = None
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    citation_count: int = 0
    pdf_url: str | None = None
    landing_page_url: str | None = None
    item_type: str = "other"


ITEM_TYPE_VOCABULARY = frozenset(
    {
        "journal-article",
        "proceedings-article",
        "book-chapter",
        "preprint",
        "dataset",
        "report",
        "review",
        "other",
    }
)

# Per-provider raw-value -> normalized item_type mapping (spec §1). Multi-valued
# providers (PubMed, Semantic Scholar) are expected to select a single raw value
# by priority scan BEFORE calling this function; it maps one raw string, not a
# list.
_ITEM_TYPE_MAP: dict[str, dict[str, str]] = {
    "openalex": {
        "article": "journal-article",
        "preprint": "preprint",
        "book-chapter": "book-chapter",
        "dataset": "dataset",
        "report": "report",
        "review": "review",
    },
    "crossref": {
        "journal-article": "journal-article",
        "proceedings-article": "proceedings-article",
        "book-chapter": "book-chapter",
        "dataset": "dataset",
        "report": "report",
        "monograph": "report",
        "posted-content": "preprint",
        "preprint": "preprint",
    },
    "semantic_scholar": {
        "JournalArticle": "journal-article",
        "Conference": "proceedings-article",
        "Review": "review",
        "Dataset": "dataset",
    },
    "pubmed": {
        "Journal Article": "journal-article",
        "Review": "review",
    },
}


def normalize_item_type(source_provider: str, raw_type: str) -> str:
    """Map a provider's raw publication-type value to our closed vocabulary."""
    if not raw_type:
        return "other"
    mapped = _ITEM_TYPE_MAP.get(source_provider, {}).get(raw_type)
    return mapped if mapped in ITEM_TYPE_VOCABULARY else "other"


class UnpaywallResult(BaseModel):
    is_oa: bool
    pdf_url: str | None = None
    landing_page_url: str | None = None


class LiteratureProvider(ABC):
    name: ClassVar[str]

    @abstractmethod
    async def search(
        self, query: str, year_from: int | None, year_to: int | None, limit: int
    ) -> list[SearchResult]: ...


class RetryableHTTPError(Exception):
    pass


async def request_with_retry(
    client: httpx.AsyncClient, method: str, url: str, **kwargs
) -> httpx.Response:
    async for attempt in AsyncRetrying(
        retry=retry_if_exception_type(RetryableHTTPError),
        wait=wait_exponential(multiplier=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    ):
        with attempt:
            response = await client.request(method, url, **kwargs)
            if response.status_code in RETRY_STATUS_CODES:
                raise RetryableHTTPError(f"{method} {url} -> {response.status_code}")
            response.raise_for_status()
            return response


def normalize_doi(doi: str) -> str:
    cleaned = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    return cleaned


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def polite_headers(email: str | None) -> dict[str, str]:
    if email is None:
        return {"User-Agent": "ResearchMCP/0.1"}
    return {"User-Agent": f"ResearchMCP/0.1 (mailto:{email})"}
