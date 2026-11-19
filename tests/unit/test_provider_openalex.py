import httpx
import pytest
import respx

from research_mcp.providers.openalex import OPENALEX

FIXTURE = {
    "results": [
        {
            "id": "https://openalex.org/W2626778328",
            "doi": "https://doi.org/10.48550/arxiv.1706.03762",
            "title": "Attention Is All You Need",
            "publication_year": 2017,
            "authorships": [{"author": {"display_name": "Ashish Vaswani"}}],
            "cited_by_count": 12345,
            "primary_location": {
                "pdf_url": "https://arxiv.org/pdf/1706.03762",
                "landing_page_url": "http://arxiv.org/abs/1706.03762",
                "source": {"display_name": "arXiv (Cornell University)"},
            },
            "abstract_inverted_index": {
                "Attention": [0],
                "is": [1],
                "all": [2],
                "you": [3],
                "need": [4],
            },
        }
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_search_maps_fields_and_reconstructs_abstract():
    respx.get("https://api.openalex.org/works").mock(return_value=httpx.Response(200, json=FIXTURE))
    results = await OPENALEX.search("attention is all you need", None, None, 1)
    assert len(results) == 1
    r = results[0]
    assert r.source_provider == "openalex"
    assert r.doi == "10.48550/arxiv.1706.03762"
    assert r.title == "Attention Is All You Need"
    assert r.authors == ["Ashish Vaswani"]
    assert r.year == 2017
    assert r.citation_count == 12345
    assert r.pdf_url == "https://arxiv.org/pdf/1706.03762"
    assert r.abstract == "Attention is all you need"


@pytest.mark.asyncio
@respx.mock
async def test_search_propagates_http_errors():
    respx.get("https://api.openalex.org/works").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await OPENALEX.search("x", None, None, 1)


@pytest.mark.asyncio
@respx.mock
async def test_year_filter_only_added_when_given():
    route = respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await OPENALEX.search("x", None, None, 5)
    assert "filter" not in route.calls[-1].request.url.params
    await OPENALEX.search("x", 2010, 2020, 5)
    assert (
        route.calls[-1].request.url.params["filter"]
        == "publication_year:>2009,publication_year:<2021"
    )
    await OPENALEX.search("x", 2010, None, 5)
    assert route.calls[-1].request.url.params["filter"] == "publication_year:>2009"
