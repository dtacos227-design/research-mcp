from unittest.mock import AsyncMock, patch

import pytest

from research_mcp.providers.base import SearchResult
from research_mcp.server import get_citation_graph


def _result():
    return SearchResult(
        source_provider="semantic_scholar",
        external_id="1",
        doi="10.1/x",
        title="T",
        authors=["A"],
        year=2020,
        venue=None,
        abstract=None,
        citation_count=1,
        pdf_url=None,
        landing_page_url=None,
    )


@pytest.mark.asyncio
async def test_citations_direction_calls_get_citations_with_doi_prefix():
    with patch("research_mcp.server.get_citations", AsyncMock(return_value=[_result()])) as mocked:
        results = await get_citation_graph("10.1038/x", "citations", 5)
    mocked.assert_awaited_once_with("DOI:10.1038/x", 5)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_references_direction_calls_get_references():
    with patch("research_mcp.server.get_references", AsyncMock(return_value=[_result()])) as mocked:
        await get_citation_graph("10.1038/x", "references", 5)
    mocked.assert_awaited_once_with("DOI:10.1038/x", 5)
