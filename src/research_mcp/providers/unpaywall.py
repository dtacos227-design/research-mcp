import httpx

from research_mcp.providers.base import UnpaywallResult, polite_headers, request_with_retry

UNPAYWALL_URL = "https://api.unpaywall.org/v2"


def _first_pdf_url(data: dict) -> str | None:
    best = data.get("best_oa_location") or {}
    if best.get("url_for_pdf"):
        return best["url_for_pdf"]
    for location in data.get("oa_locations", []):
        if location.get("url_for_pdf"):
            return location["url_for_pdf"]
    return None


async def get_oa_location(doi: str, email: str | None) -> UnpaywallResult | None:
    params = {"email": email} if email else None
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await request_with_retry(
                client,
                "GET",
                f"{UNPAYWALL_URL}/{doi}",
                params=params,
                headers=polite_headers(email),
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
    data = response.json()
    best = data.get("best_oa_location") or {}
    return UnpaywallResult(
        is_oa=data.get("is_oa", False),
        pdf_url=_first_pdf_url(data),
        landing_page_url=best.get("url"),
    )
