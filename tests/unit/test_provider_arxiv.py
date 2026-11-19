import httpx
import pytest
import respx

from research_mcp.providers.arxiv import ARXIV

FIXTURE = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <title>Attention Is All You Need</title>
    <summary>We propose a new simple network architecture, the Transformer.</summary>
    <published>2017-06-12T17:57:34Z</published>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <link rel="related" type="application/pdf" href="https://arxiv.org/pdf/1706.03762"/>
  </entry>
</feed>"""


@pytest.mark.asyncio
@respx.mock
async def test_search_parses_atom_entries():
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(
            200, content=FIXTURE, headers={"content-type": "application/atom+xml"}
        )
    )
    results = await ARXIV.search("attention is all you need", None, None, 1)
    assert len(results) == 1
    r = results[0]
    assert r.source_provider == "arxiv"
    assert r.external_id == "1706.03762v7"
    assert r.title == "Attention Is All You Need"
    assert r.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert r.year == 2017
    assert r.pdf_url == "https://arxiv.org/pdf/1706.03762"


@pytest.mark.asyncio
@respx.mock
async def test_search_returns_empty_list_for_no_entries():
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(
            200,
            content='<feed xmlns="http://www.w3.org/2005/Atom"></feed>',
            headers={"content-type": "application/atom+xml"},
        )
    )
    assert await ARXIV.search("zzz_no_such_paper", None, None, 1) == []
