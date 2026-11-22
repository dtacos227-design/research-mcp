"""Repeated literature searches should not re-hit every provider."""

import pytest

from research_mcp.providers import aggregator
from research_mcp.providers.base import SearchResult


def _result(external_id: str = "w1") -> SearchResult:
    return SearchResult(
        source_provider="openalex",
        external_id=external_id,
        title="Parking Paper",
        authors=["Jane Doe"],
        year=2024,
        item_type="journal-article",
    )


@pytest.fixture
def counting_provider(monkeypatch):
    calls = {"count": 0}

    class Provider:
        name = "openalex"

        async def search(self, query, year_from, year_to, limit):
            calls["count"] += 1
            return [_result()]

    monkeypatch.setattr(aggregator, "_providers", lambda: {"openalex": Provider()})
    aggregator.clear_search_cache()
    yield calls
    aggregator.clear_search_cache()


@pytest.mark.asyncio
async def test_repeated_identical_search_hits_the_cache(counting_provider):
    first = await aggregator.search_all("parking detection", ["openalex"], None, None, 5)
    second = await aggregator.search_all("parking detection", ["openalex"], None, None, 5)

    assert counting_provider["count"] == 1
    assert [r.external_id for r in second.results] == [r.external_id for r in first.results]


@pytest.mark.asyncio
async def test_different_parameters_bypass_the_cache(counting_provider):
    await aggregator.search_all("parking detection", ["openalex"], None, None, 5)
    await aggregator.search_all("parking detection", ["openalex"], None, None, 10)

    assert counting_provider["count"] == 2


@pytest.mark.asyncio
async def test_cache_is_not_returned_when_disabled(monkeypatch, counting_provider):
    monkeypatch.setenv("RESEARCH_MCP_SEARCH_CACHE_TTL", "0")
    aggregator.clear_search_cache()

    await aggregator.search_all("parking detection", ["openalex"], None, None, 5)
    await aggregator.search_all("parking detection", ["openalex"], None, None, 5)

    assert counting_provider["count"] == 2


@pytest.mark.asyncio
async def test_cached_results_are_copies(counting_provider):
    first = await aggregator.search_all("parking detection", ["openalex"], None, None, 5)
    first.results.clear()

    second = await aggregator.search_all("parking detection", ["openalex"], None, None, 5)

    assert len(second.results) == 1
