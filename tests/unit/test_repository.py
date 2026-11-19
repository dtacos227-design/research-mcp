import pytest
import pytest_asyncio

from research_mcp.db.database import get_connection
from research_mcp.db.repository import (
    ProjectNotFoundError,
    create_project,
    get_project,
    list_projects,
)


@pytest_asyncio.fixture
async def conn(tmp_path):
    connection = await get_connection(tmp_path / "research_mcp.db")
    yield connection
    await connection.close()


@pytest.mark.asyncio
async def test_create_project_creates_workspace_subdirs(tmp_path, conn):
    project = await create_project(conn, tmp_path / "workspaces", "My Research")
    assert project.slug == "my-research"
    workspace = tmp_path / "workspaces" / "my-research"
    for sub in ("pdfs", "parsed", "drafts", "assets", "scripts", "exports"):
        assert (workspace / sub).is_dir()


@pytest.mark.asyncio
async def test_create_project_with_colliding_name_gets_distinct_slug(tmp_path, conn):
    first = await create_project(conn, tmp_path / "workspaces", "Vision Study")
    second = await create_project(conn, tmp_path / "workspaces", "Vision Study")
    assert first.slug != second.slug
    assert second.slug.startswith("vision-study")
    assert (tmp_path / "workspaces" / second.slug / "pdfs").is_dir()


@pytest.mark.asyncio
async def test_get_project_raises_not_found_for_unknown_id(conn):
    with pytest.raises(ProjectNotFoundError):
        await get_project(conn, "does-not-exist")


@pytest.mark.asyncio
async def test_get_project_returns_created_project(tmp_path, conn):
    created = await create_project(conn, tmp_path / "workspaces", "Round Trip", "desc")
    assert await get_project(conn, created.id) == created


@pytest.mark.asyncio
async def test_list_projects_reports_zero_counts_for_fresh_project(tmp_path, conn):
    await create_project(conn, tmp_path / "workspaces", "Fresh Project")
    projects = await list_projects(conn)
    assert len(projects) == 1
    assert projects[0]["paper_count"] == 0
    assert projects[0]["draft_count"] == 0


@pytest.mark.asyncio
async def test_list_projects_counts_papers_and_drafts_without_cross_product(tmp_path, conn):
    project = await create_project(conn, tmp_path / "workspaces", "Counted")
    for i in range(2):
        await conn.execute(
            "INSERT INTO papers (id, project_id, citekey, title, authors_json, created_at)"
            " VALUES (?, ?, ?, 't', '[]', 'now')",
            (f"p{i}", project.id, f"key{i}"),
        )
    for i in range(3):
        await conn.execute(
            "INSERT INTO draft_sections (project_id, filename, section_order, updated_at)"
            " VALUES (?, ?, ?, 'now')",
            (project.id, f"{i}.md", i),
        )
    await conn.commit()
    projects = await list_projects(conn)
    assert projects[0]["paper_count"] == 2
    assert projects[0]["draft_count"] == 3
