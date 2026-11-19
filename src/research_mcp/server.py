from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.config import configure_logging, get_settings
from research_mcp.db import repository
from research_mcp.db.database import get_connection
from research_mcp.providers.aggregator import search_all
from research_mcp.providers.semantic_scholar import get_citations, get_references

mcp = MCPServer("research-mcp")


@mcp.tool()
async def create_project(name: str, description: str = "") -> dict:
    """Create a research project and its isolated workspace directories."""
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        project = await repository.create_project(conn, settings.workspace_dir, name, description)
        return project.model_dump()
    finally:
        await conn.close()


@mcp.tool()
async def list_projects() -> list[dict]:
    """List all projects with their paper and draft section counts."""
    conn = await get_connection(get_settings().db_path)
    try:
        return await repository.list_projects(conn)
    finally:
        await conn.close()


@mcp.tool()
async def get_project_status(project_id: str) -> dict:
    """Get full project status: workspace path, citekeys, drafts, charts and exports."""
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        try:
            project = await repository.get_project(conn, project_id)
        except repository.ProjectNotFoundError as exc:
            raise ToolError(str(exc)) from exc
        workspace = settings.workspace_dir / project.slug
        async with conn.execute(
            "SELECT citekey FROM papers WHERE project_id = ?", (project_id,)
        ) as cursor:
            citekeys = [row[0] for row in await cursor.fetchall()]
        return {
            **project.model_dump(),
            "workspace_path": str(workspace),
            "citekeys": citekeys,
            "draft_filenames": sorted(p.name for p in (workspace / "drafts").glob("*")),
            "chart_filenames": sorted(p.name for p in (workspace / "assets").glob("*")),
            "export_filenames": sorted(p.name for p in (workspace / "exports").glob("*")),
        }
    finally:
        await conn.close()


@mcp.tool()
async def search_papers(
    query: str,
    sources: list[str] = ["openalex", "semantic_scholar", "arxiv"],  # noqa: B006
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 10,
) -> dict:
    """Search multiple literature sources in parallel, deduplicated by DOI/title."""
    outcome = await search_all(query, sources, year_from, year_to, limit)
    results = []
    for r in outcome.results:
        item = r.model_dump()
        if item["abstract"] and len(item["abstract"]) > 300:
            item["abstract"] = item["abstract"][:300] + "..."
        results.append(item)
    return {"results": results, "errors": outcome.errors}


@mcp.tool()
async def get_citation_graph(
    identifier: str,
    direction: Literal["references", "citations"] = "citations",
    limit: int = 15,
) -> list[dict]:
    """Trace a paper's citations or references via Semantic Scholar (identifier is a DOI)."""
    s2_id = identifier if identifier.upper().startswith(("DOI:", "ARXIV:")) else f"DOI:{identifier}"
    fn = get_references if direction == "references" else get_citations
    try:
        results = await fn(s2_id, limit)
    except Exception as exc:
        raise ToolError(f"Could not fetch {direction} for {identifier!r}: {exc}") from exc
    return [r.model_dump() for r in results]


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    mcp.run()


if __name__ == "__main__":
    main()
