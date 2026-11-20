from importlib import resources
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.exporters import latex_engine, pandoc_engine
from research_mcp.exporters.docx_polisher import polish_docx
from research_mcp.exporters.latex_engine import compile_pdf, detect_tectonic, render_latex
from research_mcp.exporters.pandoc_engine import (
    detect_pandoc,
    merge_draft_markdown,
    render_docx,
)


def test_detect_pandoc_reports_install_hint(monkeypatch):
    monkeypatch.setattr(pandoc_engine.shutil, "which", lambda name: None)

    with pytest.raises(ToolError) as excinfo:
        detect_pandoc()

    message = str(excinfo.value)
    assert "pandoc" in message.lower()
    assert "brew" in message.lower()


def test_detect_tectonic_reports_install_hint(monkeypatch):
    monkeypatch.setattr(latex_engine.shutil, "which", lambda name: None)

    with pytest.raises(ToolError) as excinfo:
        detect_tectonic()

    message = str(excinfo.value)
    assert "tectonic" in message.lower()
    assert "brew" in message.lower()


def test_merge_draft_markdown_orders_registered_then_orphans(tmp_path):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / "01_abstract.md").write_text("# Abstract\n\nfirst")
    (drafts / "02_intro.md").write_text("# Intro\n\nsecond")
    (drafts / "zz_orphan.md").write_text("# Orphan\n\nunregistered")
    sections = [
        {"filename": "01_abstract.md", "section_order": 1},
        {"filename": "02_intro.md", "section_order": 2},
    ]

    merged = merge_draft_markdown(tmp_path, sections)

    assert merged.index("first") < merged.index("second") < merged.index("unregistered")
    assert merged.endswith("unregistered")


def test_render_docx_builds_pandoc_argv(tmp_path, monkeypatch):
    calls = []

    def fake_run_pandoc(args, input_text=None):
        calls.append((args, input_text))

    monkeypatch.setattr(pandoc_engine, "run_pandoc", fake_run_pandoc)
    monkeypatch.setattr(pandoc_engine, "detect_pandoc", lambda: "/usr/local/bin/pandoc")
    output = tmp_path / "out.docx"

    result = render_docx(
        "# Title\n\nbody",
        tmp_path / "references.bib",
        tmp_path / "apa.csl",
        tmp_path / "reference.docx",
        output,
    )

    assert result == output
    args, input_text = calls[0]
    assert args[0] == "/usr/local/bin/pandoc"
    assert input_text == "# Title\n\nbody"
    assert "--citeproc" in args
    assert _pair(args, "--metadata") == "reference-section-title=References"
    assert _pair(args, "--bibliography") == str(tmp_path / "references.bib")
    assert _pair(args, "--csl") == str(tmp_path / "apa.csl")
    assert _pair(args, "--reference-doc") == str(tmp_path / "reference.docx")
    assert _pair(args, "-o") == str(output)


def test_render_latex_builds_pandoc_argv(tmp_path, monkeypatch):
    calls = []

    def fake_run_pandoc(args, input_text=None):
        calls.append((args, input_text))

    monkeypatch.setattr(latex_engine, "run_pandoc", fake_run_pandoc)
    monkeypatch.setattr(latex_engine, "detect_pandoc", lambda: "pandoc")
    output = tmp_path / "out.tex"

    result = render_latex(
        "body",
        tmp_path / "ieee.tex",
        tmp_path / "references.bib",
        tmp_path / "ieee.csl",
        output,
    )

    assert result == output
    args, input_text = calls[0]
    assert input_text == "body"
    assert "--standalone" in args
    assert "--citeproc" in args
    assert _pair(args, "--metadata") == "reference-section-title=References"
    assert _pair(args, "--template") == str(tmp_path / "ieee.tex")
    assert _pair(args, "-o") == str(output)


def test_render_latex_injects_graphicspath_for_relative_assets(tmp_path, monkeypatch):
    def fake_run_pandoc(args, input_text=None):
        Path(_pair(args, "-o")).write_text(
            "\\documentclass{article}\n\\begin{document}\nbody\n\\end{document}\n"
        )

    monkeypatch.setattr(latex_engine, "run_pandoc", fake_run_pandoc)
    monkeypatch.setattr(latex_engine, "detect_pandoc", lambda: "pandoc")
    tex = tmp_path / "out.tex"

    render_latex(
        "body",
        tmp_path / "ieee.tex",
        tmp_path / "references.bib",
        tmp_path / "ieee.csl",
        tex,
        resource_path=tmp_path,
    )

    text = tex.read_text()
    root = tmp_path.resolve().as_posix()
    assert f"\\graphicspath{{{{{root}/}}}}" in text
    assert text.index("\\graphicspath") < text.index("\\begin{document}")


def test_compile_pdf_invokes_tectonic(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(latex_engine.subprocess, "run", fake_run)
    monkeypatch.setattr(latex_engine, "detect_tectonic", lambda: "tectonic")
    exports = tmp_path / "exports"
    exports.mkdir()

    result = compile_pdf(exports / "out.tex", exports)

    assert calls[0][0] == "tectonic"
    assert str(exports / "out.tex") in calls[0]
    assert result == exports / "out.pdf"


def test_polish_docx_sets_document_title(tmp_path):
    from docx import Document

    path = tmp_path / "doc.docx"
    Document().save(path)

    polish_docx(path, "My Thesis")

    assert Document(path).core_properties.title == "My Thesis"


def test_bundled_templates_are_importable():
    templates = resources.files("research_mcp.templates")

    for relative in (
        "csl/apa.csl",
        "csl/ieee.csl",
        "csl/vancouver.csl",
        "csl/chicago.csl",
        "latex/ieee.tex",
        "latex/article_id.tex",
        "docx/default_academic.docx",
    ):
        assert (templates / relative).is_file()
    assert "IEEEtran" in (templates / "latex/ieee.tex").read_text()
    assert Path(str(templates / "docx/default_academic.docx")).read_bytes()[:2] == b"PK"


def _pair(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]
