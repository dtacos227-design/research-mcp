import pytest
import pytest_asyncio

from research_mcp.db.database import get_connection
from research_mcp.db.repository import create_project, get_paper_by_doi, save_paper_and_sections
from research_mcp.models import PaperModel
from research_mcp.providers.base import AuthorName


@pytest_asyncio.fixture
async def db(tmp_path):
    connection = await get_connection(tmp_path / "research_mcp.db")
    project = await create_project(connection, tmp_path / "workspaces", "Authors")
    yield connection, project, tmp_path
    await connection.close()


def test_author_name_roundtrip():
    a = AuthorName(given="Ludwig", family="van der Berg")
    d = a.model_dump()
    assert d == {"given": "Ludwig", "family": "van der Berg", "literal": None}
    assert AuthorName.model_validate(d) == a


def test_crossref_mapper_preserves_compound_family():
    from research_mcp.providers.crossref import _map_item

    result = _map_item(
        {
            "DOI": "10.1/x",
            "title": ["T"],
            "author": [{"given": "Ludwig", "family": "van der Berg"}],
        }
    )
    assert result.authors_structured == [AuthorName(given="Ludwig", family="van der Berg")]


def test_pubmed_mapper_uses_collective_name_as_literal():
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
              <AuthorList>
                <Author><CollectiveName>COVID-19 Study Group</CollectiveName></Author>
              </AuthorList>
            </Article>
          </MedlineCitation>
        </PubmedArticle>
        """
    )
    result = _to_result(article)
    assert result.authors_structured == [AuthorName(literal="COVID-19 Study Group")]


@pytest.mark.asyncio
async def test_structured_authors_survive_repository_roundtrip(db):
    conn, project, _ = db
    paper = PaperModel(
        id="p1",
        project_id=project.id,
        citekey="berg2024study",
        doi="10.1/x",
        title="A Study",
        authors=["Ludwig van der Berg"],
        authors_structured=[{"given": "Ludwig", "family": "van der Berg", "literal": None}],
        year=2024,
        created_at="2024-01-01T00:00:00+00:00",
    )
    await save_paper_and_sections(conn, project.id, paper, sections=None)
    got = await get_paper_by_doi(conn, project.id, "10.1/x")
    assert got is not None
    assert got.authors_structured == [
        {"given": "Ludwig", "family": "van der Berg", "literal": None}
    ]
