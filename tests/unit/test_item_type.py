import pytest

from research_mcp.providers.base import normalize_item_type


@pytest.mark.parametrize(
    ("provider", "raw", "expected"),
    [
        ("openalex", "article", "journal-article"),
        ("openalex", "preprint", "preprint"),
        ("openalex", "book-chapter", "book-chapter"),
        ("openalex", "dataset", "dataset"),
        ("openalex", "report", "report"),
        ("openalex", "review", "review"),
        ("openalex", "weird-unknown", "other"),
        ("crossref", "journal-article", "journal-article"),
        ("crossref", "proceedings-article", "proceedings-article"),
        ("crossref", "book-chapter", "book-chapter"),
        ("crossref", "dataset", "dataset"),
        ("crossref", "report", "report"),
        ("crossref", "monograph", "report"),
        ("crossref", "posted-content", "preprint"),
        ("crossref", "preprint", "preprint"),
        ("crossref", "something-else", "other"),
        ("semantic_scholar", "JournalArticle", "journal-article"),
        ("semantic_scholar", "Conference", "proceedings-article"),
        ("semantic_scholar", "Review", "review"),
        ("semantic_scholar", "Dataset", "dataset"),
        ("semantic_scholar", "Editorial", "other"),
        ("pubmed", "Journal Article", "journal-article"),
        ("pubmed", "Review", "review"),
        ("pubmed", "Meta-Analysis", "other"),
    ],
)
def test_normalize_item_type(provider, raw, expected):
    assert normalize_item_type(provider, raw) == expected


def test_openalex_mapper_maps_type():
    from research_mcp.providers.openalex import _to_result

    result = _to_result({"type": "preprint", "id": "https://openalex.org/W1", "title": "T"})
    assert result.item_type == "preprint"


def test_crossref_mapper_maps_type():
    from research_mcp.providers.crossref import _map_item

    result = _map_item({"type": "proceedings-article", "DOI": "10.1/x", "title": ["T"]})
    assert result.item_type == "proceedings-article"


def test_semantic_scholar_mapper_prefers_review_over_journal_article():
    from research_mcp.providers.semantic_scholar import _map_paper

    result = _map_paper(
        {"paperId": "p1", "title": "T", "publicationTypes": ["Review", "JournalArticle"]}
    )
    assert result.item_type == "review"


def test_arxiv_mapper_is_preprint():
    import xml.etree.ElementTree as ET

    from research_mcp.providers.arxiv import _to_result

    entry = ET.fromstring(
        """
        <entry xmlns="http://www.w3.org/2005/Atom">
          <id>https://arxiv.org/abs/1706.03762v7</id>
          <title>Attention Is All You Need</title>
          <published>2017-06-12T00:00:00Z</published>
          <author><name>Jane Doe</name></author>
        </entry>
        """
    )
    result = _to_result(entry)
    assert result.item_type == "preprint"


def test_pubmed_mapper_prefers_review():
    import xml.etree.ElementTree as ET

    from research_mcp.providers.pubmed import _to_result

    article = ET.fromstring(
        """
        <PubmedArticle>
          <MedlineCitation>
            <PMID>123</PMID>
            <Article>
              <ArticleTitle>T</ArticleTitle>
              <Journal>
                <Title>J</Title>
                <JournalIssue><PubDate><Year>2020</Year></PubDate></JournalIssue>
              </Journal>
              <PublicationTypeList>
                <PublicationType>Journal Article</PublicationType>
                <PublicationType>Review</PublicationType>
              </PublicationTypeList>
            </Article>
          </MedlineCitation>
        </PubmedArticle>
        """
    )
    result = _to_result(article)
    assert result.item_type == "review"
