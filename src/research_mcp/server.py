import asyncio
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
from research_mcp.exporters.formatting import (
    apply_docx_formatting,
    latex_metadata,
    normalize_chapter_markdown,
)
from research_mcp.exporters.pandoc_engine import (
    _read_draft,
    detect_pandoc,
    render_docx,
    render_markdown,
)
from research_mcp.exporters.templates import (
    bundled_template,
    resolve_csl,
    resolve_docx_template,
    resolve_latex_template,
)
from research_mcp.models import BriefLanguage, ResearchBrief
from research_mcp.parsers.ingestion import (
    _safe_destination,
    ingest_local_pdf,
    ingest_remote_paper,
)
from research_mcp.providers.aggregator import search_all
from research_mcp.providers.crossref import get_bibtex, get_by_doi
from research_mcp.providers.semantic_scholar import get_citations, get_references
from research_mcp.providers.unpaywall import get_oa_location
from research_mcp.styles.models import SectionSpec, StyleSpec
from research_mcp.styles.registry import get_style, list_styles
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


DEFAULT_RESEARCH_STYLE = "article_imrad"

_ASK_USER_TEMPLATES: dict[str, str] = {
    "style_id": (
        "Gaya riset apa? Pilih salah satu id gaya yang tersedia. "
        "/ Which research style should we use? Pick one of the available ids."
    ),
    "title": "Judul riset? / What is the research title?",
    "research_question": (
        "Pertanyaan atau tujuan riset? / What is the research question or objective?"
    ),
    "citation": (
        "Gaya sitasi? (default dari gaya: {citation}) / Citation style? (style default: {citation})"
    ),
}


def _clean_optional(value: str | None) -> str | None:
    """Strip an answer; None and whitespace-only both mean "not answered"."""
    if value is None:
        return None
    return value.strip() or None


def _ask_user_prompts(missing: list[str], style: StyleSpec) -> list[str]:
    """One bilingual question per missing brief field, in the missing order."""
    options = ", ".join(item["id"] for item in list_styles())
    prompts: list[str] = []
    for field in missing:
        template = _ASK_USER_TEMPLATES.get(field, f"{field}? / {field}?")
        prompt = template.format(citation=style.citation)
        if field == "style_id":
            prompt = f"{prompt} Options: {options}"
        prompts.append(prompt)
    return prompts


@mcp.tool()
async def save_research_brief(
    project_id: str,
    style_id: str,
    title: str,
    research_question: str,
    citation: str | None = None,
    language: BriefLanguage = "id",
    scope: str = "",
    target_length: str = "",
    deadline: str = "",
) -> dict:
    """Save the intake answers for a project and report what is still missing.

    Nothing is auto-filled: `citation` is required, and omitting it leaves the
    brief incomplete so `start_research` can ask the user to confirm the
    style's default. Blank or whitespace-only answers count as unanswered.
    """
    try:
        resolved_style_id = (style_id or "").strip()
        # Validate before persisting so an unknown style raises the registry's ToolError.
        get_style(resolved_style_id)
        brief = ResearchBrief(
            style_id=resolved_style_id,
            title=_clean_optional(title),
            research_question=_clean_optional(research_question),
            citation=_clean_optional(citation),
            language=language,
            scope=_clean_optional(scope) or "",
            target_length=_clean_optional(target_length) or "",
            deadline=_clean_optional(deadline) or "",
        )
        conn = await get_connection(get_settings().db_path)
        try:
            await _get_project_or_raise(conn, project_id)
            await repository.set_project_brief(
                conn, project_id, brief.model_dump_json(), resolved_style_id
            )
        finally:
            await conn.close()
        missing = brief.missing()
        return {
            "status": "complete" if not missing else "needs_input",
            "complete": not missing,
            "missing": missing,
            "brief": brief.model_dump(),
        }
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not save the research brief for project {project_id!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


@mcp.tool()
async def get_research_brief(project_id: str) -> dict:
    """Return the saved research brief and which required answers are missing."""
    try:
        conn = await get_connection(get_settings().db_path)
        try:
            await _get_project_or_raise(conn, project_id)
            stored = await repository.get_project_brief(conn, project_id)
        finally:
            await conn.close()
        brief = ResearchBrief.model_validate(stored or {})
        return {
            "complete": brief.complete(),
            "missing": brief.missing(),
            "brief": brief.model_dump(),
        }
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not read the research brief for project {project_id!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


@mcp.tool()
async def list_research_styles() -> dict:
    """List every available research style with its outline sections.

    Styles are bundled with the package and can be overridden or extended with
    YAML files in `~/.research-mcp/styles`; `id` is what `save_research_brief`
    expects for `style_id`.
    """
    try:
        return {"styles": list_styles()}
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Could not list the research styles: {type(exc).__name__}: {exc}") from exc


@mcp.tool()
async def get_research_style(style_id: str) -> dict:
    """Return one style's full specification: outline, citation and formatting."""
    try:
        return {"style": get_style(style_id).model_dump()}
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not read the research style {style_id!r}: {type(exc).__name__}: {exc}"
        ) from exc


@mcp.tool()
async def start_research(project_id: str, overwrite: bool = False) -> dict:
    """Check the intake gate and start the research once the brief is complete.

    An incomplete brief returns `needs_input` with one bilingual question per
    missing field and creates no files; answer them with `save_research_brief`
    and call this again.

    Once the brief is complete, every section of the chosen style outline gets a
    `drafts/<filename>` skeleton holding its heading and guidance comment, plus a
    `draft_sections` row. Existing non-empty files are left untouched and reported
    as `conflicts` unless `overwrite=True`.
    """
    try:
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            project = await _get_project_or_raise(conn, project_id)
            stored = await repository.get_project_brief(conn, project_id)
            brief = ResearchBrief.model_validate(stored or {})
            missing = brief.missing()
            style = await _resolve_style_for_export(conn, project_id, brief.style_id)
            if missing:
                return {
                    "status": "needs_input",
                    "missing": missing,
                    "ask_user": _ask_user_prompts(missing, style),
                    "defaults": {"citation": style.citation, "language": brief.language},
                }
            return await _create_style_skeleton(
                conn, project_id, _project_dir(settings, project), style, overwrite
            )
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not start research for project {project_id!r}: {type(exc).__name__}: {exc}"
        ) from exc


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


async def _get_project_or_raise(conn, project_id: str):
    try:
        return await repository.get_project(conn, project_id)
    except repository.ProjectNotFoundError as exc:
        raise ToolError(str(exc)) from exc


async def _resolve_style_for_export(conn, project_id: str, style: str | None) -> StyleSpec:
    """Resolve a style: explicit parameter, else the project's style, else the default."""
    project = await _get_project_or_raise(conn, project_id)
    requested = (style or "").strip() or project.style_id or DEFAULT_RESEARCH_STYLE
    return get_style(requested)


async def _precondition_failed(conn, project_id: str, style: StyleSpec) -> dict | None:
    """Return the failed export preconditions, or `None` when the project is ready.

    The gate enforces the intake contract before any file is written: a complete
    research brief and at least one saved paper. Style resolution happens before
    this helper, so an unknown style already failed with its own `ToolError`.
    Citation verification intentionally stays in `_citation_gate`, which honors
    `strict_citations` during rendering (R11).
    """
    stored = await repository.get_project_brief(conn, project_id)
    brief = ResearchBrief.model_validate(stored or {})
    missing = brief.missing()

    failed: list[str] = []
    remediation: list[str] = []
    if missing:
        failed.append(f"brief: missing required fields {missing}")
        remediation.append(
            "Answer the research brief with save_research_brief(project_id="
            f"{project_id!r}, style_id={style.id!r}, title=..., research_question=..., "
            "citation=...) and export again."
        )
    if not await repository.list_papers(conn, project_id):
        failed.append("papers: no paper saved")
        remediation.append(
            "Save at least one paper with save_paper_to_project or import_local_pdf so "
            "the bibliography and citation gate have a source."
        )
    if not failed:
        return None
    return {
        "status": "precondition_failed",
        "failed": failed,
        "remediation": remediation,
    }


def _is_bibliography_section(section: SectionSpec) -> bool:
    """True for outline sections that name the reference list.

    Pandoc Citeproc already appends `# References` via
    `--metadata reference-section-title=References`, so merging the style's own
    reference chapter would leave a second, empty heading behind.
    """
    headings = {heading.casefold() for heading in wordcraft_bridge.BIBLIOGRAPHY_HEADINGS}
    return section.title.strip().casefold() in headings or section.id.strip().casefold() in headings


def _style_merge_markdown(
    project_dir: Path, sections: list[dict], style: StyleSpec
) -> tuple[str, list[str]]:
    """Merge drafts for a style, returning the markdown and unmatched filenames.

    R10: chapters named by the style outline come first in `order` with their
    leading heading normalized to the outline title; every other `drafts/*.md`
    is appended afterwards in filename order, unchanged, and reported as an
    extra so the user learns it is outside the chosen structure. Outline
    sections that name the reference list are skipped: Citeproc owns that list.
    """
    drafts_dir = project_dir / "drafts"
    pieces: list[str] = []
    outline_names: set[str] = set()

    for section in sorted(style.outline, key=lambda item: item.order):
        outline_names.add(section.filename)
        if _is_bibliography_section(section):
            continue
        text = _read_draft(drafts_dir / section.filename)
        if text:
            pieces.append(normalize_chapter_markdown(text, section))

    extra_files: list[str] = []
    if drafts_dir.is_dir():
        for path in sorted(drafts_dir.glob("*.md")):
            if path.name in outline_names:
                continue
            text = _read_draft(path)
            if text:
                extra_files.append(path.name)
                pieces.append(text)

    return "\n\n".join(pieces), extra_files


def _skeleton_markdown(section: SectionSpec) -> str:
    return f"# {section.title}\n\n<!-- guidance: {section.guidance} -->\n"


async def _create_style_skeleton(
    conn,
    project_id: str,
    project_dir: Path,
    style: StyleSpec,
    overwrite: bool,
) -> dict:
    """Write one `drafts/<filename>` skeleton per outline section, in style order.

    Nothing is written when an existing non-empty file would be clobbered and
    `overwrite` is false: the caller gets `exists` plus the conflicting names so
    the user can decide.
    """
    sections = sorted(style.outline, key=lambda section: section.order)
    names = [section.filename for section in sections]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ToolError(
            f"Style {style.id!r} declares duplicate outline filename(s): "
            f"{', '.join(duplicates)}; one chapter file cannot hold two sections."
        )
    targets = [
        (section, _safe_destination(project_dir / "drafts" / section.filename, project_dir))
        for section in sections
    ]
    conflicts = [
        section.filename
        for section, target in targets
        if target.is_file() and target.stat().st_size > 0
    ]
    if conflicts and not overwrite:
        return {"status": "exists", "conflicts": conflicts}

    created_files: list[str] = []
    for section, target in targets:
        content = _skeleton_markdown(section)
        _write_file(target, project_dir, content)
        await repository.upsert_draft_section(
            conn,
            project_id,
            section.filename,
            section.order,
            section.title,
            len(content.split()),
        )
        created_files.append(section.filename)

    return {
        "status": "created",
        "created_files": created_files,
        "guidance": style.guidance,
        "style_id": style.id,
    }


async def _render_docx_export(
    conn,
    project,
    project_dir: Path,
    output_name: str,
    csl_style: str,
    template_name: str,
    polish: bool,
    strict_citations: bool,
    style: StyleSpec,
) -> tuple[Path, list[str], list[str]]:
    """Render the merged draft to `exports/<output_name>.docx` behind the citation gate."""
    output = _export_target(project_dir, output_name, ".docx")
    csl_name = resolve_csl(csl_style)
    docx_name = resolve_docx_template(template_name)

    sections = await repository.list_draft_sections(conn, project.id)
    markdown, extra_files = _style_merge_markdown(project_dir, sections, style)
    markdown = _draft_markdown_or_raise(markdown)
    missing = await _citation_gate(conn, project.id, markdown, strict_citations)
    bib_path, _csl_json = await _refresh_bibliography(conn, project.id, project_dir)

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
    apply_docx_formatting(output, style.formatting)
    if polish:
        polish_docx(output, project.name)
    return output, missing, extra_files


def _warnings_for_missing(missing: list[str]) -> list[str]:
    if not missing:
        return []
    return [f"Undefined citekey(s) left unresolved by Citeproc: {', '.join(missing)}."]


def _warnings_for_extra_drafts(extra_files: list[str]) -> list[str]:
    if not extra_files:
        return []
    return [f"Exported draft(s) not in the style outline: {', '.join(extra_files)}"]


_UNSUPPORTED_HEADING_NUMBERING = frozenset({"roman_bab", "numeric_bab"})


def _warnings_for_heading_numbering(style: StyleSpec) -> list[str]:
    """Report that heading numbers come from the draft text, not from the engine.

    Only ``roman_bab``/``numeric_bab`` ask for automatic numbering; ``none`` and
    ``arabic`` styles keep whatever heading text the draft already holds, so they
    must not raise a false alarm. Modes without a blueprint (seed DOCX and job
    script) get this caveat here; `live_authoring` already receives the
    equivalent note from `build_blueprint` via `_style_warnings`, so callers must
    not add a second copy.
    """
    if style.formatting.heading_numbering not in _UNSUPPORTED_HEADING_NUMBERING:
        return []
    return [
        "Heading numbering (BAB I / 1.1) is taken from the draft text; the export "
        "engine does not renumber headings. Ensure each chapter's heading is written "
        "as the style requires."
    ]


def _warnings_for_two_column(style: StyleSpec, latex_name: str) -> list[str]:
    """Report that the bundled LaTeX templates cannot honor a two-column style."""
    if not style.formatting.two_column:
        return []
    return [
        f"Style {style.id!r} requests a two-column layout, but the bundled LaTeX "
        f"template {latex_name!r} is single-column; the PDF is rendered in one column. "
        "Re-export with export_to_docx (DOCX columns are applied via python-docx) or "
        "open the document in WordCraft for a two-column layout."
    ]


def _warnings_for_latex(style: StyleSpec, latex_name: str) -> list[str]:
    """Every honest degradation note for the LaTeX fallback."""
    warnings = _warnings_for_two_column(style, latex_name)
    if style.formatting.page_number_position == "none":
        warnings.append(
            f"Style {style.id!r} requests no page numbers, but the bundled LaTeX "
            f"template {latex_name!r} still numbers pages; the PDF will show page numbers."
        )
    return warnings


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
    csl_style: str | None = None,
    template_name: str = "default_academic",
    polish: bool = True,
    strict_citations: bool = True,
    style: str | None = None,
) -> dict:
    """Export every draft chapter to one `.docx` via Pandoc + Citeproc.

    Regenerates references.bib/references.csl.json from the project library,
    merges the draft chapters in the resolved style's section order, verifies
    every `[@citekey]` against that library, then converts and applies the
    style's page/typography formatting. Writes `exports/<output_name>.docx`.

    The export refuses to run until the research brief is complete and at least
    one paper is saved, returning `precondition_failed` with remediation steps.
    """
    try:
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            project = await _get_project_or_raise(conn, project_id)
            resolved_style = await _resolve_style_for_export(conn, project_id, style)
            blocked = await _precondition_failed(conn, project_id, resolved_style)
            if blocked is not None:
                return blocked

            project_dir = _project_dir(settings, project)
            effective_csl = csl_style or resolved_style.citation
            output, missing, extra_files = await _render_docx_export(
                conn,
                project,
                project_dir,
                output_name,
                effective_csl,
                template_name,
                polish,
                strict_citations,
                resolved_style,
            )
            return {
                "output_path": str(output),
                "format": "docx",
                "style_id": resolved_style.id,
                "csl_style": effective_csl,
                "template_name": template_name,
                "cited_but_missing": missing,
                "warnings": [
                    *_warnings_for_missing(missing),
                    *_warnings_for_extra_drafts(extra_files),
                    *_warnings_for_heading_numbering(resolved_style),
                ],
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
    template_name: str | None = None,
    compile_pdf: bool = True,
    strict_citations: bool = True,
    style: str | None = None,
) -> dict:
    """Export every draft chapter to a standalone `.tex`, optionally compiling a PDF.

    Merges the draft chapters in the resolved style's outline order, verifies
    every `[@citekey]` against the project library, and forwards the style's page
    geometry/typography as Pandoc metadata. `template_name` defaults to the
    style's `latex_template` (else `ieee`). Writes `exports/<output_name>.tex`
    and, when `compile_pdf` is true, compiles it with Tectonic into
    `exports/<output_name>.pdf`.

    The export refuses to run until the research brief is complete and at least
    one paper is saved, returning `precondition_failed` with remediation steps.
    """
    try:
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            resolved_style = await _resolve_style_for_export(conn, project_id, style)
            blocked = await _precondition_failed(conn, project_id, resolved_style)
            if blocked is not None:
                return blocked

            project = await _get_project_or_raise(conn, project_id)
            project_dir = _project_dir(settings, project)
            tex_path = _export_target(project_dir, output_name, ".tex")
            latex_name = resolve_latex_template(
                template_name or resolved_style.export.get("latex_template") or "ieee"
            )
            csl_name = "ieee" if latex_name == "ieee" else resolve_csl(resolved_style.citation)

            sections = await repository.list_draft_sections(conn, project_id)
            markdown, extra_files = _style_merge_markdown(project_dir, sections, resolved_style)
            markdown = _draft_markdown_or_raise(markdown)
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
                    metadata=latex_metadata(resolved_style.formatting),
                )

            pdf_path: Path | None = None
            if compile_pdf:
                pdf_path = latex_engine.compile_pdf(tex_path, tex_path.parent)

            return {
                "output_path": str(pdf_path or tex_path),
                "format": "latex",
                "style_id": resolved_style.id,
                "tex_path": str(tex_path),
                "pdf_path": str(pdf_path) if pdf_path else None,
                "template_name": latex_name,
                "csl_style": csl_name,
                "cited_but_missing": missing,
                "warnings": [
                    *_warnings_for_missing(missing),
                    *_warnings_for_extra_drafts(extra_files),
                    *_warnings_for_latex(resolved_style, latex_name),
                ],
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
    csl_style: str | None = None,
    strict_citations: bool = True,
    style: str | None = None,
) -> dict:
    """Merge all draft chapters into one Citeproc-rendered Markdown file.

    The resolved research style (`style`, else the project's, else the default)
    decides the chapter merge order and citation default; an explicit `csl_style`
    still wins. In-text `[@citekey]` markers are formatted and a reference list is
    appended; the result is written to `exports/<output_name>.md`.

    The export refuses to run until the research brief is complete and at least
    one paper is saved, returning `precondition_failed` plus `style_id` and
    remediation steps.
    """
    try:
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            project = await _get_project_or_raise(conn, project_id)
            resolved_style = await _resolve_style_for_export(conn, project_id, style)
            blocked = await _precondition_failed(conn, project_id, resolved_style)
            if blocked is not None:
                return {**blocked, "style_id": resolved_style.id}

            project_dir = _project_dir(settings, project)
            output = _export_target(project_dir, output_name, ".md")
            effective_csl = csl_style or resolved_style.citation
            csl_name = resolve_csl(effective_csl)

            sections = await repository.list_draft_sections(conn, project_id)
            markdown, extra_files = _style_merge_markdown(project_dir, sections, resolved_style)
            markdown = _draft_markdown_or_raise(markdown)
            missing = await _citation_gate(conn, project_id, markdown, strict_citations)
            bib_path, _csl_json = await _refresh_bibliography(conn, project_id, project_dir)

            with bundled_template(f"csl/{csl_name}.csl") as csl_path:
                render_markdown(markdown, bib_path, csl_path, output, resource_path=project_dir)

            return {
                "output_path": str(output),
                "format": "markdown",
                "style_id": resolved_style.id,
                "csl_style": effective_csl,
                "cited_but_missing": missing,
                "warnings": [
                    *_warnings_for_missing(missing),
                    *_warnings_for_extra_drafts(extra_files),
                ],
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
    """Report the local WordCraft install, live desktop and confinement roots.

    Side effect: creates the workspace root directory when it does not yet
    exist, so that the returned `confinement.mcp_command` paths are always
    valid existing folders ready for `--automation-read-root`.
    """
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
                "version": (
                    await asyncio.to_thread(wordcraft_bridge.cli_version, cli)
                    if cli is not None
                    else None
                ),
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


_HANDOFF_MODES = ("live_authoring", "seed_docx", "batch_script")
_HANDOFF_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".svg", ".webp"})


_STEM_EXTENSIONS = (".docx", ".pdf", ".tex", ".md")


def _handoff_stem(output_name: str) -> str:
    name = output_name.strip()
    for ext in _STEM_EXTENSIONS:
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    if not name or name in {".", ".."} or "/" in name or "\\" in name or ".." in name:
        raise ToolError(
            f"output_name {output_name!r} must be a plain file name without directories."
        )
    return name


def _project_assets(project_dir: Path) -> list[Path]:
    assets = project_dir / "assets"
    if not assets.is_dir():
        return []
    return sorted(
        path
        for path in assets.iterdir()
        if path.is_file()
        and path.suffix.lower() in _HANDOFF_IMAGE_SUFFIXES
        and path.resolve().is_relative_to(assets.resolve())
    )


def _handoff_warnings(missing: list[str], bibliography: dict | None) -> list[str]:
    warnings = [
        "Paths below are absolute and must stay inside the WordCraft automation "
        "roots; point both roots at this project's workspace root.",
        "After an `outcome unknown` delivery failure, call `inspect_document` "
        "before repeating any step.",
    ]
    if bibliography and bibliography["markdown"].strip():
        warnings.append(
            "Citations and the reference list are Pandoc Citeproc output written as "
            "plain text; WordCraft's `references.bibliography` only lists its own "
            "citation fields, so do not add it on top of these steps."
        )
    warnings.extend(_warnings_for_missing(missing))
    return warnings


@mcp.tool()
async def prepare_wordcraft_handoff(
    project_id: str,
    csl_style: str | None = None,
    mode: Literal["live_authoring", "seed_docx", "batch_script"] = "live_authoring",
    strict_citations: bool = True,
    output_name: str = "manuscript",
    style: str | None = None,
) -> dict:
    """Hand a citation-verified draft to WordCraft as a blueprint, a seed DOCX or a job script.

    `live_authoring` renders the merged draft through Pandoc Citeproc and returns the
    parsed blocks plus the exact WordCraft tool sequence that writes them. `seed_docx`
    exports a `.docx` to open with `open_document`. `batch_script` writes
    `scripts/job.json` for `wordcraft-cli run --script`.

    The research style (`style`, else the project's, else the default) decides the
    chapter merge order, page setup and citation default; an explicit `csl_style`
    still wins over the style's citation. The handoff refuses to run until the
    research brief is complete and at least one paper is saved, returning
    `precondition_failed` plus the resolved `style_id` and remediation steps.
    """
    try:
        if mode not in _HANDOFF_MODES:
            raise ToolError(
                f"Unknown handoff mode {mode!r}; available modes are {list(_HANDOFF_MODES)!r}."
            )
        stem = _handoff_stem(output_name)
        settings = get_settings()
        conn = await get_connection(settings.db_path)
        try:
            project = await _get_project_or_raise(conn, project_id)
            resolved_style = await _resolve_style_for_export(conn, project_id, style)
            blocked = await _precondition_failed(conn, project_id, resolved_style)
            if blocked is not None:
                return {**blocked, "style_id": resolved_style.id}

            project_dir = _project_dir(settings, project)
            exports_dir = project_dir / "exports"
            effective_csl = csl_style or resolved_style.citation

            result: dict = {
                "project_id": project.id,
                "mode": mode,
                "style_id": resolved_style.id,
                "csl_style": effective_csl,
                "cited_but_missing": [],
                "handoff_markdown_path": None,
                "seed_docx_path": None,
                "script_path": None,
                "blueprint": None,
                "assets": [],
                "tool_sequence": [],
                "run_command": [],
                "warnings": [],
            }

            if mode == "live_authoring":
                sections = await repository.list_draft_sections(conn, project.id)
                markdown, extra_files = _style_merge_markdown(project_dir, sections, resolved_style)
                markdown = _draft_markdown_or_raise(markdown)
                missing = await _citation_gate(conn, project.id, markdown, strict_citations)
                output = _export_target(project_dir, f"{stem}-handoff", ".md")
                csl_name = resolve_csl(effective_csl)
                bib_path, _csl_json = await _refresh_bibliography(conn, project.id, project_dir)
                with bundled_template(f"csl/{csl_name}.csl") as csl_path:
                    render_markdown(
                        markdown,
                        bib_path,
                        csl_path,
                        output,
                        resource_path=project_dir,
                    )
                rendered = output.read_text(encoding="utf-8")
                assets = _project_assets(project_dir)
                blueprint = wordcraft_bridge.build_blueprint(
                    project.name,
                    output,
                    rendered,
                    assets,
                    effective_csl,
                    exports_dir,
                    stem,
                    project_dir=project_dir,
                    style=resolved_style,
                )
                result.update(
                    {
                        "cited_but_missing": missing,
                        "handoff_markdown_path": str(output),
                        "blueprint": blueprint,
                        "assets": blueprint["assets"],
                        "tool_sequence": blueprint["tool_sequence"],
                        "warnings": [
                            # build_blueprint already carries the style's heading
                            # numbering caveat; do not repeat it here.
                            *blueprint["warnings"],
                            *_handoff_warnings(missing, blueprint["bibliography"]),
                            *_warnings_for_extra_drafts(extra_files),
                        ],
                    }
                )
                return result

            seed_docx, missing, extra_files = await _render_docx_export(
                conn,
                project,
                project_dir,
                stem,
                effective_csl,
                "default_academic",
                True,
                strict_citations,
                resolved_style,
            )
            result["seed_docx_path"] = str(seed_docx)
            result["cited_but_missing"] = missing
            numbering = _warnings_for_heading_numbering(resolved_style)

            if mode == "seed_docx":
                result.update(
                    {
                        "tool_sequence": wordcraft_bridge.build_polish_sequence(
                            seed_docx,
                            effective_csl,
                            exports_dir,
                            stem,
                            style=resolved_style,
                        ),
                        "warnings": [
                            *_handoff_warnings(missing, None),
                            *_warnings_for_extra_drafts(extra_files),
                            *numbering,
                        ],
                    }
                )
                return result

            script = wordcraft_bridge.build_job_script(
                seed_docx,
                effective_csl,
                exports_dir / f"{stem}.docx",
                exports_dir / f"{stem}.pdf",
            )
            payload = wordcraft_bridge.serialize_job_script(script)
            script_path = _safe_destination(project_dir / "scripts" / "job.json", project_dir)
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_bytes(payload)
            cli = wordcraft_bridge.detect_wordcraft_cli()
            result.update(
                {
                    "script_path": str(script_path),
                    "run_command": [
                        str(cli) if cli is not None else "wordcraft-cli",
                        "run",
                        "--script",
                        str(script_path),
                    ],
                    "warnings": [
                        *_handoff_warnings(missing, None),
                        *_warnings_for_extra_drafts(extra_files),
                        *numbering,
                        "Run the command above with `wordcraft-cli` and no automation "
                        "roots: the job script already names absolute paths.",
                    ],
                }
            )
            return result
        finally:
            await conn.close()
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"Could not prepare a WordCraft handoff for project {project_id!r}: "
            f"{type(exc).__name__}: {exc}"
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


def _research_workflow_text(project_id: str, style_id: str = "article_imrad") -> str:
    return f"""You are running the full Research MCP workflow for project {project_id!r}
with the research style {style_id!r}.

**Style intake → confirmed brief → chapter drafting → citation gate → export.**
Every step is mandatory and ordered. Never skip the brief and never skip the
citation gate before exporting.

## Step 1 — Choose the style and read it

1. If the user has not chosen a style yet, call `list_research_styles()` and present
   each option's `id`, `label` and `description` so the user can pick one.
2. Call `get_research_style({style_id!r})` and use its `outline` (section `id`,
   `title`, `filename`, `order`, `guidance`), its `citation` default and its `export`
   templates (`docx_template`, `latex_template`) as the plan for the rest of this
   workflow.

## Step 2 — Save the research brief (user-confirmed)

The brief is the contract for the whole document and must be confirmed by the user
before any file is written. Never invent or auto-fill an answer.

Ask the user for each field, then call:

```
save_research_brief(
    project_id={project_id!r},
    style_id="<confirmed style id>",
    title="<confirmed title>",
    research_question="<confirmed research question>",
    citation="<confirmed citation style>",
)
```

- `citation` is required: always pass it explicitly. Offer the style's default
  (`get_research_style` → `citation`) as the suggestion, but only save it once the
  user confirms it.
- Optional fields: `language`, `scope`, `target_length`, `deadline`.
- If the result returns `status: "needs_input"`, ask the user about every field in
  `missing` and call `save_research_brief` again with the confirmed answers.

## Step 3 — Start the research

Call `start_research({project_id!r})` and branch on `status`:

- `needs_input` → **STOP.** Ask the user every question listed in `ask_user`, save
  the answers with `save_research_brief` (including `citation`), then call
  `start_research` again. Do not draft anything before the brief is complete.
- `exists` → the listed chapters already hold content. Ask the user whether to
  overwrite them, and only after explicit confirmation call
  `start_research({project_id!r}, overwrite=True)`.
- `created` → the skeleton chapters exist; continue with Step 4.

## Step 4 — Fill every chapter from the style outline

Work through the `outline` from `get_research_style({style_id!r})` in `order`:
write each chapter and save it with
`save_draft_section({project_id!r}, "<filename>", content, order=<order>)`,
following the section's `guidance`.

- Cite every claim with Pandoc citations: `[@citekey]`, `[@key1; @key2]`.
- A citekey may only come from a paper saved in this project with
  `save_paper_to_project` or `import_local_pdf`; read the evidence first with
  `read_paper_section` / `search_local_library`.
- Create figures with `generate_chart` and reference the file from the chapter.
- Research the literature with `search_papers` / `get_citation_graph` /
  `resolve_doi` when the library is still thin.

## Step 5 — Citation gate (blocking)

Call `verify_citations({project_id!r})` before any export.
Every `[@citekey]` must resolve against the project library. If anything is listed
as missing, fix or remove it and re-run the gate — do not proceed with unresolved
citations.

## Step 6 — Export according to the style

The export inherits this project's style: omit `style=` and leave `csl_style` unset
(or pass `csl_style=None`) unless the user explicitly asks for a different citation
style, so the style's own `citation` wins.

- Word document:
  `export_to_docx({project_id!r}, "paper")` — the style's `export.docx_template`
  applies the page setup and typography.
- LaTeX / PDF:
  `export_to_latex({project_id!r}, "paper", compile_pdf=True)` — the style's
  `export.latex_template` is the default template; Tectonic compiles the PDF.
- WordCraft MCP:
  `prepare_wordcraft_handoff({project_id!r}, mode="live_authoring")` for a writing
  blueprint, `mode="seed_docx"` for a seed DOCX, or `mode="batch_script"` for
  `wordcraft-cli run --script`; then follow the `write_in_wordcraft` prompt for the
  WordCraft execution and verification steps.

## Zero-Hallucination Rule

Every `[@citekey]` in every draft MUST correspond to a paper saved in this project.
Never invent a citekey, a DOI, a finding or a reference entry. If the library does
not support a sentence, remove the sentence or search for a real source first.
"""


# Public alias kept for prompt tests and callers that should not reach into privates.
research_workflow_text = _research_workflow_text


@mcp.prompt()
def research_workflow(project_id: str, style_id: str = "article_imrad") -> str:
    """Guide an AI through style intake, brief, drafting, citation gate and export."""
    return _research_workflow_text(project_id, style_id)


def _write_in_wordcraft_text(
    project_id: str,
    csl_style: str | None = None,
    target_filename: str = "manuscript",
) -> str:
    if csl_style:
        style_line = (
            f"**CSL style:** {csl_style!r} — explicit override, it wins over the "
            "project style's citation"
        )
        handoff_call = (
            f"prepare_wordcraft_handoff(\n"
            f"    {project_id!r}, csl_style={csl_style!r}, "
            'mode="live_authoring",\n)'
        )
        fallback_call = (
            f'export_to_docx({project_id!r}, "{target_filename}", csl_style={csl_style!r})'
        )
    else:
        style_line = (
            "**CSL style:** inherited from the project's research style (recommended — "
            "omit `csl_style` or pass `csl_style=None` so the style's citation wins)"
        )
        handoff_call = (
            f'prepare_wordcraft_handoff({project_id!r}, mode="live_authoring")\n'
            "# csl_style is omitted on purpose: the project style's citation wins."
        )
        fallback_call = f'export_to_docx({project_id!r}, "{target_filename}")'

    return f"""You are orchestrating Research MCP + WordCraft MCP to write a final document.

**Project:** {project_id!r}
{style_line}
**Output:** {target_filename}.docx / {target_filename}.pdf

This prompt covers the WordCraft handoff and execution. For the style intake, the
confirmed brief, chapter drafting and the citation gate, follow the
`research_workflow` prompt (Steps 1–5) first; come back here for Step 6.

---

## Step 0 — Prerequisites (delegated to `research_workflow`)

If the project has no complete brief or no drafts yet, run `research_workflow`:
`list_research_styles` → `get_research_style` → `save_research_brief` →
`start_research` → `save_draft_section` → `verify_citations`.
Do not write a document from an unconfirmed brief.

## Step 1 — Check the environment

Call `check_wordcraft_env("{project_id}")`.

- If `live_desktop.ready` is true, start the bridge with `--connect 127.0.0.1:7981`.
  **IMPORTANT:** `--connect` must NOT be combined with `--automation-read-root` or
  `--automation-write-root`; the running app does not support confinement.
- Else if `wordcraft_cli.found` is true, start the headless server with the
  `confinement.mcp_command` snippet from the report (both roots at the workspace root).
- Else: skip to Fallback below.

## Step 2 — Get the verified handoff

```
{handoff_call}
```

The style is inherited from the project (page setup, chapter order and citation
default), so leave `csl_style` unset unless the user asked for an override.

Stop immediately if `cited_but_missing` is non-empty — do NOT write a document that
silently drops citations. Re-run `verify_citations` after fixing the drafts.

## Step 3 — Execute the blueprint

The result carries `tool_sequence`, a list of WordCraft calls. Execute them in order:

- `new_document` → `file.setAuthor` → `styles.modify` (Normal, Heading 1–3) →
  `references.citationStyle` → content blocks (type_text / insert_table / insert.picture) →
  bibliography text → `caret.docEnd` → `file.strictReport` → `render_page` → `save_document`
  `.docx` → `file.exportPdf` strict.

**Safety rules:**
- In revision mode, call `execute("review.trackChanges", {{"value": true}})` right
  after `file.setAuthor`. Never call `review.trackChanges` without `value`; it toggles.
- After every `references.bibliography`, `references.toc`, or `references.tableOfFigures`
  call `caret.docEnd` to move the caret out of the generated heading.
- On an `outcome unknown` delivery error: call `inspect_document` first, check whether
  the step was actually applied, then decide whether to retry.

## Step 4 — Verify

`file.strictReport` must report `"strict": "passed"`.
`render_page` the first and last page; fix any visual problem before saving.
`save_document` to `{target_filename}.docx`, then `file.exportPdf` with `strict: true`.

## Fallback (no WordCraft available)

Call `{fallback_call}`.
"""


def _systematic_literature_review_text(topic: str, year_from: int) -> str:
    return f"""You are conducting a systematic literature review on: {topic!r}.
Include papers from {year_from} onwards.

For the generic, style-driven version of this flow see the `research_workflow` prompt.

## Step 0 — Prepare the project and the brief

1. `create_project(name)` — reuse an existing project instead if the user has one
   (`list_projects()`).
2. `list_research_styles()` — show the available styles and let the user choose
   (e.g. `rapid_synthesis`, `annotated_bibliography`, `article_imrad`).
3. `get_research_style(style_id)` — read the outline and guidance.
4. `save_research_brief(project_id, style_id=..., title=..., research_question=...,
   citation=...)` with answers **confirmed by the user** — `citation` must be passed
   explicitly and never auto-filled.
5. `start_research(project_id)` — if it returns `needs_input`, stop and ask the user
   the listed questions; if it returns `exists`, confirm overwriting with the user.

## Step 1 — Discover literature

1. `search_papers` with your topic across multiple sources:
   ```
   search_papers(
       query={topic!r}, year_from={year_from}, limit=15,
       sources=["openalex", "semantic_scholar", "arxiv"],
   )
   ```
2. Use `get_citation_graph` to find related work (backward and forward).
3. Use `resolve_doi` for any DOI to confirm metadata and check for open-access PDF.

## Step 2 — Save and parse selected papers

For each paper you decide to include:
1. `save_paper_to_project(project_id, identifier, download_pdf=True)` — saves
   metadata, generates citekey, downloads and parses PDF sections.
2. `list_paper_sections(project_id, citekey)` — see which sections are available.
3. `read_paper_section(project_id, citekey, section_name)` — read Methods, Results,
   Discussion without loading the full PDF into context.
4. `search_local_library(project_id, query)` — cross-search your library for
   specific findings across multiple papers.

## Step 3 — Build the citation database

1. `generate_bibliography(project_id)` — exports `references.bib` and
   `references.csl.json`.
2. `verify_citations(project_id)` — run before writing any draft to confirm all
   `[@citekey]` references are in the local database.

## Step 4 — Write the draft

Write each chapter as a separate Markdown file using `[@citekey]` citations. The
filename and order of every chapter come from the chosen style's `outline`; the
default IMRaD-style example is:
1. `save_draft_section(project_id, "00_abstract.md", content, order=0)`
2. `save_draft_section(project_id, "01_introduction.md", content, order=1)`
3. `save_draft_section(project_id, "02_methodology.md", content, order=2)` —
   search strategy, inclusion/exclusion criteria.
4. `save_draft_section(project_id, "03_findings.md", content, order=3)` —
   synthesise across papers.
5. `save_draft_section(project_id, "04_discussion.md", content, order=4)`
6. `save_draft_section(project_id, "05_conclusion.md", content, order=5)`

## Step 5 — Verify and export

1. `verify_citations(project_id)` — anti-hallucination gate.
2. Choose your export:
   - `export_to_docx(project_id, "literature_review")` — Word document
   - `export_to_latex(project_id, "literature_review",
       template_name="article_id", compile_pdf=True)` — PDF via Tectonic
   - `prepare_wordcraft_handoff(project_id)` — if WordCraft MCP is active

## Zero-Hallucination Rule

Every `[@citekey]` in a draft MUST correspond to a paper saved with
`save_paper_to_project`. Never invent citekeys.
"""


@mcp.prompt()
def systematic_literature_review(topic: str, year_from: int = 2000) -> str:
    """Guide an AI through a systematic literature review from search to draft."""
    return _systematic_literature_review_text(topic, year_from)


def _draft_latex_paper_text(project_id: str, template_name: str) -> str:
    if template_name == "ieee":
        intro = (
            f"You are drafting a LaTeX research paper for project {project_id!r}\n"
            "using the project's research style."
        )
        template_note = (
            "The project's research style supplies the page geometry, typography and\n"
            "citation, so omit `style=` and `template_name` from the call below: the\n"
            "style's `export.latex_template` (and its citation) win. The legacy `ieee`\n"
            "default is deliberately NOT passed — pass `template_name` only when the user\n"
            "explicitly asks for a specific template."
        )
        export_call = f"""export_to_latex(
    project_id={project_id!r},
    output_name="paper",
    compile_pdf=True,
    strict_citations=True,
)
# template_name is omitted on purpose: the project style's export.latex_template
# and citation win. Never add the legacy `ieee` default here."""
    else:
        intro = (
            f"You are drafting a LaTeX research paper for project {project_id!r}\n"
            f"using the {template_name!r} template the user asked for."
        )
        template_note = (
            f"The user explicitly asked for the {template_name!r} template, so pass it\n"
            "explicitly. The project's research style still supplies the page geometry and\n"
            "typography, so omit `style=`; `template_name` selects that template and its\n"
            "CSL citation."
        )
        export_call = f"""export_to_latex(
    project_id={project_id!r},
    output_name="paper",
    template_name="{template_name}",
    compile_pdf=True,
    strict_citations=True,
)"""

    return f"""{intro}

This prompt is the LaTeX tail of the generic `research_workflow`: for style intake,
the user-confirmed brief, `start_research` and chapter-by-chapter drafting, follow
the `research_workflow` prompt (Steps 1–4) instead of inventing your own structure.
`export_to_latex` returns `precondition_failed` until the brief is complete and at
least one paper is saved — fix that with `save_research_brief` first.

## Step 1 — Survey the project

1. `get_project_status({project_id!r})` — see existing drafts, papers, assets.
2. `generate_bibliography({project_id!r})` — ensure `references.bib` and
   `references.csl.json` are up to date.
3. If the brief is still incomplete: `list_research_styles()` → `get_research_style`
   → `save_research_brief` (user-confirmed, `citation` included).

## Step 2 — Write IMRaD sections

If not already present, create each section with `save_draft_section`. The filenames
and order come from the project style's `outline`; the IMRaD example is:

- `save_draft_section({project_id!r}, "00_abstract.md", content, order=0)`
  — 150–250 words.
- `save_draft_section({project_id!r}, "01_introduction.md", content, order=1)`
  — Introduction: motivate the problem, state contributions, cite `[@citekey]`.
- `save_draft_section({project_id!r}, "02_methods.md", content, order=2)`
  — Methods: dataset, model, algorithm.
- `save_draft_section({project_id!r}, "03_results.md", content, order=3)`
  — Results: quantitative findings; use `generate_chart` for figures.
- `save_draft_section({project_id!r}, "04_discussion.md", content, order=4)`
  — Discussion: interpret results, limitations.
- `save_draft_section({project_id!r}, "05_conclusion.md", content, order=5)`
  — Conclusion.

## Step 3 — Verify citations

1. `verify_citations({project_id!r})` — REQUIRED gate before export.
   Fix any unknown `[@citekey]` before continuing.

## Step 4 — Compile the LaTeX paper

{template_note}

```
{export_call}
```

If Tectonic is not installed, `compile_pdf=True` will raise an error.
Use `compile_pdf=False` to get the `.tex` file only, then run:
```bash
tectonic paper.tex
```
Tectonic auto-downloads required TeX packages — no TeX Live installation needed.

## Step 5 — Review the output

1. The compiled `.pdf` and `.tex` are in `exports/` inside the project workspace.
2. For final polish with WordCraft MCP:
   `prepare_wordcraft_handoff({project_id!r}, mode="seed_docx")` — omit `csl_style`
   so the style's citation wins. Then continue with the `write_in_wordcraft` prompt.

## Zero-Hallucination Rule

All citations must pass `verify_citations`. Never fabricate a citekey.
"""


@mcp.prompt()
def draft_latex_paper(project_id: str, template_name: str = "ieee") -> str:
    """Guide an AI through drafting and compiling a LaTeX paper."""
    return _draft_latex_paper_text(project_id, template_name)


@mcp.prompt()
def write_in_wordcraft(
    project_id: str,
    csl_style: str | None = None,
    target_filename: str = "manuscript",
) -> str:
    """Guide an AI through the full Research MCP → WordCraft MCP writing workflow."""
    return _write_in_wordcraft_text(project_id, csl_style, target_filename)


def _peer_review_in_wordcraft_text(project_id: str, docx_path: str) -> str:
    return f"""You are reviewing a research document in WordCraft MCP using the local library in
Research MCP project {project_id!r}.

**Document to review:** {docx_path}

---

## Step 1 — Open and configure

1. `check_wordcraft_env("{project_id}")` — confirm WordCraft is reachable.
2. `open_document("{docx_path}")` in WordCraft MCP.
3. `execute("file.setAuthor", {{"name": "Research Agent"}})` — your identity in tracked changes.
4. `execute("review.trackChanges", {{"value": true}})` — always pass `value: true`; without it
   the command is a toggle and might disable tracking.

## Step 2 — Audit claims and citations

For every factual claim or inline citation you encounter:

- Cross-check with `search_local_library("{project_id}", "<claim or topic>")`.
- Read the supporting passage with `read_paper_section("{project_id}", "<citekey>", "<section>")`.
- If a citekey in the document is not in the project library, flag it as a potential hallucination.

## Step 3 — Insert tracked edits and comments

- Use WordCraft tracked changes (already on) to correct text in-line.
- Insert a comment for each substantive concern: cite the evidence from the local library.
- Do not silently delete text; use strike-through or propose an alternative.

## Step 4 — Verify and save

1. `inspect_document` — check that tracked changes are recorded (look for revision marks).
2. On `outcome unknown`: call `inspect_document` first; never repeat a mutation blindly.
3. `save_document` to a `-reviewed` copy (e.g. append `-reviewed.docx`).
4. Summarise findings: list each issue with its citekey evidence and the section it appears in.
"""


@mcp.prompt()
def peer_review_in_wordcraft(project_id: str, docx_path: str) -> str:
    """Guide an AI to audit a document in WordCraft with tracked changes and library evidence."""
    return _peer_review_in_wordcraft_text(project_id, docx_path)


# ---------------------------------------------------------------------------
# MCP Resources
# ---------------------------------------------------------------------------


@mcp.resource("research://projects", name="projects", description="All research projects")
async def resource_projects() -> str:
    """Return JSON list of all research projects."""
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        rows = await repository.list_projects(conn)
    finally:
        await conn.close()
    return json.dumps(rows, indent=2, ensure_ascii=False)


@mcp.resource(
    "research://styles",
    name="styles",
    description="All available research styles and their outline sections",
)
async def resource_styles() -> str:
    """Return JSON list of every research style, bundled and user-defined."""
    return json.dumps(list_styles(), indent=2, ensure_ascii=False)


@mcp.resource(
    "research://templates",
    name="templates",
    description="Available CSL, DOCX, and LaTeX templates",
)
async def resource_templates() -> str:
    """Return JSON dict of available export templates, scanned from the templates directory."""
    templates_dir = Path(__file__).parent / "templates"
    return json.dumps(
        {
            "csl": sorted(p.name for p in (templates_dir / "csl").glob("*.csl")),
            "docx": sorted(p.name for p in (templates_dir / "docx").glob("*.docx")),
            "latex": sorted(p.name for p in (templates_dir / "latex").glob("*.tex")),
        },
        indent=2,
    )


@mcp.resource(
    "research://project/{project_id}/bibliography",
    name="project-bibliography",
    description="BibTeX bibliography for a research project",
)
async def resource_bibliography(project_id: str) -> str:
    """Return contents of references.bib, or instructions if not yet generated."""
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        try:
            project = await repository.get_project(conn, project_id)
        except repository.ProjectNotFoundError:
            return (
                f"Project {project_id!r} not found. Use list_projects() to see available projects."
            )
    finally:
        await conn.close()
    bib_path = settings.workspace_dir / project.slug / "references.bib"
    if not bib_path.exists():
        return (
            "Bibliography not yet generated for this project. "
            "Call generate_bibliography first to create references.bib."
        )
    return bib_path.read_text(encoding="utf-8")


@mcp.resource(
    "research://project/{project_id}/csl-json",
    name="project-csl-json",
    description="CSL-JSON bibliography for a research project",
)
async def resource_csl_json(project_id: str) -> str:
    """Return contents of references.csl.json, or instructions if not yet generated."""
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        try:
            project = await repository.get_project(conn, project_id)
        except repository.ProjectNotFoundError:
            return (
                f"Project {project_id!r} not found. Use list_projects() to see available projects."
            )
    finally:
        await conn.close()
    csl_path = settings.workspace_dir / project.slug / "references.csl.json"
    if not csl_path.exists():
        return (
            "CSL-JSON bibliography not yet generated for this project. "
            "Call generate_bibliography first to create references.csl.json."
        )
    return csl_path.read_text(encoding="utf-8")


@mcp.resource(
    "research://project/{project_id}/outline",
    name="project-outline",
    description="Draft chapter outline for a research project",
)
async def resource_outline(project_id: str) -> str:
    """Return Markdown table of draft sections, or instructions if none exist."""
    settings = get_settings()
    conn = await get_connection(settings.db_path)
    try:
        try:
            await repository.get_project(conn, project_id)
        except repository.ProjectNotFoundError:
            return (
                f"Project {project_id!r} not found. Use list_projects() to see available projects."
            )
        sections = await repository.list_draft_sections(conn, project_id)
    finally:
        await conn.close()
    if not sections:
        return (
            "No draft sections yet for this project. "
            "Call save_draft_section to start writing chapters."
        )
    lines = [
        "| order | filename | title | words | updated |",
        "|-------|----------|-------|-------|---------|",
    ]
    for s in sections:
        row = (
            f"| {s['section_order']} | {s['filename']} | {s['title'] or ''}"
            f" | {s['word_count']} | {s['updated_at'] or ''} |"
        )
        lines.append(row)
    return "\n".join(lines)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    mcp.run()


if __name__ == "__main__":
    main()
