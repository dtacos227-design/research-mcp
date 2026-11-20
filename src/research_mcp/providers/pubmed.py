import datetime
import xml.etree.ElementTree as ET

import httpx

from research_mcp.providers.base import (
    LiteratureProvider,
    SearchResult,
    normalize_item_type,
    request_with_retry,
)

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


# Priority scan over a multi-valued PublicationTypeList: prefer the most
# specific/informative type, not the first in API order (spec §6b.2).
_PUBMED_TYPE_PRIORITY = ("Review", "Journal Article")


def _pick_publication_type(types: list[str]) -> str:
    for candidate in _PUBMED_TYPE_PRIORITY:
        if candidate in types:
            return candidate
    return types[0] if types else ""


def _author_name(author_el: ET.Element) -> str:
    collective = author_el.find("CollectiveName")
    if collective is not None and collective.text:
        return collective.text
    fore = author_el.find("ForeName")
    last = author_el.find("LastName")
    return " ".join(p.text for p in (fore, last) if p is not None and p.text)


def _inner_text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return " ".join("".join(el.itertext()).split())


def _to_result(pubmed_article: ET.Element) -> SearchResult:
    citation = pubmed_article.find("MedlineCitation")
    assert citation is not None
    article = citation.find("Article")
    assert article is not None
    year_el = article.find("Journal/JournalIssue/PubDate/Year")
    year_text = (year_el.text or "").strip() if year_el is not None else ""
    doi_el = article.find("ELocationID[@EIdType='doi']")
    abstract = " ".join(
        text for el in article.findall("Abstract/AbstractText") if (text := _inner_text(el))
    )
    venue = _inner_text(article.find("Journal/Title"))
    pmid = _inner_text(citation.find("PMID"))
    names = [_author_name(a) for a in article.findall("AuthorList/Author")]
    publication_types = [
        _inner_text(pt) for pt in article.findall("PublicationTypeList/PublicationType")
    ]
    raw_type = _pick_publication_type(publication_types)
    return SearchResult(
        source_provider=PubmedProvider.name,
        external_id=pmid,
        doi=(doi_el.text or "").strip() or None if doi_el is not None else None,
        title=_inner_text(article.find("ArticleTitle")),
        authors=[n for n in names if n],
        year=int(year_text) if year_text.isdigit() else None,
        venue=venue or None,
        abstract=abstract or None,
        landing_page_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
        item_type=normalize_item_type(PubmedProvider.name, raw_type),
    )


class PubmedProvider(LiteratureProvider):
    name = "pubmed"

    async def search(
        self, query: str, year_from: int | None, year_to: int | None, limit: int
    ) -> list[SearchResult]:
        search_params: dict[str, str | int] = {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": limit,
        }
        if year_from is not None or year_to is not None:
            search_params["mindate"] = year_from if year_from is not None else 1800
            search_params["maxdate"] = (
                year_to if year_to is not None else datetime.date.today().year
            )
            search_params["datetype"] = "pdat"

        async with httpx.AsyncClient(timeout=30.0) as client:
            search_response = await request_with_retry(
                client, "GET", ESEARCH_URL, params=search_params
            )
            ids = search_response.json().get("esearchresult", {}).get("idlist", [])
            if not ids:
                return []
            fetch_response = await request_with_retry(
                client,
                "GET",
                EFETCH_URL,
                params={
                    "db": "pubmed",
                    "id": ",".join(ids),
                    "retmode": "xml",
                    "rettype": "abstract",
                },
            )
        root = ET.fromstring(fetch_response.content)
        return [_to_result(a) for a in root.findall("PubmedArticle")]


PUBMED = PubmedProvider()
