import itertools
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from research_mcp.models import ProjectModel

WORKSPACE_SUBDIRS = ("pdfs", "parsed", "drafts", "assets", "scripts", "exports")


class ProjectNotFoundError(Exception):
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
        "SELECT id, slug, name, description, created_at, updated_at FROM projects WHERE id = ?",
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
        created_at=row[4],
        updated_at=row[5],
    )


async def list_projects(conn: aiosqlite.Connection) -> list[dict]:
    # Correlated subqueries (not two LEFT JOINs) so the paper x draft cross
    # product cannot inflate either count.
    async with conn.execute(
        """
        SELECT p.id, p.slug, p.name, p.description, p.created_at, p.updated_at,
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
                created_at=r[4],
                updated_at=r[5],
            ).model_dump(),
            "paper_count": r[6],
            "draft_count": r[7],
        }
        for r in rows
    ]
