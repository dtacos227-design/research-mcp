import httpx

from research_mcp.config import get_settings
from research_mcp.providers.base import (
    LiteratureProvider,
    SearchResult,
    normalize_item_type,
    polite_headers,
    request_with_retry,
)

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
DOI_PREFIX = "https://doi.org/"


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    if not inverted_index:
        return None
    max_pos = max(pos for positions in inverted_index.values() for pos in positions)
    words: list[str] = [""] * (max_pos + 1)
    for word, positions in inverted_index.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words)


def _strip_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    return doi.removeprefix(DOI_PREFIX)


def _to_result(work: dict) -> SearchResult:
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    return SearchResult(
        source_provider=OpenAlexProvider.name,
        external_id=work["id"],
        doi=_strip_doi(work.get("doi")),
        title=work.get("title") or "",
        authors=[
            a["author"]["display_name"]
            for a in work.get("authorships") or []
            if (a.get("author") or {}).get("display_name")
        ],
        year=work.get("publication_year"),
        venue=source.get("display_name"),
        abstract=_reconstruct_abstract(work.get("abstract_inverted_index")),
        citation_count=work.get("cited_by_count") or 0,
        pdf_url=location.get("pdf_url"),
        landing_page_url=location.get("landing_page_url"),
        item_type=normalize_item_type(OpenAlexProvider.name, work.get("type") or ""),
    )


class OpenAlexProvider(LiteratureProvider):
    name = "openalex"

    async def search(
        self, query: str, year_from: int | None, year_to: int | None, limit: int
    ) -> list[SearchResult]:
        email = get_settings().email
        params: dict[str, str | int] = {"search": query, "per_page": limit}
        if email:
            params["mailto"] = email
        filters: list[str] = []
        if year_from is not None:
            filters.append(f"publication_year:>{year_from - 1}")
        if year_to is not None:
            filters.append(f"publication_year:<{year_to + 1}")
        if filters:
            params["filter"] = ",".join(filters)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await request_with_retry(
                client,
                "GET",
                OPENALEX_WORKS_URL,
                params=params,
                headers=polite_headers(email),
            )
        return [_to_result(work) for work in response.json().get("results", [])]


OPENALEX = OpenAlexProvider()
