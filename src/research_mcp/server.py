import re
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.config import configure_logging, get_settings
from research_mcp.db import repository
from research_mcp.db.database import get_connection
from research_mcp.providers.aggregator import search_all
from research_mcp.providers.crossref import get_bibtex, get_by_doi
from research_mcp.providers.semantic_scholar import get_citations, get_references
from research_mcp.providers.unpaywall import get_oa_location

mcp = MCPServer("research-mcp")

_DOI_RE = re.compile(r"^(?:https?://doi\.org/)?10\.\S+$", re.IGNORECASE)
_ARXIV_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
_S2_ID_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_KNOWN_PREFIXES = ("DOI:", "ARXIV:", "PMID:", "CORPUSID:", "MAG:", "ACL:", "DBLP:")


def _normalize_s2_identifier(identifier: str) -> str:
    upper = identifier.upper()
    for prefix in _KNOWN_PREFIXES:
        if upper.startswith(prefix):
            return prefix + identifier[len(prefix) :]
    if _ARXIV_RE.match(identifier):
        return f"ARXIV:{identifier.split('v')[0]}"
    if _S2_ID_RE.match(identifier):
        return identifier
    if _DOI_RE.match(identifier):
        doi = (
            identifier.split("doi.org/", 1)[-1] if "doi.org/" in identifier.lower() else identifier
        )
        return f"DOI:{doi}"
    return f"DOI:{identifier}"  # last-resort fallback, preserves old default behavior


def _truncate_abstract(abstract: str | None) -> str | None:
    if abstract and len(abstract) > 300:
        return abstract[:300] + "..."
    return abstract


@mcp.tool()
async def create_project(name: str, description: str = "") -> dict:
    """Create a research project and its isolated workspace directories."""
    try:
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            project = await repository.create_project(
                conn, settings.workspace_dir, name, description
            )
            return project.model_dump()
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"{type(exc).__name__}: {exc}") from exc


@mcp.tool()
async def list_projects() -> list[dict]:
    """List all projects with their paper and draft section counts."""
    try:
        conn = await get_connection(get_settings().db_path)
        try:
            return await repository.list_projects(conn)
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"{type(exc).__name__}: {exc}") from exc


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
        item["abstract"] = _truncate_abstract(item["abstract"])
        results.append(item)
    return {"results": results, "errors": outcome.errors}


@mcp.tool()
async def get_citation_graph(
    identifier: str,
    direction: Literal["references", "citations"] = "citations",
    limit: int = 15,
) -> list[dict]:
    """Trace a paper's citations or references via Semantic Scholar (identifier is a DOI)."""
    s2_id = _normalize_s2_identifier(identifier)
    fn = get_references if direction == "references" else get_citations
    try:
        results = await fn(s2_id, limit)
    except Exception as exc:
        raise ToolError(f"Could not fetch {direction} for {identifier!r}: {exc}") from exc
    return [r.model_dump() for r in results]


@mcp.tool()
async def resolve_doi(doi: str) -> dict:
    """Resolve a DOI to official Crossref metadata, BibTeX and legal open-access PDF info."""
    try:
        metadata = await get_by_doi(doi)
        if metadata is None:
            raise ToolError(f"Crossref has no record for DOI {doi!r}. Check the DOI for typos.")
        try:
            bibtex = await get_bibtex(doi)
        except Exception:
            bibtex = None
        oa_lookup_error: str | None = None
        try:
            oa = await get_oa_location(doi, get_settings().email)
        except Exception as exc:
            oa = None
            oa_lookup_error = str(exc) or repr(exc)
        response: dict = {
            "doi": metadata.doi,
            "title": metadata.title,
            "authors": metadata.authors,
            "year": metadata.year,
            "venue": metadata.venue,
            "citation_count": metadata.citation_count,
            "bibtex": bibtex,
            # None means "could not check" (see oa_lookup_error); False means checked, closed.
            "is_oa": None if oa_lookup_error else (oa.is_oa if oa else False),
            "pdf_url": oa.pdf_url if oa else None,
            "landing_page_url": oa.landing_page_url if oa else None,
        }
        if oa_lookup_error:
            response["oa_lookup_error"] = oa_lookup_error
        return response
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"{type(exc).__name__}: {exc}") from exc


@mcp.tool()
async def list_paper_sections(project_id: str, citekey: str) -> list[dict]:
    """List a paper's extracted section headings and word counts."""
    try:
        conn = await get_connection(get_settings().db_path)
        try:
            return await repository.list_sections(conn, project_id, citekey)
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not list sections for citekey {citekey!r}: {type(exc).__name__}: {exc}"
        ) from exc


@mcp.tool()
async def read_paper_section(
    project_id: str,
    citekey: str,
    section_name: str,
) -> dict:
    """Read only the requested section from a paper stored in this project."""
    try:
        conn = await get_connection(get_settings().db_path)
        try:
            return await repository.get_section(conn, project_id, citekey, section_name)
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not read section {section_name!r} for citekey {citekey!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


@mcp.tool()
async def search_local_library(
    project_id: str,
    query: str,
    limit: int = 10,
) -> list[dict]:
    """Search the project's local PDF sections and return brief snippets."""
    try:
        conn = await get_connection(get_settings().db_path)
        try:
            return await repository.search_sections(conn, project_id, query, limit)
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Could not search the local library: {type(exc).__name__}: {exc}") from exc


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    mcp.run()


if __name__ == "__main__":
    main()
