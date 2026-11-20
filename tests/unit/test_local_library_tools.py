import pytest
import pytest_asyncio
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.config import get_settings
from research_mcp.db.database import get_connection
from research_mcp.db.repository import create_project, save_paper_and_sections
from research_mcp.models import PaperModel
from research_mcp.parsers.sectionizer import SectionData
from research_mcp.server import (
    list_paper_sections,
    read_paper_section,
    search_local_library,
)


@pytest_asyncio.fixture
async def library(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspaces"
    db_path = tmp_path / "research_mcp.db"
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(workspace_root))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(db_path))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    first = await create_project(conn, settings.workspace_dir, "First Project")
    second = await create_project(conn, settings.workspace_dir, "Second Project")
    paper = PaperModel(
        id="paper-first",
        project_id=first.id,
        citekey="doe2024first",
        doi="10.1/first",
        title="First Paper",
        authors=["Jane Doe"],
        year=2024,
        created_at="2024-01-01T00:00:00Z",
    )
    await save_paper_and_sections(
        conn,
        first.id,
        paper,
        [
            SectionData(
                section_order=1,
                heading="Abstract",
                content="Local retrieval shared phrase",
                word_count=4,
            ),
            SectionData(
                section_order=2,
                heading="Methods",
                content="Methods details",
                word_count=2,
            ),
        ],
    )
    second_paper = PaperModel(
        id="paper-second",
        project_id=second.id,
        citekey="roe2024second",
        doi="10.1/second",
        title="Second Paper",
        authors=["John Roe"],
        year=2024,
        created_at="2024-01-02T00:00:00Z",
    )
    await save_paper_and_sections(
        conn,
        second.id,
        second_paper,
        [
            SectionData(
                section_order=1,
                heading="Abstract",
                content="Local retrieval shared phrase",
                word_count=4,
            )
        ],
    )
    await conn.close()
    return first, second, paper


@pytest.mark.asyncio
async def test_list_paper_sections_returns_heading_order_and_word_count(library):
    first, _, paper = library

    sections = await list_paper_sections(first.id, paper.citekey)

    assert sections == [
        {"section_order": 1, "heading": "Abstract", "word_count": 4},
        {"section_order": 2, "heading": "Methods", "word_count": 2},
    ]


@pytest.mark.asyncio
async def test_read_paper_section_matches_case_and_whitespace(library):
    first, _, paper = library

    section = await read_paper_section(first.id, paper.citekey, " abstract ")

    assert section["heading"] == "Abstract"
    assert section["content"] == "Local retrieval shared phrase"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("project_id", "citekey", "section_name", "message"),
    [
        ("missing-project", "doe2024first", "Abstract", "not found"),
        ("existing", "missing-citekey", "Abstract", "not found"),
        ("existing", "doe2024first", "Discussion", "not found"),
    ],
)
async def test_missing_project_paper_or_section_returns_tool_error(
    library, project_id, citekey, section_name, message
):
    first, _, _ = library
    if project_id == "existing":
        project_id = first.id

    with pytest.raises(ToolError, match=message):
        await read_paper_section(project_id, citekey, section_name)


@pytest.mark.asyncio
async def test_search_local_library_is_project_scoped(library):
    first, _, _ = library

    results = await search_local_library(first.id, "retrieval shared", limit=10)

    assert len(results) == 1
    assert results[0]["citekey"] == "doe2024first"
    assert results[0]["heading"] == "Abstract"


@pytest.mark.asyncio
async def test_search_local_library_rejects_punctuation_only_query_as_tool_error(library):
    first, _, _ = library

    with pytest.raises(ToolError, match="word"):
        await search_local_library(first.id, '*** " ???', limit=10)


@pytest.mark.asyncio
async def test_search_local_library_caps_snippets_at_300_chars(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspaces"
    db_path = tmp_path / "research_mcp.db"
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(workspace_root))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(db_path))
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    project = await create_project(conn, settings.workspace_dir, "Long Section")
    paper = PaperModel(
        id="paper-long",
        project_id=project.id,
        citekey="roe2024long",
        title="Long Paper",
        authors=[],
        created_at="2024-01-03T00:00:00Z",
    )
    content = "needle " + "ordinary " * 500
    await save_paper_and_sections(
        conn,
        project.id,
        paper,
        [SectionData(section_order=1, heading="Text", content=content, word_count=501)],
    )
    await conn.close()

    results = await search_local_library(project.id, "needle", limit=5)

    assert len(results) == 1
    assert len(results[0]["snippet"]) <= 300
