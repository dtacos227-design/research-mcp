from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.providers.base import SearchResult, UnpaywallResult
from research_mcp.server import resolve_doi


def _crossref_result():
    return SearchResult(
        source_provider="crossref",
        external_id="10.1/x",
        doi="10.1/x",
        title="T",
        authors=["A"],
        year=2020,
        venue="V",
        abstract=None,
        citation_count=5,
        pdf_url=None,
        landing_page_url=None,
    )


@pytest.mark.asyncio
async def test_resolve_doi_merges_crossref_and_unpaywall():
    with (
        patch("research_mcp.server.get_by_doi", AsyncMock(return_value=_crossref_result())),
        patch("research_mcp.server.get_bibtex", AsyncMock(return_value="@article{x}")),
        patch(
            "research_mcp.server.get_oa_location",
            AsyncMock(
                return_value=UnpaywallResult(
                    is_oa=True, pdf_url="https://x.test/p.pdf", landing_page_url=None
                )
            ),
        ),
    ):
        result = await resolve_doi("10.1/x")
    assert result["bibtex"] == "@article{x}"
    assert result["is_oa"] is True
    assert result["pdf_url"] == "https://x.test/p.pdf"


@pytest.mark.asyncio
async def test_resolve_doi_raises_tool_error_when_crossref_has_no_record():
    with patch("research_mcp.server.get_by_doi", AsyncMock(return_value=None)):
        with pytest.raises(ToolError):
            await resolve_doi("10.0000/missing")


@pytest.mark.asyncio
async def test_resolve_doi_degrades_gracefully_when_unpaywall_fails():
    with (
        patch("research_mcp.server.get_by_doi", AsyncMock(return_value=_crossref_result())),
        patch("research_mcp.server.get_bibtex", AsyncMock(return_value="@article{x}")),
        patch("research_mcp.server.get_oa_location", AsyncMock(side_effect=RuntimeError("down"))),
    ):
        result = await resolve_doi("10.1/x")
    assert result["is_oa"] is None
    assert result["pdf_url"] is None
    assert "oa_lookup_error" in result


@pytest.mark.asyncio
async def test_resolve_doi_wraps_unexpected_errors_in_tool_error():
    with patch("research_mcp.server.get_by_doi", AsyncMock(side_effect=RuntimeError("timeout"))):
        with pytest.raises(ToolError, match="RuntimeError: timeout"):
            await resolve_doi("10.1/x")


@pytest.mark.asyncio
async def test_resolve_doi_keeps_metadata_when_bibtex_fails():
    with (
        patch("research_mcp.server.get_by_doi", AsyncMock(return_value=_crossref_result())),
        patch("research_mcp.server.get_bibtex", AsyncMock(side_effect=RuntimeError("down"))),
        patch("research_mcp.server.get_oa_location", AsyncMock(return_value=None)),
    ):
        result = await resolve_doi("10.1/x")
    assert result["title"] == "T"
    assert result["bibtex"] is None
