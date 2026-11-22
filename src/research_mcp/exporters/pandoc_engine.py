"""Pandoc wrapper: draft merging, DOCX rendering, and binary detection."""

import shutil
import subprocess
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

_PANDOC_INSTALL_HINT = (
    "Pandoc was not found on PATH. Install it with `brew install pandoc` on macOS, "
    "or see https://pandoc.org/installing.html."
)


def detect_pandoc() -> str:
    """Return the absolute path to the `pandoc` binary, or raise a ToolError."""
    found = shutil.which("pandoc")
    if found is None:
        raise ToolError(_PANDOC_INSTALL_HINT)
    return found


def merge_draft_markdown(project_dir: Path, sections: list[dict]) -> str:
    """Concatenate draft chapters into one Markdown document.

    Registered chapters come first, in the order given (section order); any
    `.md` file in `drafts/` without a database row is appended afterwards in
    filename order so hand-written chapters are never silently dropped.
    """
    drafts_dir = project_dir / "drafts"
    pieces: list[str] = []
    seen: set[str] = set()

    for section in sections:
        filename = str(section["filename"])
        seen.add(filename)
        text = _read_draft(drafts_dir / filename)
        if text:
            pieces.append(text)

    if drafts_dir.is_dir():
        for path in sorted(drafts_dir.glob("*.md")):
            if path.name in seen:
                continue
            text = _read_draft(path)
            if text:
                pieces.append(text)

    return "\n\n".join(pieces)


def _resource_args(resource_path: Path | None) -> list[str]:
    """Extra pandoc flags so relative `assets/...` links resolve from the project root."""
    if resource_path is None:
        return []
    return ["--resource-path", str(resource_path)]


def _toc_args(toc: bool) -> list[str]:
    """Ask Pandoc to generate a table of contents at the start of the document."""
    return ["--toc"] if toc else []


def _read_draft(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def run_pandoc(args: list[str], input_text: str | None = None) -> subprocess.CompletedProcess:
    """Run pandoc with `input_text` on stdin and raise ToolError on failure."""
    try:
        result = subprocess.run(
            args,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError as exc:
        raise ToolError(_PANDOC_INSTALL_HINT) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:2000]
        raise ToolError(f"pandoc failed with exit code {result.returncode}: {detail}")
    return result


def render_markdown(
    markdown: str,
    bib_path: Path,
    csl_path: Path,
    output: Path,
    resource_path: Path | None = None,
    toc: bool = False,
) -> Path:
    """Render merged Markdown through Citeproc and write a self-contained `.md`."""
    output.parent.mkdir(parents=True, exist_ok=True)
    args = [
        detect_pandoc(),
        "-",
        "--from",
        "markdown",
        "--to",
        "markdown",
        "--citeproc",
        *_toc_args(toc),
        "--metadata",
        "reference-section-title=References",
        "--bibliography",
        str(bib_path),
        "--csl",
        str(csl_path),
        *_resource_args(resource_path),
        "-o",
        str(output),
    ]
    run_pandoc(args, input_text=markdown)
    return output


def render_docx(
    markdown: str,
    bib_path: Path,
    csl_path: Path,
    reference_doc: Path,
    output: Path,
    resource_path: Path | None = None,
    toc: bool = False,
) -> Path:
    """Render merged Markdown to `.docx` via Pandoc + Citeproc."""
    output.parent.mkdir(parents=True, exist_ok=True)
    args = [
        detect_pandoc(),
        "-",
        "--from",
        "markdown",
        "--to",
        "docx",
        "--citeproc",
        *_toc_args(toc),
        "--metadata",
        "reference-section-title=References",
        "--bibliography",
        str(bib_path),
        "--csl",
        str(csl_path),
        "--reference-doc",
        str(reference_doc),
        *_resource_args(resource_path),
        "-o",
        str(output),
    ]
    run_pandoc(args, input_text=markdown)
    return output
