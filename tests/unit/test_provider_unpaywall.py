import httpx
import pytest
import respx

from research_mcp.providers.unpaywall import get_oa_location

FIXTURE_BEST_HAS_PDF = {
    "is_oa": True,
    "best_oa_location": {
        "url": "https://doi.org/10.x",
        "url_for_pdf": "https://example.test/best.pdf",
    },
    "oa_locations": [
        {"url": "https://doi.org/10.x", "url_for_pdf": "https://example.test/best.pdf"}
    ],
}

FIXTURE_BEST_MISSING_PDF = {
    "is_oa": True,
    "best_oa_location": {"url": "https://doi.org/10.x", "url_for_pdf": None},
    "oa_locations": [
        {"url": "https://doi.org/10.x", "url_for_pdf": None},
        {
            "url": "https://pmc.example.test/landing",
            "url_for_pdf": "https://pmc.example.test/paper.pdf",
        },
    ],
}


@pytest.mark.asyncio
@respx.mock
async def test_uses_best_oa_location_pdf_when_present():
    respx.get("https://api.unpaywall.org/v2/10.x").mock(
        return_value=httpx.Response(200, json=FIXTURE_BEST_HAS_PDF)
    )
    result = await get_oa_location("10.x", "a@b.com")
    assert result.is_oa is True
    assert result.pdf_url == "https://example.test/best.pdf"


@pytest.mark.asyncio
@respx.mock
async def test_falls_back_to_oa_locations_when_best_has_no_pdf():
    respx.get("https://api.unpaywall.org/v2/10.x").mock(
        return_value=httpx.Response(200, json=FIXTURE_BEST_MISSING_PDF)
    )
    result = await get_oa_location("10.x", "a@b.com")
    assert result.pdf_url == "https://pmc.example.test/paper.pdf"


@pytest.mark.asyncio
@respx.mock
async def test_returns_none_on_404():
    respx.get("https://api.unpaywall.org/v2/10.unknown").mock(return_value=httpx.Response(404))
    assert await get_oa_location("10.unknown", "a@b.com") is None


@pytest.mark.asyncio
@respx.mock
async def test_escapes_special_characters_in_path():
    route = respx.route(host="api.unpaywall.org").mock(return_value=httpx.Response(404))
    await get_oa_location("10.1/a#b?c", None)
    assert route.calls[-1].request.url.raw_path.startswith(b"/v2/10.1/a%23b%3Fc")
