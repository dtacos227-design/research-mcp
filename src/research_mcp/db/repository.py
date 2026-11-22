import itertools
import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from research_mcp.models import PaperModel, ProjectModel
from research_mcp.parsers.sectionizer import SectionData

WORKSPACE_SUBDIRS = ("pdfs", "parsed", "drafts", "assets", "scripts", "exports")


class ProjectNotFoundError(Exception):
    pass


class PaperNotFoundError(Exception):
    pass


class SectionNotFoundError(Exception):
    pass


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"


async def project_workspace_dir(workspace_root: Path, slug: str) -> Path:
    return workspace_root / slug


async def create_project(
    conn: aiosqlite.Connection,
    workspace_root: Path,
    name: str,
    description: str = "",
) -> ProjectModel:
    base_slug = _slugify(name)
    now = datetime.now(UTC).isoformat()
    project_id = uuid.uuid4().hex

    slug = base_slug
    for suffix in itertools.count(2):
        try:
            await conn.execute(
                "INSERT INTO projects (id, slug, name, description, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, slug, name, description, now, now),
            )
            break
        except sqlite3.IntegrityError:
            slug = f"{base_slug}-{suffix}"

    # Row is inserted but uncommitted: create folders for the final slug, and
    # roll back if that fails so no project exists without its workspace.
    try:
        project_dir = await project_workspace_dir(workspace_root, slug)
        project_dir.mkdir(parents=True, exist_ok=True)
        for sub in WORKSPACE_SUBDIRS:
            (project_dir / sub).mkdir(parents=True, exist_ok=True)
        await conn.commit()
    except BaseException:
        await conn.rollback()
        raise

    return ProjectModel(
        id=project_id,
        slug=slug,
        name=name,
        description=description,
        created_at=now,
        updated_at=now,
    )


async def get_project(conn: aiosqlite.Connection, project_id: str) -> ProjectModel:
    async with conn.execute(
        "SELECT id, slug, name, description, style_id, created_at, updated_at"
        " FROM projects WHERE id = ?",
        (project_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        raise ProjectNotFoundError(
            f"Project '{project_id}' not found. Use list_projects to see valid project ids."
        )
    return ProjectModel(
        id=row[0],
        slug=row[1],
        name=row[2],
        description=row[3] or "",
        style_id=row[4],
        created_at=row[5],
        updated_at=row[6],
    )


async def set_project_brief(
    conn: aiosqlite.Connection,
    project_id: str,
    brief_json: str,
    style_id: str,
) -> None:
    """Persist a project's research brief JSON together with the style it was built for."""
    await get_project(conn, project_id)
    await conn.execute(
        "UPDATE projects SET brief_json = ?, style_id = ?, updated_at = ? WHERE id = ?",
        (brief_json, style_id, datetime.now(UTC).isoformat(), project_id),
    )
    await conn.commit()


async def get_project_brief(
    conn: aiosqlite.Connection,
    project_id: str,
) -> dict | None:
    """Return the stored brief as a dict, or None when the project has no brief yet."""
    await get_project(conn, project_id)
    async with conn.execute(
        "SELECT brief_json FROM projects WHERE id = ?",
        (project_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None or row[0] is None:
        return None
    return json.loads(row[0])


async def set_project_style(
    conn: aiosqlite.Connection,
    project_id: str,
    style_id: str | None,
) -> None:
    """Point a project at a research style, or clear it when style_id is None."""
    await get_project(conn, project_id)
    await conn.execute(
        "UPDATE projects SET style_id = ?, updated_at = ? WHERE id = ?",
        (style_id, datetime.now(UTC).isoformat(), project_id),
    )
    await conn.commit()


async def list_projects(conn: aiosqlite.Connection) -> list[dict]:
    # Correlated subqueries (not two LEFT JOINs) so the paper x draft cross
    # product cannot inflate either count.
    async with conn.execute(
        """
        SELECT p.id, p.slug, p.name, p.description, p.style_id, p.created_at, p.updated_at,
               (SELECT COUNT(*) FROM papers WHERE project_id = p.id) AS paper_count,
               (SELECT COUNT(*) FROM draft_sections WHERE project_id = p.id) AS draft_count
        FROM projects p
        ORDER BY p.created_at, p.slug
        """
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {
            **ProjectModel(
                id=r[0],
                slug=r[1],
                name=r[2],
                description=r[3] or "",
                style_id=r[4],
                created_at=r[5],
                updated_at=r[6],
            ).model_dump(),
            "paper_count": r[7],
            "draft_count": r[8],
        }
        for r in rows
    ]


async def _get_paper(
    conn: aiosqlite.Connection,
    project_id: str,
    where_clause: str,
    value: str,
) -> PaperModel | None:
    async with conn.execute(
        f"""
        SELECT id, project_id, citekey, doi, title, authors_json, year, venue, abstract,
               citation_count, pdf_path, source_provider, external_id, item_type,
               authors_structured_json, bibtex_raw, csl_json, created_at
        FROM papers
        WHERE project_id = ? AND {where_clause} = ?
        """,
        (project_id, value),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return PaperModel(
        id=row[0],
        project_id=row[1],
        citekey=row[2],
        doi=row[3],
        title=row[4],
        authors=json.loads(row[5]),
        year=row[6],
        venue=row[7],
        abstract=row[8],
        citation_count=row[9],
        pdf_path=row[10],
        source_provider=row[11],
        external_id=row[12],
        item_type=row[13] or "other",
        authors_structured=json.loads(row[14]) if row[14] else None,
        bibtex_raw=row[15],
        csl_json=row[16],
        created_at=row[17],
    )


async def get_paper_by_doi(
    conn: aiosqlite.Connection,
    project_id: str,
    doi: str,
) -> PaperModel | None:
    return await _get_paper(conn, project_id, "doi", doi)


async def get_paper_by_citekey(
    conn: aiosqlite.Connection,
    project_id: str,
    citekey: str,
) -> PaperModel | None:
    return await _get_paper(conn, project_id, "citekey", citekey)


async def list_papers(
    conn: aiosqlite.Connection,
    project_id: str,
) -> list[PaperModel]:
    """Return every paper in a project, ordered by citekey."""
    async with conn.execute(
        """
        SELECT id, project_id, citekey, doi, title, authors_json, year, venue, abstract,
               citation_count, pdf_path, source_provider, external_id, item_type,
               authors_structured_json, bibtex_raw, csl_json, created_at
        FROM papers
        WHERE project_id = ?
        ORDER BY citekey
        """,
        (project_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        PaperModel(
            id=row[0],
            project_id=row[1],
            citekey=row[2],
            doi=row[3],
            title=row[4],
            authors=json.loads(row[5]),
            year=row[6],
            venue=row[7],
            abstract=row[8],
            citation_count=row[9],
            pdf_path=row[10],
            source_provider=row[11],
            external_id=row[12],
            item_type=row[13] or "other",
            authors_structured=json.loads(row[14]) if row[14] else None,
            bibtex_raw=row[15],
            csl_json=row[16],
            created_at=row[17],
        )
        for row in rows
    ]


async def set_paper_csl_json(
    conn: aiosqlite.Connection,
    paper_id: str,
    csl_json: str,
) -> None:
    """Write a paper's individual CSL-JSON object back to its row."""
    await conn.execute(
        "UPDATE papers SET csl_json = ? WHERE id = ?",
        (csl_json, paper_id),
    )


async def get_paper_by_pdf_path(
    conn: aiosqlite.Connection,
    project_id: str,
    pdf_path: str,
) -> PaperModel | None:
    return await _get_paper(conn, project_id, "pdf_path", pdf_path)


async def get_paper_by_external_id(
    conn: aiosqlite.Connection,
    project_id: str,
    source_provider: str,
    external_id: str,
) -> PaperModel | None:
    async with conn.execute(
        """
        SELECT id, project_id, citekey, doi, title, authors_json, year, venue, abstract,
               citation_count, pdf_path, source_provider, external_id, item_type,
               authors_structured_json, bibtex_raw, csl_json, created_at
        FROM papers
        WHERE project_id = ? AND source_provider = ? AND external_id = ?
        """,
        (project_id, source_provider, external_id),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return PaperModel(
        id=row[0],
        project_id=row[1],
        citekey=row[2],
        doi=row[3],
        title=row[4],
        authors=json.loads(row[5]),
        year=row[6],
        venue=row[7],
        abstract=row[8],
        citation_count=row[9],
        pdf_path=row[10],
        source_provider=row[11],
        external_id=row[12],
        item_type=row[13] or "other",
        authors_structured=json.loads(row[14]) if row[14] else None,
        bibtex_raw=row[15],
        csl_json=row[16],
        created_at=row[17],
    )


async def save_paper_and_sections(
    conn: aiosqlite.Connection,
    project_id: str,
    paper: PaperModel,
    sections: list[SectionData] | None,
) -> PaperModel:
    await get_project(conn, project_id)
    existing = await get_paper_by_doi(conn, project_id, paper.doi) if paper.doi else None
    if existing is None and paper.source_provider and paper.external_id:
        existing = await get_paper_by_external_id(
            conn, project_id, paper.source_provider, paper.external_id
        )
    if existing is None and paper.pdf_path:
        existing = await get_paper_by_pdf_path(conn, project_id, paper.pdf_path)

    if existing is not None:
        stored = paper.model_copy(
            update={
                "id": existing.id,
                "project_id": project_id,
                "citekey": existing.citekey,
                "created_at": existing.created_at,
                "pdf_path": paper.pdf_path or existing.pdf_path,
            }
        )
    else:
        stored = paper.model_copy(update={"project_id": project_id})

    try:
        await conn.execute("BEGIN")
        if existing is None:
            await conn.execute(
                """
                INSERT INTO papers (
                    id, project_id, citekey, doi, title, authors_json, year, venue, abstract,
                    citation_count, pdf_path, source_provider, external_id, item_type,
                    authors_structured_json, bibtex_raw, csl_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.id,
                    project_id,
                    stored.citekey,
                    stored.doi,
                    stored.title,
                    json.dumps(stored.authors, ensure_ascii=False),
                    stored.year,
                    stored.venue,
                    stored.abstract,
                    stored.citation_count,
                    stored.pdf_path,
                    stored.source_provider,
                    stored.external_id,
                    stored.item_type,
                    json.dumps(stored.authors_structured, ensure_ascii=False)
                    if stored.authors_structured
                    else None,
                    stored.bibtex_raw,
                    stored.csl_json,
                    stored.created_at,
                ),
            )
        else:
            await conn.execute(
                """
                UPDATE papers
                SET doi = ?, title = ?, authors_json = ?, year = ?, venue = ?, abstract = ?,
                    citation_count = ?, pdf_path = ?, source_provider = ?, external_id = ?,
                    item_type = ?, authors_structured_json = ?, bibtex_raw = ?, csl_json = ?
                WHERE id = ? AND project_id = ?
                """,
                (
                    stored.doi,
                    stored.title,
                    json.dumps(stored.authors, ensure_ascii=False),
                    stored.year,
                    stored.venue,
                    stored.abstract,
                    stored.citation_count,
                    stored.pdf_path,
                    stored.source_provider,
                    stored.external_id,
                    stored.item_type,
                    json.dumps(stored.authors_structured, ensure_ascii=False)
                    if stored.authors_structured
                    else None,
                    stored.bibtex_raw,
                    stored.csl_json,
                    stored.id,
                    project_id,
                ),
            )

        if sections is not None:
            await conn.execute("DELETE FROM paper_sections WHERE paper_id = ?", (stored.id,))
            for item in sections:
                await conn.execute(
                    """
                    INSERT INTO paper_sections (
                        paper_id, section_order, heading, content, word_count
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        stored.id,
                        item.section_order,
                        item.heading,
                        item.content,
                        item.word_count,
                    ),
                )
        await conn.commit()
    except BaseException:
        await conn.rollback()
        raise
    return stored


async def list_sections(
    conn: aiosqlite.Connection,
    project_id: str,
    citekey: str,
) -> list[dict]:
    await get_project(conn, project_id)
    paper = await get_paper_by_citekey(conn, project_id, citekey)
    if paper is None:
        raise PaperNotFoundError(f"Paper with citekey {citekey!r} was not found.")
    async with conn.execute(
        """
        SELECT section_order, heading, word_count
        FROM paper_sections
        WHERE paper_id = ?
        ORDER BY section_order, id
        """,
        (paper.id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [{"section_order": row[0], "heading": row[1], "word_count": row[2]} for row in rows]


async def get_section(
    conn: aiosqlite.Connection,
    project_id: str,
    citekey: str,
    section_name: str,
) -> dict:
    await get_project(conn, project_id)
    paper = await get_paper_by_citekey(conn, project_id, citekey)
    if paper is None:
        raise PaperNotFoundError(f"Paper with citekey {citekey!r} was not found.")

    normalized_name = " ".join(section_name.casefold().split())
    async with conn.execute(
        """
        SELECT section_order, heading, content, word_count
        FROM paper_sections
        WHERE paper_id = ?
        ORDER BY section_order, id
        """,
        (paper.id,),
    ) as cursor:
        rows = await cursor.fetchall()
    matches = [row for row in rows if " ".join(row[1].casefold().split()) == normalized_name]
    if not matches:
        raise SectionNotFoundError(
            f"Section {section_name!r} was not found for citekey {citekey!r}."
        )
    if len(matches) > 1:
        raise SectionNotFoundError(
            f"Section {section_name!r} is ambiguous for citekey {citekey!r}."
        )
    row = matches[0]
    return {
        "section_order": row[0],
        "heading": row[1],
        "content": row[2],
        "word_count": row[3],
    }


async def search_sections(
    conn: aiosqlite.Connection,
    project_id: str,
    query: str,
    limit: int,
) -> list[dict]:
    await get_project(conn, project_id)
    tokens = re.findall(r"\w+", query, flags=re.UNICODE)
    if not tokens:
        raise ValueError("Search query must contain at least one word")
    match_query = " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
    async with conn.execute(
        """
        SELECT p.citekey, f.heading, f.paper_id,
               snippet(paper_sections_fts, 1, '', '', ' … ', 30)
        FROM paper_sections_fts AS f
        JOIN papers AS p ON p.id = f.paper_id
        WHERE f.project_id = ? AND paper_sections_fts MATCH ?
        ORDER BY bm25(paper_sections_fts)
        LIMIT ?
        """,
        (project_id, match_query, limit),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {
            "citekey": row[0],
            "heading": row[1],
            "paper_id": row[2],
            "snippet": row[3][:300],
        }
        for row in rows
    ]


async def upsert_draft_section(
    conn: aiosqlite.Connection,
    project_id: str,
    filename: str,
    section_order: int,
    title: str | None,
    word_count: int,
) -> None:
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO draft_sections (
            project_id, filename, section_order, title, word_count, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id, filename) DO UPDATE SET
            section_order = excluded.section_order,
            title = excluded.title,
            word_count = excluded.word_count,
            updated_at = excluded.updated_at
        """,
        (project_id, filename, section_order, title, word_count, now),
    )
    await conn.commit()


async def get_draft_section(
    conn: aiosqlite.Connection,
    project_id: str,
    filename: str,
) -> dict | None:
    async with conn.execute(
        """
        SELECT filename, section_order, title, word_count, updated_at
        FROM draft_sections
        WHERE project_id = ? AND filename = ?
        """,
        (project_id, filename),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return {
        "filename": row[0],
        "section_order": row[1],
        "title": row[2],
        "word_count": row[3],
        "updated_at": row[4],
    }


async def list_draft_sections(
    conn: aiosqlite.Connection,
    project_id: str,
) -> list[dict]:
    async with conn.execute(
        """
        SELECT filename, section_order, title, word_count, updated_at
        FROM draft_sections
        WHERE project_id = ?
        ORDER BY section_order, filename
        """,
        (project_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {
            "filename": row[0],
            "section_order": row[1],
            "title": row[2],
            "word_count": row[3],
            "updated_at": row[4],
        }
        for row in rows
    ]
