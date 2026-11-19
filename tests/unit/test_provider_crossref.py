import httpx
import pytest
import respx

from research_mcp.providers.crossref import CROSSREF, get_bibtex, get_by_doi

ITEM = {
    "DOI": "10.1371/journal.pone.0000308",
    "title": ["Sharing Detailed Research Data Is Associated with Increased Citation Rate"],
    "author": [{"given": "Heather A.", "family": "Piwowar"}],
    "published-print": {"date-parts": [[2007, 3, 21]]},
    "container-title": ["PLoS ONE"],
    "is-referenced-by-count": 900,
    "link": [{"URL": "https://example.test/paper.pdf", "content-type": "application/pdf"}],
}


@pytest.mark.asyncio
@respx.mock
async def test_search_maps_crossref_item_fields():
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json={"message": {"items": [ITEM]}})
    )
    results = await CROSSREF.search("citation rate", None, None, 1)
    r = results[0]
    assert r.doi == "10.1371/journal.pone.0000308"
    assert r.authors == ["Heather A. Piwowar"]
    assert r.year == 2007
    assert r.venue == "PLoS ONE"
    assert r.citation_count == 900
    assert r.pdf_url == "https://example.test/paper.pdf"


@pytest.mark.asyncio
@respx.mock
async def test_get_by_doi_returns_none_on_404():
    respx.get("https://api.crossref.org/works/10.0000/missing").mock(
        return_value=httpx.Response(404)
    )
    assert await get_by_doi("10.0000/missing") is None


@pytest.mark.asyncio
@respx.mock
async def test_get_by_doi_reraises_non_404():
    respx.get("https://api.crossref.org/works/10.0000/boom").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await get_by_doi("10.0000/boom")


@pytest.mark.asyncio
@respx.mock
async def test_get_bibtex_returns_raw_text():
    respx.get(
        "https://api.crossref.org/works/10.1371/journal.pone.0000308/transform/application/x-bibtex"
    ).mock(return_value=httpx.Response(200, text="@article{Piwowar_2007, title={Sharing...} }"))
    bibtex = await get_bibtex("10.1371/journal.pone.0000308")
    assert bibtex.startswith("@article{Piwowar_2007")
