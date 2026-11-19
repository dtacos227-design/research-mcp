import httpx
import pytest
import respx

from research_mcp.providers.semantic_scholar import SEMANTIC_SCHOLAR, get_citations, get_references

PAPER = {
    "paperId": "abc123",
    "title": "Attention Is All You Need",
    "abstract": "We propose a new architecture.",
    "year": 2017,
    "authors": [{"name": "Ashish Vaswani"}],
    "externalIds": {"DOI": "10.48550/arXiv.1706.03762"},
    "citationCount": 50000,
    "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762"},
    "venue": "NeurIPS",
}


@pytest.mark.asyncio
@respx.mock
async def test_search_maps_fields():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(200, json={"data": [PAPER]})
    )
    results = await SEMANTIC_SCHOLAR.search("attention is all you need", None, None, 1)
    r = results[0]
    assert r.external_id == "abc123"
    assert r.doi == "10.48550/arXiv.1706.03762"
    assert r.authors == ["Ashish Vaswani"]
    assert r.pdf_url == "https://arxiv.org/pdf/1706.03762"


@pytest.mark.asyncio
@respx.mock
async def test_get_citations_unwraps_citing_paper():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/DOI:10.1/x/citations").mock(
        return_value=httpx.Response(200, json={"data": [{"citingPaper": PAPER}]})
    )
    results = await get_citations("DOI:10.1/x", 1)
    assert results[0].external_id == "abc123"


@pytest.mark.asyncio
@respx.mock
async def test_get_references_unwraps_cited_paper():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/abc123/references").mock(
        return_value=httpx.Response(200, json={"data": [{"citedPaper": PAPER}]})
    )
    results = await get_references("abc123", 1)
    assert results[0].external_id == "abc123"


@pytest.mark.asyncio
@respx.mock
async def test_citation_graph_skips_null_wrapper():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/abc123/citations").mock(
        return_value=httpx.Response(
            200, json={"data": [{"citingPaper": None}, {}, {"citingPaper": PAPER}]}
        )
    )
    results = await get_citations("abc123", 3)
    assert [r.external_id for r in results] == ["abc123"]


@pytest.mark.asyncio
@respx.mock
async def test_search_year_range_param_only_when_given():
    route = respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await SEMANTIC_SCHOLAR.search("q", None, None, 1)
    assert "year" not in route.calls.last.request.url.params
    await SEMANTIC_SCHOLAR.search("q", 2020, 2022, 1)
    assert route.calls.last.request.url.params["year"] == "2020-2022"
    await SEMANTIC_SCHOLAR.search("q", 2020, None, 1)
    assert route.calls.last.request.url.params["year"] == "2020-"
