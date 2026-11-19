import xml.etree.ElementTree as ET

import httpx

from research_mcp.providers.base import LiteratureProvider, SearchResult, request_with_retry

ARXIV_API_URL = "http://export.arxiv.org/api/query"
NS = {"atom": "http://www.w3.org/2005/Atom"}


def _text(entry: ET.Element, tag: str) -> str:
    node = entry.find(f"atom:{tag}", NS)
    if node is None or node.text is None:
        return ""
    return " ".join(node.text.split())


def _to_result(entry: ET.Element) -> SearchResult:
    published = _text(entry, "published")
    pdf_url = None
    for link in entry.findall("atom:link", NS):
        if link.get("type") == "application/pdf":
            pdf_url = link.get("href")
            break
    entry_id = _text(entry, "id")
    return SearchResult(
        source_provider=ArxivProvider.name,
        external_id=entry_id.rsplit("/", 1)[-1],
        title=_text(entry, "title"),
        authors=[
            " ".join(name.text.split())
            for name in entry.findall("atom:author/atom:name", NS)
            if name.text
        ],
        year=int(published[:4]) if published[:4].isdigit() else None,
        abstract=_text(entry, "summary") or None,
        pdf_url=pdf_url,
        landing_page_url=entry_id or None,
    )


class ArxivProvider(LiteratureProvider):
    name = "arxiv"

    async def search(
        self, query: str, year_from: int | None, year_to: int | None, limit: int
    ) -> list[SearchResult]:
        search_query = f"all:{query}"
        if year_from is not None or year_to is not None:
            start = f"{year_from}01010000" if year_from is not None else "000101010000"
            end = f"{year_to}12312359" if year_to is not None else "999912312359"
            search_query += f" AND submittedDate:[{start} TO {end}]"
        params: dict[str, str | int] = {
            "search_query": search_query,
            "start": 0,
            "max_results": limit,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await request_with_retry(client, "GET", ARXIV_API_URL, params=params)
        root = ET.fromstring(response.text)
        return [_to_result(entry) for entry in root.findall("atom:entry", NS)]


ARXIV = ArxivProvider()
