"""Draft chapter management: Markdown files on disk indexed in draft_sections."""

import re
from pathlib import Path

import aiosqlite

from research_mcp.db.repository import (
    get_draft_section,
    list_draft_sections,
    upsert_draft_section,
)

_HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)

# A draft filename is a single path component ending in `.md` — no separators,
# no `..`, no absolute paths, no other extensions.
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.md$")


def _validate_filename(filename: str) -> None:
    if not _SAFE_FILENAME_RE.match(filename) or ".." in filename:
        raise ValueError(
            f"Draft filename {filename!r} must be a plain `.md` name (no directories)."
        )


def _derive_title(content: str) -> str | None:
    match = _HEADING_RE.search(content)
    return match.group(1).strip() if match else None


def _safe_destination(path: Path, project_dir: Path) -> Path:
    resolved_root = project_dir.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"Draft path escaped the project workspace: {resolved_path}")
    return resolved_path


def _write_draft_file(path: Path, project_dir: Path, content: str) -> Path:
    safe = _safe_destination(path, project_dir)
    safe.parent.mkdir(parents=True, exist_ok=True)
    temporary = safe.with_name(f".{safe.name}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(safe)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return safe


async def write_draft_section(
    conn: aiosqlite.Connection,
    project_id: str,
    project_dir: Path,
    filename: str,
    content: str,
    order: int | None,
) -> dict:
    _validate_filename(filename)
    if order is None:
        existing = await list_draft_sections(conn, project_id)
        order = (max((s["section_order"] for s in existing), default=0) + 1) or 1

    _write_draft_file(project_dir / "drafts" / filename, project_dir, content)
    title = _derive_title(content)
    word_count = len(content.split())
    await upsert_draft_section(conn, project_id, filename, order, title, word_count)

    return {
        "filename": filename,
        "section_order": order,
        "title": title,
        "word_count": word_count,
    }


async def read_draft_section(
    conn: aiosqlite.Connection,
    project_id: str,
    project_dir: Path,
    filename: str,
) -> dict:
    _validate_filename(filename)
    row = await get_draft_section(conn, project_id, filename)
    if row is None:
        raise ValueError(f"Draft {filename!r} was not found.")
    content_path = _safe_destination(project_dir / "drafts" / filename, project_dir)
    content = content_path.read_text(encoding="utf-8") if content_path.exists() else ""
    return {**row, "content": content}
