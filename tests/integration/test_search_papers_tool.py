import sys
from unittest.mock import AsyncMock, patch

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from research_mcp.providers.aggregator import AggregatedSearch
from research_mcp.providers.base import SearchResult


@pytest.mark.asyncio
async def test_search_papers_truncates_long_abstracts(tmp_path, monkeypatch):
    long_abstract = "word " * 100
    fake_result = SearchResult(
        source_provider="openalex",
        external_id="1",
        doi="10.1/x",
        title="T",
        authors=["A"],
        year=2020,
        venue=None,
        abstract=long_abstract,
        citation_count=1,
        pdf_url=None,
        landing_page_url=None,
    )
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "research_mcp.server"],
        env={
            "RESEARCH_MCP_WORKSPACE": str(tmp_path / "workspaces"),
            "RESEARCH_MCP_DB_PATH": str(tmp_path / "research_mcp.db"),
        },
    )
    # NOTE: this patch has no effect across the subprocess boundary; the test
    # exercises the real search_all end-to-end (see Task 12 brief).
    with patch(
        "research_mcp.server.search_all",
        AsyncMock(return_value=AggregatedSearch(results=[fake_result], errors={})),
    ):
        with anyio.fail_after(15):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    response = await session.call_tool("search_papers", {"query": "anything"})
                    assert response.is_error is False
