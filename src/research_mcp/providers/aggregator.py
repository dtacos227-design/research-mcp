import asyncio

from pydantic import BaseModel, Field

from research_mcp.providers.arxiv import ARXIV
from research_mcp.providers.base import (
    LiteratureProvider,
    SearchResult,
    normalize_doi,
    normalize_title,
)
from research_mcp.providers.crossref import CROSSREF
from research_mcp.providers.openalex import OPENALEX
from research_mcp.providers.pubmed import PUBMED
from research_mcp.providers.semantic_scholar import SEMANTIC_SCHOLAR


class AggregatedSearch(BaseModel):
    results: list[SearchResult] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)


def _providers() -> dict[str, LiteratureProvider]:
    # Built fresh on every call (not a module-level constant) so that tests patching
    # the module-level OPENALEX/ARXIV/etc. names (patch("...aggregator.OPENALEX")) are
    # observed here — a dict built once at import time would keep stale references to
    # the pre-patch objects.
    return {
        "openalex": OPENALEX,
        "semantic_scholar": SEMANTIC_SCHOLAR,
        "arxiv": ARXIV,
        "pubmed": PUBMED,
        "crossref": CROSSREF,
    }


def _is_duplicate(result: SearchResult, kept: list[SearchResult]) -> bool:
    doi = normalize_doi(result.doi) if result.doi else None
    title = normalize_title(result.title)
    for other in kept:
        other_doi = normalize_doi(other.doi) if other.doi else None
        if doi and other_doi:
            # Both have DOIs: the DOI is authoritative, titles are not compared.
            if doi == other_doi:
                return True
        elif title == normalize_title(other.title):
            return True
    return False


def _interleave(sources: list[str], results: list[SearchResult]) -> list[SearchResult]:
    # Round-robin by provider so providers that report no citation counts (arXiv,
    # PubMed) are not starved by truncation; each provider's own ranking is kept.
    buckets: dict[str, list[SearchResult]] = {}
    for r in results:
        buckets.setdefault(r.source_provider, []).append(r)
    interleaved: list[SearchResult] = []
    while any(buckets.values()):
        for name in sources:
            bucket = buckets.get(name)
            if bucket:
                interleaved.append(bucket.pop(0))
    return interleaved


async def search_all(
    query: str,
    sources: list[str],
    year_from: int | None,
    year_to: int | None,
    limit: int,
) -> AggregatedSearch:
    providers = _providers()
    chosen = [(name, providers[name]) for name in sources if name in providers]
    outcomes = await asyncio.gather(
        *(provider.search(query, year_from, year_to, limit) for _, provider in chosen),
        return_exceptions=True,
    )

    errors: dict[str, str] = {}
    merged: list[SearchResult] = []
    for (name, _), outcome in zip(chosen, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            errors[name] = str(outcome) or type(outcome).__name__
            continue
        for result in outcome:
            if not _is_duplicate(result, merged):
                merged.append(result)

    return AggregatedSearch(results=_interleave(sources, merged)[:limit], errors=errors)
