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
    resource_path: Path | None = None,
    metadata: dict[str, str] | None = None,
) -> Path:
    """Render merged Markdown to a standalone `.tex` document via Pandoc + Citeproc.

    `metadata` maps Pandoc template variables (for example `geometry`,
    `linestretch`, `fontsize`) to their values, so a style can drive page layout
    without editing the bundled template.
    """
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
        "--metadata",
        "reference-section-title=References",
        *_metadata_args(metadata),
        "--bibliography",
        str(bib_path),
        "--csl",
        str(csl_path),
        "--template",
        str(template_path),
        *_resource_args(resource_path),
        "-o",
        str(output_tex),
    ]
    run_pandoc(args, input_text=markdown)
    if resource_path is not None:
        _inject_graphicspath(output_tex, resource_path)
    return output_tex


def _resource_args(resource_path: Path | None) -> list[str]:
    if resource_path is None:
        return []
    return ["--resource-path", str(resource_path)]


def _metadata_args(metadata: dict[str, str] | None) -> list[str]:
    """Flatten a metadata mapping into repeated `--metadata key=value` pairs."""
    if not metadata:
        return []
    args: list[str] = []
    for key, value in metadata.items():
        args.extend(["--metadata", f"{key}={value}"])
    return args


_TEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "%": r"\%",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _tex_escape(text: str) -> str:
    """Escape TeX-special characters one at a time (no re-escaping of output)."""
    return "".join(_TEX_SPECIALS.get(char, char) for char in text)


def _inject_graphicspath(tex_path: Path, resource_path: Path) -> None:
    r"""Point `\includegraphics` at the project root so relative `assets/` paths work.

    Pandoc emits paths exactly as written in the Markdown, while LaTeX resolves
    them relative to the `.tex` file (which lives in `exports/`). Adding a
    `\graphicspath` entry for the project root fixes that without rewriting the
    document's own Markdown.
    """
    root = _tex_escape(resource_path.resolve().as_posix())
    line = f"\\graphicspath{{{{{root}/}}}}\n"
    text = tex_path.read_text(encoding="utf-8")
    marker = "\\begin{document}"
    if marker in text:
        text = text.replace(marker, line + marker, 1)
    else:
        text = line + text
    tex_path.write_text(text, encoding="utf-8")


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
