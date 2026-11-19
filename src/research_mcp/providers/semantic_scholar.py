import httpx

from research_mcp.providers.base import (
    LiteratureProvider,
    SearchResult,
    polite_headers,
    request_with_retry,
)

SEMANTIC_SCHOLAR_BASE_URL = "https://api.semanticscholar.org/graph/v1"
PAPER_FIELDS = "title,abstract,year,authors,externalIds,citationCount,openAccessPdf,venue"


def _map_paper(paper: dict) -> SearchResult:
    return SearchResult(
        source_provider=SemanticScholarProvider.name,
        external_id=paper["paperId"],
        doi=(paper.get("externalIds") or {}).get("DOI"),
        title=paper.get("title") or "",
        authors=[a["name"] for a in paper.get("authors") or [] if a.get("name")],
        year=paper.get("year"),
        venue=paper.get("venue") or None,
        abstract=paper.get("abstract"),
        citation_count=paper.get("citationCount") or 0,
        pdf_url=(paper.get("openAccessPdf") or {}).get("url"),
    )


async def _get_json(path: str, params: dict[str, str | int]) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await request_with_retry(
            client,
            "GET",
            f"{SEMANTIC_SCHOLAR_BASE_URL}{path}",
            params=params,
            headers=polite_headers(None),
        )
    return response.json()


async def _graph_lookup(
    paper_id: str, edge: str, wrapper_key: str, limit: int
) -> list[SearchResult]:
    data = await _get_json(f"/paper/{paper_id}/{edge}", {"fields": PAPER_FIELDS, "limit": limit})
    results: list[SearchResult] = []
    for item in data.get("data") or []:
        paper = item.get(wrapper_key)
        if paper and paper.get("paperId"):
            results.append(_map_paper(paper))
    return results


class SemanticScholarProvider(LiteratureProvider):
    name = "semantic_scholar"

    async def search(
        self, query: str, year_from: int | None, year_to: int | None, limit: int
    ) -> list[SearchResult]:
        params: dict[str, str | int] = {
            "query": query,
            "limit": limit,
            "fields": PAPER_FIELDS,
        }
        if year_from is not None or year_to is not None:
            params["year"] = f"{year_from or ''}-{year_to or ''}"
        data = await _get_json("/paper/search", params)
        return [_map_paper(p) for p in data.get("data") or [] if p.get("paperId")]


async def get_citations(paper_id: str, limit: int) -> list[SearchResult]:
    return await _graph_lookup(paper_id, "citations", "citingPaper", limit)


async def get_references(paper_id: str, limit: int) -> list[SearchResult]:
    return await _graph_lookup(paper_id, "references", "citedPaper", limit)


SEMANTIC_SCHOLAR = SemanticScholarProvider()
