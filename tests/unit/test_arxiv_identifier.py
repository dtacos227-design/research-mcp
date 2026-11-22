"""arXiv identifier ingestion: versioned ids and direct id lookup."""

import httpx
import pytest
import respx

from research_mcp.parsers import ingestion
from research_mcp.providers.aggregator import AggregatedSearch
from research_mcp.providers.arxiv import ARXIV
from research_mcp.providers.base import SearchResult

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2304.08069v3</id>
    <title>DETRs Beat YOLOs on Real-time Object Detection</title>
    <published>2024-04-01T00:00:00Z</published>
    <author><name>Yian Zhao</name></author>
    <summary>RT-DETR.</summary>
    <link type="application/pdf" href="https://arxiv.org/pdf/2304.08069v3"/>
  </entry>
</feed>
"""

EMPTY_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>
"""


def _result(external_id: str = "2304.08069v3") -> SearchResult:
    return SearchResult(
        source_provider="arxiv",
        external_id=external_id,
        title="DETRs Beat YOLOs on Real-time Object Detection",
        authors=["Yian Zhao"],
        year=2024,
        item_type="preprint",
    )


@pytest.mark.asyncio
@respx.mock
async def test_fetch_by_id_parses_the_feed():
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=FEED)
    )

    result = await ARXIV.fetch_by_id("2304.08069")

    assert result is not None
    assert result.external_id == "2304.08069v3"
    assert result.title.startswith("DETRs Beat YOLOs")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_by_id_returns_none_for_unknown_id():
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )

    assert await ARXIV.fetch_by_id("9999.99999") is None


@pytest.mark.asyncio
async def test_resolve_identifier_accepts_prefixed_arxiv_id(monkeypatch):
    seen: list[str] = []

    async def fake_fetch(arxiv_id: str) -> SearchResult | None:
        seen.append(arxiv_id)
        return _result()

    monkeypatch.setattr(ingestion.ARXIV, "fetch_by_id", fake_fetch)

    resolved = await ingestion.resolve_identifier("arxiv:2304.08069")

    assert resolved.external_id == "2304.08069v3"
    assert seen == ["2304.08069"]


@pytest.mark.asyncio
async def test_resolve_identifier_strips_version_suffix(monkeypatch):
    seen: list[str] = []

    async def fake_fetch(arxiv_id: str) -> SearchResult | None:
        seen.append(arxiv_id)
        return _result()

    monkeypatch.setattr(ingestion.ARXIV, "fetch_by_id", fake_fetch)

    await ingestion.resolve_identifier("2304.08069v3")

    assert seen == ["2304.08069"]


@pytest.mark.asyncio
async def test_resolve_identifier_matches_versioned_external_id(monkeypatch):
    async def no_direct_hit(arxiv_id: str) -> None:
        return None

    async def fake_search(*args, **kwargs) -> AggregatedSearch:
        return AggregatedSearch(results=[_result("1506.02640v5")], errors={})

    monkeypatch.setattr(ingestion.ARXIV, "fetch_by_id", no_direct_hit)
    monkeypatch.setattr(ingestion, "search_all", fake_search)

    resolved = await ingestion.resolve_identifier("arxiv:1506.02640")

    assert resolved.external_id == "1506.02640v5"
