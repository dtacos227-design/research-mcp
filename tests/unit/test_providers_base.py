import httpx
import pytest
import respx

from research_mcp.providers.base import (
    RetryableHTTPError,
    normalize_doi,
    normalize_title,
    polite_headers,
    request_with_retry,
)


def test_normalize_doi_strips_url_prefix_and_lowercases():
    assert normalize_doi("https://doi.org/10.1038/ABC.123") == "10.1038/abc.123"
    assert normalize_doi(" 10.1038/ABC.123 ") == "10.1038/abc.123"


def test_normalize_title_strips_punctuation_and_case():
    assert normalize_title("Attention Is All You Need!") == normalize_title(
        "attention is all you need"
    )


def test_polite_headers_with_and_without_email():
    assert polite_headers("a@b.com") == {"User-Agent": "ResearchMCP/0.1 (mailto:a@b.com)"}
    assert polite_headers(None) == {"User-Agent": "ResearchMCP/0.1"}


@pytest.mark.asyncio
@respx.mock
async def test_request_with_retry_retries_on_429_then_succeeds():
    route = respx.get("https://example.test/x").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": True})]
    )
    async with httpx.AsyncClient() as client:
        response = await request_with_retry(client, "GET", "https://example.test/x")
    assert response.json() == {"ok": True}
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_request_with_retry_raises_after_exhausting_retries_on_503():
    route = respx.get("https://example.test/y").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        with pytest.raises(RetryableHTTPError):
            await request_with_retry(client, "GET", "https://example.test/y")
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_request_with_retry_does_not_retry_on_404():
    route = respx.get("https://example.test/z").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await request_with_retry(client, "GET", "https://example.test/z")
    assert route.call_count == 1
