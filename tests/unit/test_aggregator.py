from unittest.mock import AsyncMock, patch

import pytest

from research_mcp.providers.aggregator import search_all
from research_mcp.providers.base import SearchResult


def _result(**overrides):
    base = dict(
        source_provider="openalex",
        external_id="1",
        doi="10.1/x",
        title="Same Paper",
        authors=["A"],
        year=2020,
        venue=None,
        abstract=None,
        citation_count=10,
        pdf_url=None,
        landing_page_url=None,
    )
    base.update(overrides)
    return SearchResult(**base)


@pytest.mark.asyncio
async def test_dedups_by_doi_keeping_first_seen():
    with (
        patch("research_mcp.providers.aggregator.OPENALEX") as openalex,
        patch("research_mcp.providers.aggregator.ARXIV") as arxiv,
    ):
        openalex.search = AsyncMock(return_value=[_result(source_provider="openalex")])
        arxiv.search = AsyncMock(return_value=[_result(source_provider="arxiv", external_id="2")])
        outcome = await search_all("same paper", ["openalex", "arxiv"], None, None, 10)
    assert len(outcome.results) == 1
    assert outcome.results[0].source_provider == "openalex"


@pytest.mark.asyncio
async def test_dedups_by_normalized_title_when_no_doi():
    with (
        patch("research_mcp.providers.aggregator.OPENALEX") as openalex,
        patch("research_mcp.providers.aggregator.ARXIV") as arxiv,
    ):
        openalex.search = AsyncMock(
            return_value=[_result(doi=None, title="Attention is all you need!")]
        )
        arxiv.search = AsyncMock(
            return_value=[_result(doi=None, title="Attention Is All You Need", external_id="2")]
        )
        outcome = await search_all("attention", ["openalex", "arxiv"], None, None, 10)
    assert len(outcome.results) == 1


@pytest.mark.asyncio
async def test_one_provider_failing_does_not_drop_the_others():
    with (
        patch("research_mcp.providers.aggregator.OPENALEX") as openalex,
        patch("research_mcp.providers.aggregator.ARXIV") as arxiv,
    ):
        openalex.search = AsyncMock(side_effect=RuntimeError("boom"))
        arxiv.search = AsyncMock(return_value=[_result(source_provider="arxiv")])
        outcome = await search_all("x", ["openalex", "arxiv"], None, None, 10)
    assert len(outcome.results) == 1
    assert "openalex" in outcome.errors
    assert "arxiv" not in outcome.errors


@pytest.mark.asyncio
async def test_truncates_merged_results_to_limit():
    with patch("research_mcp.providers.aggregator.OPENALEX") as openalex:
        openalex.search = AsyncMock(
            return_value=[
                _result(external_id=str(i), doi=f"10.1/{i}", citation_count=i) for i in range(5)
            ]
        )
        outcome = await search_all("x", ["openalex"], None, None, 2)
    assert len(outcome.results) == 2
    assert [r.external_id for r in outcome.results] == ["0", "1"]


@pytest.mark.asyncio
async def test_truncation_keeps_zero_citation_provider_represented():
    with (
        patch("research_mcp.providers.aggregator.OPENALEX") as openalex,
        patch("research_mcp.providers.aggregator.ARXIV") as arxiv,
    ):
        openalex.search = AsyncMock(
            return_value=[
                _result(external_id=str(i), doi=f"10.1/{i}", citation_count=1000 - i)
                for i in range(3)
            ]
        )
        arxiv.search = AsyncMock(
            return_value=[
                _result(
                    source_provider="arxiv",
                    external_id="a1",
                    doi=None,
                    title="Arxiv Only",
                    citation_count=0,
                )
            ]
        )
        outcome = await search_all("x", ["openalex", "arxiv"], None, None, 2)
    assert [r.source_provider for r in outcome.results] == ["openalex", "arxiv"]


@pytest.mark.asyncio
async def test_dedups_by_title_when_only_one_side_has_doi_keeping_first_seen():
    with (
        patch("research_mcp.providers.aggregator.OPENALEX") as openalex,
        patch("research_mcp.providers.aggregator.ARXIV") as arxiv,
    ):
        openalex.search = AsyncMock(
            return_value=[_result(source_provider="openalex", doi="10.1/x", title="Same Paper")]
        )
        arxiv.search = AsyncMock(
            return_value=[
                _result(source_provider="arxiv", doi=None, title="same paper!", external_id="2")
            ]
        )
        outcome = await search_all("same paper", ["openalex", "arxiv"], None, None, 10)
    assert len(outcome.results) == 1
    assert outcome.results[0].source_provider == "openalex"


@pytest.mark.asyncio
async def test_keeps_both_when_dois_differ_even_if_titles_match():
    with (
        patch("research_mcp.providers.aggregator.OPENALEX") as openalex,
        patch("research_mcp.providers.aggregator.ARXIV") as arxiv,
    ):
        openalex.search = AsyncMock(
            return_value=[_result(source_provider="openalex", doi="10.1/a")]
        )
        arxiv.search = AsyncMock(
            return_value=[_result(source_provider="arxiv", doi="10.1/b", external_id="2")]
        )
        outcome = await search_all("same paper", ["openalex", "arxiv"], None, None, 10)
    assert len(outcome.results) == 2
