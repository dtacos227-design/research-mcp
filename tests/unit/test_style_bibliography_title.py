"""Research styles carry the reference-list heading used by the exporters."""

from research_mcp.exporters import pandoc_engine
from research_mcp.styles.registry import get_style


def test_style_defaults_to_english_reference_heading():
    style = get_style("article_imrad")
    assert style.bibliography_title == "References"


def test_indonesian_styles_use_daftar_pustaka():
    assert get_style("skripsi_s1").bibliography_title == "DAFTAR PUSTAKA"
    assert get_style("tesis_s2").bibliography_title == "DAFTAR PUSTAKA"


def test_render_docx_forwards_reference_section_title(tmp_path, monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_run_pandoc(args, input_text=None):
        captured["args"] = args

        class Result:
            returncode = 0
            stderr = ""
            stdout = ""

        return Result()

    monkeypatch.setattr(pandoc_engine, "detect_pandoc", lambda: "pandoc")
    monkeypatch.setattr(pandoc_engine, "run_pandoc", fake_run_pandoc)

    pandoc_engine.render_docx(
        "# Judul\n\nTeks.",
        tmp_path / "refs.bib",
        tmp_path / "style.csl",
        tmp_path / "template.docx",
        tmp_path / "out.docx",
        reference_section_title="DAFTAR PUSTAKA",
    )

    args = captured["args"]
    assert "reference-section-title=DAFTAR PUSTAKA" in args
    assert "reference-section-title=References" not in args


def test_render_docx_keeps_english_default(tmp_path, monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_run_pandoc(args, input_text=None):
        captured["args"] = args

        class Result:
            returncode = 0
            stderr = ""
            stdout = ""

        return Result()

    monkeypatch.setattr(pandoc_engine, "detect_pandoc", lambda: "pandoc")
    monkeypatch.setattr(pandoc_engine, "run_pandoc", fake_run_pandoc)

    pandoc_engine.render_docx(
        "# Title\n\nBody.",
        tmp_path / "refs.bib",
        tmp_path / "style.csl",
        tmp_path / "template.docx",
        tmp_path / "out.docx",
    )

    assert "reference-section-title=References" in captured["args"]
