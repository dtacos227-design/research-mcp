import json
import re
from pathlib import Path
from typing import Literal
from uuid import uuid4

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.citations.bibtex import build_bibtex_entry, build_csl_entry
from research_mcp.citations.verifier import diff_citations, extract_citekeys
from research_mcp.config import configure_logging, get_settings
from research_mcp.db import repository
from research_mcp.db.database import get_connection
from research_mcp.exporters import latex_engine, wordcraft_bridge
from research_mcp.exporters.docx_polisher import polish_docx
from research_mcp.exporters.pandoc_engine import (
    detect_pandoc,
    merge_draft_markdown,
    render_docx,
    render_markdown,
)
from research_mcp.exporters.templates import (
    bundled_template,
    resolve_csl,
    resolve_docx_template,
    resolve_latex_template,
)
from research_mcp.parsers.ingestion import (
    _safe_destination,
    ingest_local_pdf,
    ingest_remote_paper,
)
from research_mcp.providers.aggregator import search_all
from research_mcp.providers.crossref import get_bibtex, get_by_doi
from research_mcp.providers.semantic_scholar import get_citations, get_references
from research_mcp.providers.unpaywall import get_oa_location
from research_mcp.writers import draft_manager
from research_mcp.writers.chart_runner import run_chart

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


@mcp.tool()
async def save_paper_to_project(
    project_id: str,
    identifier: str,
    download_pdf: bool = True,
) -> dict:
    """Save paper metadata and optionally download and parse a verified OA PDF."""
    try:
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            await repository.get_project(conn, project_id)
            return await ingest_remote_paper(
                conn,
                project_id,
                settings.workspace_dir,
                identifier,
                download_pdf,
            )
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not save paper {identifier!r} to project {project_id!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


@mcp.tool()
async def import_local_pdf(
    project_id: str,
    file_path: str,
    doi: str | None = None,
) -> dict:
    """Import a user-owned local PDF and index its extracted sections."""
    try:
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            await repository.get_project(conn, project_id)
            return await ingest_local_pdf(
                conn,
                project_id,
                settings.workspace_dir,
                Path(file_path).expanduser(),
                doi,
            )
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not import local PDF {file_path!r} into project {project_id!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


@mcp.tool()
async def save_draft_section(
    project_id: str,
    filename: str,
    content: str,
    order: int | None = None,
) -> dict:
    """Save or update a draft Markdown chapter (e.g. 01_abstract.md) in this project."""
    try:
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            project = await repository.get_project(conn, project_id)
            return await draft_manager.write_draft_section(
                conn, project_id, _project_dir(settings, project), filename, content, order
            )
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not save draft section {filename!r}: {type(exc).__name__}: {exc}"
        ) from exc


@mcp.tool()
async def read_draft_section(project_id: str, filename: str) -> dict:
    """Read a draft chapter's full Markdown content from this project."""
    try:
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            project = await repository.get_project(conn, project_id)
            return await draft_manager.read_draft_section(
                conn, project_id, _project_dir(settings, project), filename
            )
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not read draft section {filename!r}: {type(exc).__name__}: {exc}"
        ) from exc


@mcp.tool()
async def generate_chart(
    project_id: str,
    filename: str,
    python_code: str,
    dpi: int = 300,
) -> dict:
    """Run matplotlib/seaborn visualization code and save the figure(s) to assets/."""
    try:
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            project = await repository.get_project(conn, project_id)
        finally:
            await conn.close()
        assets = run_chart(_project_dir(settings, project), filename, python_code, dpi)
        return {"assets": assets, "dpi": dpi}
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not generate chart {filename!r}: {type(exc).__name__}: {exc}"
        ) from exc


_VALID_BIB_FORMATS = {"bibtex", "csl_json"}


def _project_dir(settings, project: object) -> Path:
    return settings.workspace_dir / project.slug


def _write_file(path: Path, project_dir: Path, text: str) -> None:
    safe = _safe_destination(path, project_dir)
    safe.parent.mkdir(parents=True, exist_ok=True)
    temporary = safe.with_name(f".{safe.name}.{uuid4().hex}.part")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(safe)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


async def _citation_gate(
    conn,
    project_id: str,
    markdown: str,
    strict: bool,
) -> list[str]:
    """Return citekeys cited in `markdown` but absent from the project library.

    In strict mode a non-empty result halts the export before any Pandoc or
    Tectonic call runs.
    """
    referenced = extract_citekeys(markdown)
    papers = await repository.list_papers(conn, project_id)
    missing, _found, _uncited = diff_citations(referenced, {paper.citekey for paper in papers})
    if strict and missing:
        raise ToolError(
            "Citation gate failed: the draft cites citekey(s) that are not in this "
            f"project's library: {', '.join(missing)}. Save the papers first, fix the "
            "citekeys, or export with strict_citations=False."
        )
    return missing


async def _refresh_bibliography(conn, project_id: str, project_dir: Path) -> tuple[Path, Path]:
    """Rewrite references.bib and references.csl.json from the project's papers."""
    papers = await repository.list_papers(conn, project_id)
    entries = [build_bibtex_entry(paper) for paper in papers]
    bib_path = _safe_destination(project_dir / "references.bib", project_dir)
    _write_file(bib_path, project_dir, "\n\n".join(entries) + "\n")

    csl_entries = [build_csl_entry(paper) for paper in papers]
    csl_path = _safe_destination(project_dir / "references.csl.json", project_dir)
    _write_file(csl_path, project_dir, json.dumps(csl_entries, ensure_ascii=False, indent=2))
    return bib_path, csl_path


def _export_target(project_dir: Path, output_name: str, extension: str) -> Path:
    name = output_name.strip()
    if extension == ".tex":
        name = name.removesuffix(".tex").removesuffix(".pdf")
    if not name or name in {".", ".."} or "/" in name or "\\" in name or ".." in name:
        raise ToolError(
            f"output_name {output_name!r} must be a plain file name without directories."
        )
    if not name.lower().endswith(extension):
        name = f"{name}{extension}"
    return _safe_destination(project_dir / "exports" / name, project_dir)


def _draft_markdown_or_raise(markdown: str) -> str:
    if not markdown.strip():
        raise ToolError(
            "This project has no draft chapters to export. Write one with save_draft_section first."
        )
    return markdown


def _warnings_for_missing(missing: list[str]) -> list[str]:
    if not missing:
        return []
    return [f"Undefined citekey(s) left unresolved by Citeproc: {', '.join(missing)}."]


@mcp.tool()
async def generate_bibliography(
    project_id: str,
    formats: list[str] = ["bibtex", "csl_json"],  # noqa: B006
) -> dict:
    """Rebuild the project's references.bib and/or references.csl.json from saved papers."""
    try:
        invalid = [f for f in formats if f not in _VALID_BIB_FORMATS]
        if invalid:
            raise ToolError(
                f"Unknown bibliography format(s) {invalid!r}; "
                f"choose from {sorted(_VALID_BIB_FORMATS)!r}."
            )
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            try:
                project = await repository.get_project(conn, project_id)
            except repository.ProjectNotFoundError as exc:
                raise ToolError(str(exc)) from exc
            papers = await repository.list_papers(conn, project_id)
            project_dir = _project_dir(settings, project)

            bib_path: str | None = None
            csl_json_path: str | None = None

            if "bibtex" in formats:
                entries = [build_bibtex_entry(p) for p in papers]
                target = _safe_destination(project_dir / "references.bib", project_dir)
                _write_file(target, project_dir, "\n\n".join(entries) + "\n")
                bib_path = str(target)

            if "csl_json" in formats:
                csl_entries = [build_csl_entry(p) for p in papers]
                target = _safe_destination(project_dir / "references.csl.json", project_dir)
                _write_file(
                    target, project_dir, json.dumps(csl_entries, ensure_ascii=False, indent=2)
                )
                csl_json_path = str(target)
                for paper, entry in zip(papers, csl_entries, strict=True):
                    await repository.set_paper_csl_json(
                        conn, paper.id, json.dumps(entry, ensure_ascii=False)
                    )
                await conn.commit()

            return {
                "bib_path": bib_path,
                "csl_json_path": csl_json_path,
                "paper_count": len(papers),
            }
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not generate bibliography for project {project_id!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


@mcp.tool()
async def verify_citations(project_id: str, markdown_text: str | None = None) -> dict:
    """Audit [@citekey] references against the project's library; flag missing and uncited."""
    try:
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            try:
                project = await repository.get_project(conn, project_id)
            except repository.ProjectNotFoundError as exc:
                raise ToolError(str(exc)) from exc

            if markdown_text is None:
                drafts_dir = _project_dir(settings, project) / "drafts"
                markdown_text = "\n".join(
                    path.read_text(encoding="utf-8") for path in sorted(drafts_dir.glob("*.md"))
                )
                source = "drafts_directory"
            else:
                source = "markdown_text"

            referenced = extract_citekeys(markdown_text)
            papers = await repository.list_papers(conn, project_id)
            library = {paper.citekey for paper in papers}
            missing, found, unreferenced = diff_citations(referenced, library)

            uncited = [
                {"citekey": paper.citekey, "title": paper.title}
                for paper in papers
                if paper.citekey in unreferenced
            ]
            return {
                "cited_but_missing": missing,
                "uncited_papers": uncited,
                "cited_and_found": found,
                "source": source,
            }
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not verify citations for project {project_id!r}: {type(exc).__name__}: {exc}"
        ) from exc


@mcp.tool()
async def export_to_docx(
    project_id: str,
    output_name: str,
    csl_style: str = "apa",
    template_name: str = "default_academic",
    polish: bool = True,
    strict_citations: bool = True,
) -> dict:
    """Export every draft chapter to one `.docx` via Pandoc + Citeproc.

    Regenerates references.bib/references.csl.json from the project library,
    merges the draft chapters in section order, verifies every `[@citekey]`
    against that library, then converts. Writes to `exports/<output_name>.docx`.
    """
    try:
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            try:
                project = await repository.get_project(conn, project_id)
            except repository.ProjectNotFoundError as exc:
                raise ToolError(str(exc)) from exc
            project_dir = _project_dir(settings, project)
            output = _export_target(project_dir, output_name, ".docx")
            csl_name = resolve_csl(csl_style)
            docx_name = resolve_docx_template(template_name)

            sections = await repository.list_draft_sections(conn, project_id)
            markdown = _draft_markdown_or_raise(merge_draft_markdown(project_dir, sections))
            missing = await _citation_gate(conn, project_id, markdown, strict_citations)
            bib_path, _csl_json = await _refresh_bibliography(conn, project_id, project_dir)

            with (
                bundled_template(f"csl/{csl_name}.csl") as csl_path,
                bundled_template(f"docx/{docx_name}.docx") as reference_doc,
            ):
                render_docx(
                    markdown,
                    bib_path,
                    csl_path,
                    reference_doc,
                    output,
                    resource_path=project_dir,
                )
            if polish:
                polish_docx(output, project.name)

            return {
                "output_path": str(output),
                "format": "docx",
                "csl_style": csl_style,
                "template_name": template_name,
                "cited_but_missing": missing,
                "warnings": _warnings_for_missing(missing),
            }
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not export project {project_id!r} to DOCX: {type(exc).__name__}: {exc}"
        ) from exc


@mcp.tool()
async def export_to_latex(
    project_id: str,
    output_name: str,
    template_name: str = "ieee",
    compile_pdf: bool = True,
    strict_citations: bool = True,
) -> dict:
    """Export every draft chapter to a standalone `.tex`, optionally compiling a PDF.

    Writes `exports/<output_name>.tex` and, when `compile_pdf` is true, compiles
    it with Tectonic into `exports/<output_name>.pdf`.
    """
    try:
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            try:
                project = await repository.get_project(conn, project_id)
            except repository.ProjectNotFoundError as exc:
                raise ToolError(str(exc)) from exc
            project_dir = _project_dir(settings, project)
            tex_path = _export_target(project_dir, output_name, ".tex")
            latex_name = resolve_latex_template(template_name)
            csl_name = "ieee" if latex_name == "ieee" else "apa"

            sections = await repository.list_draft_sections(conn, project_id)
            markdown = _draft_markdown_or_raise(merge_draft_markdown(project_dir, sections))
            missing = await _citation_gate(conn, project_id, markdown, strict_citations)
            bib_path, _csl_json = await _refresh_bibliography(conn, project_id, project_dir)

            with (
                bundled_template(f"csl/{csl_name}.csl") as csl_path,
                bundled_template(f"latex/{latex_name}.tex") as template_path,
            ):
                latex_engine.render_latex(
                    markdown,
                    template_path,
                    bib_path,
                    csl_path,
                    tex_path,
                    resource_path=project_dir,
                )

            pdf_path: Path | None = None
            if compile_pdf:
                pdf_path = latex_engine.compile_pdf(tex_path, tex_path.parent)

            return {
                "output_path": str(pdf_path or tex_path),
                "format": "latex",
                "tex_path": str(tex_path),
                "pdf_path": str(pdf_path) if pdf_path else None,
                "template_name": template_name,
                "csl_style": csl_name,
                "cited_but_missing": missing,
                "warnings": _warnings_for_missing(missing),
            }
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not export project {project_id!r} to LaTeX: {type(exc).__name__}: {exc}"
        ) from exc


@mcp.tool()
async def export_to_markdown_bundle(
    project_id: str,
    output_name: str,
    csl_style: str = "apa",
    strict_citations: bool = True,
) -> dict:
    """Merge all draft chapters into one Citeproc-rendered Markdown file.

    In-text `[@citekey]` markers are formatted and a reference list is appended;
    the result is written to `exports/<output_name>.md`.
    """
    try:
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            try:
                project = await repository.get_project(conn, project_id)
            except repository.ProjectNotFoundError as exc:
                raise ToolError(str(exc)) from exc
            project_dir = _project_dir(settings, project)
            output = _export_target(project_dir, output_name, ".md")
            csl_name = resolve_csl(csl_style)

            sections = await repository.list_draft_sections(conn, project_id)
            markdown = _draft_markdown_or_raise(merge_draft_markdown(project_dir, sections))
            missing = await _citation_gate(conn, project_id, markdown, strict_citations)
            bib_path, _csl_json = await _refresh_bibliography(conn, project_id, project_dir)

            with bundled_template(f"csl/{csl_name}.csl") as csl_path:
                render_markdown(markdown, bib_path, csl_path, output, resource_path=project_dir)

            return {
                "output_path": str(output),
                "format": "markdown",
                "csl_style": csl_style,
                "cited_but_missing": missing,
                "warnings": _warnings_for_missing(missing),
            }
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not export project {project_id!r} to Markdown: {type(exc).__name__}: {exc}"
        ) from exc


@mcp.tool()
async def check_wordcraft_env(project_id: str | None = None) -> dict:
    """Report the local WordCraft install, live desktop and confinement roots."""
    try:
        settings = get_settings()
        workspace_root = settings.workspace_dir
        workspace_root.mkdir(parents=True, exist_ok=True)
        cli = wordcraft_bridge.detect_wordcraft_cli()
        desktop = wordcraft_bridge.detect_wordcraft_desktop()
        key_path = wordcraft_bridge.control_key_path()
        port_open = wordcraft_bridge.probe_port()
        key_present = bool(key_path and key_path.is_file() and key_path.stat().st_size > 0)
        report = {
            "wordcraft_cli": {
                "found": cli is not None,
                "path": str(cli) if cli is not None else None,
                "version": wordcraft_bridge.cli_version(cli) if cli is not None else None,
            },
            "desktop_app": {
                "found": desktop is not None,
                "path": str(desktop) if desktop is not None else None,
            },
            "live_desktop": {
                "port": wordcraft_bridge.DEFAULT_PORT,
                "port_open": port_open,
                "control_key_path": str(key_path) if key_path is not None else None,
                "control_key_present": key_present,
                "ready": port_open and key_present,
            },
            "workspace_root": str(workspace_root),
            "confinement": {
                "read_root": str(workspace_root),
                "write_root": str(workspace_root),
                "mcp_command": wordcraft_bridge.build_mcp_command(cli, workspace_root),
            },
            "fallback": {
                "pandoc": _binary_path(detect_pandoc),
                "tectonic": _binary_path(latex_engine.detect_tectonic),
            },
        }
        report["recommendations"] = _wordcraft_recommendations(report)
        if project_id is not None:
            conn = await get_connection(settings.db_path)
            try:
                try:
                    project = await repository.get_project(conn, project_id)
                except repository.ProjectNotFoundError as exc:
                    raise ToolError(str(exc)) from exc
                chapters = await repository.list_draft_sections(conn, project_id)
                papers = await repository.list_papers(conn, project_id)
            finally:
                await conn.close()
            report["project"] = {
                "id": project.id,
                "slug": project.slug,
                "workspace_dir": str(workspace_root / project.slug),
                "draft_chapters": [chapter["filename"] for chapter in chapters],
                "papers": len(papers),
            }
        return report
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not inspect the WordCraft environment: {type(exc).__name__}: {exc}"
        ) from exc


def _binary_path(detect) -> str | None:
    try:
        return detect()
    except ToolError:
        return None


def _wordcraft_recommendations(report: dict) -> list[str]:
    items: list[str] = []
    live = report["live_desktop"]
    cli = report["wordcraft_cli"]
    roots = report["confinement"]
    if live["ready"]:
        items.append(
            f"WordCraft is running on 127.0.0.1:{live['port']}. Start the bridge with "
            f"`--connect 127.0.0.1:{live['port']}`; `--connect` cannot be combined with the "
            "automation roots, so the app itself is not confined."
        )
    if cli["found"]:
        items.append(
            "Use the headless WordCraft MCP server with both roots: "
            + " ".join(roots["mcp_command"])
        )
    if not cli["found"]:
        if report["desktop_app"]["found"]:
            items.append(
                "The WordCraft desktop app is installed but `wordcraft-cli` is missing; the "
                "CLI ships as a separate download from the WordCraft releases."
            )
        items.append(
            "Without WordCraft, fall back to export_to_docx or "
            "prepare_wordcraft_handoff(mode='seed_docx')."
        )
    if report["fallback"]["pandoc"] is None:
        items.append(
            "Pandoc is missing, so the seed DOCX and the writing blueprint cannot be built: "
            "install it with `brew install pandoc`."
        )
    return items


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    mcp.run()


if __name__ == "__main__":
    main()
