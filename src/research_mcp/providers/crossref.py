import httpx

from research_mcp.config import get_settings
from research_mcp.providers.base import (
    LiteratureProvider,
    SearchResult,
    polite_headers,
    request_with_retry,
)

CROSSREF_WORKS_URL = "https://api.crossref.org/works"


def _year(item: dict) -> int | None:
    for key in ("published-print", "published-online", "published"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0] is not None:
            return parts[0][0]
    return None


def _map_item(item: dict) -> SearchResult:
    doi = item.get("DOI")
    titles = item.get("title") or []
    venues = item.get("container-title") or []
    authors = [
        " ".join(part for part in (a.get("given"), a.get("family")) if part)
        for a in item.get("author") or []
        if a.get("given") or a.get("family")
    ]
    pdf_url = next(
        (
            link["URL"]
            for link in item.get("link") or []
            if link.get("content-type") == "application/pdf"
        ),
        None,
    )
    return SearchResult(
        source_provider=CrossrefProvider.name,
        external_id=doi or "",
        doi=doi,
        title=titles[0] if titles else "",
        authors=authors,
        year=_year(item),
        venue=venues[0] if venues else None,
        citation_count=item.get("is-referenced-by-count") or 0,
        pdf_url=pdf_url,
        landing_page_url=item.get("URL"),
    )


class CrossrefProvider(LiteratureProvider):
    name = "crossref"

    async def search(
        self, query: str, year_from: int | None, year_to: int | None, limit: int
    ) -> list[SearchResult]:
        email = get_settings().email
        params: dict[str, str | int] = {"query.bibliographic": query, "rows": limit}
        if email:
            params["mailto"] = email
        filters: list[str] = []
        if year_from is not None:
            filters.append(f"from-pub-date:{year_from}-01-01")
        if year_to is not None:
            filters.append(f"until-pub-date:{year_to}-12-31")
        if filters:
            params["filter"] = ",".join(filters)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await request_with_retry(
                client, "GET", CROSSREF_WORKS_URL, params=params, headers=polite_headers(email)
            )
        items = response.json().get("message", {}).get("items", [])
        return [_map_item(item) for item in items]


async def _get_or_none(path: str) -> httpx.Response | None:
    email = get_settings().email
    params = {"mailto": email} if email else None
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            return await request_with_retry(
                client,
                "GET",
                f"{CROSSREF_WORKS_URL}/{path}",
                params=params,
                headers=polite_headers(email),
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise


async def get_by_doi(doi: str) -> SearchResult | None:
    response = await _get_or_none(doi)
    if response is None:
        return None
    return _map_item(response.json()["message"])


async def get_bibtex(doi: str) -> str | None:
    response = await _get_or_none(f"{doi}/transform/application/x-bibtex")
    if response is None:
        return None
    return response.text


CROSSREF = CrossrefProvider()
