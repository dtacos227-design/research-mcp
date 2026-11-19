import httpx
import pytest
import respx

from research_mcp.providers.pubmed import PUBMED

ESEARCH_FIXTURE = {"esearchresult": {"idlist": ["12345"]}}
EFETCH_FIXTURE = """<?xml version="1.0"?>
<PubmedArticleSet>
<PubmedArticle><MedlineCitation><PMID Version="1">12345</PMID>
<Article><Journal><Title>PLoS One</Title>
<JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue></Journal>
<ArticleTitle>A Pandemic Study</ArticleTitle>
<Abstract><AbstractText>Background text.</AbstractText>
<AbstractText>Results text.</AbstractText></Abstract>
<ELocationID EIdType="doi">10.1371/journal.pone.0000308</ELocationID>
<AuthorList><Author><LastName>Doe</LastName><ForeName>Jane</ForeName></Author></AuthorList>
</Article></MedlineCitation></PubmedArticle>
</PubmedArticleSet>"""


@pytest.mark.asyncio
@respx.mock
async def test_search_joins_multi_part_abstract_and_extracts_doi():
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json=ESEARCH_FIXTURE)
    )
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(200, content=EFETCH_FIXTURE)
    )
    results = await PUBMED.search("pandemic", None, None, 1)
    assert len(results) == 1
    r = results[0]
    assert r.source_provider == "pubmed"
    assert r.external_id == "12345"
    assert r.title == "A Pandemic Study"
    assert r.abstract == "Background text. Results text."
    assert r.doi == "10.1371/journal.pone.0000308"
    assert r.authors == ["Jane Doe"]
    assert r.year == 2021


@pytest.mark.asyncio
@respx.mock
async def test_search_returns_empty_list_without_calling_efetch_when_no_ids():
    esearch = respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": []}})
    )
    efetch = respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi")
    assert await PUBMED.search("zzz_no_such_paper", None, None, 1) == []
    assert esearch.call_count == 1
    assert efetch.call_count == 0
