"""LaTeX rendering via Pandoc templates and PDF compilation via Tectonic."""

import shutil
import subprocess
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.exporters.pandoc_engine import detect_pandoc, run_pandoc

_TECTONIC_INSTALL_HINT = (
    "Tectonic was not found on PATH. Install it with `brew install tectonic` on macOS, "
    "or see https://tectonic-typesetting.github.io/."
)


def detect_tectonic() -> str:
    """Return the absolute path to the `tectonic` binary, or raise a ToolError."""
    found = shutil.which("tectonic")
    if found is None:
        raise ToolError(_TECTONIC_INSTALL_HINT)
    return found


def render_latex(
    markdown: str,
    template_path: Path,
    bib_path: Path,
    csl_path: Path,
    output_tex: Path,
) -> Path:
    """Render merged Markdown to a standalone `.tex` document via Pandoc + Citeproc."""
    output_tex.parent.mkdir(parents=True, exist_ok=True)
    args = [
        detect_pandoc(),
        "-",
        "--from",
        "markdown",
        "--to",
        "latex",
        "--standalone",
        "--citeproc",
        "--bibliography",
        str(bib_path),
        "--csl",
        str(csl_path),
        "--template",
        str(template_path),
        "-o",
        str(output_tex),
    ]
    run_pandoc(args, input_text=markdown)
    return output_tex


def compile_pdf(tex_path: Path, exports_dir: Path) -> Path:
    """Compile a `.tex` file to `.pdf` with Tectonic into `exports_dir`."""
    tectonic = detect_tectonic()
    try:
        result = subprocess.run(
            [tectonic, str(tex_path), "-o", str(exports_dir)],
            capture_output=True,
            text=True,
            timeout=900,
        )
    except FileNotFoundError as exc:
        raise ToolError(_TECTONIC_INSTALL_HINT) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-3000:]
        raise ToolError(f"Tectonic failed with exit code {result.returncode}: {detail}")
    return exports_dir / tex_path.with_suffix(".pdf").name
