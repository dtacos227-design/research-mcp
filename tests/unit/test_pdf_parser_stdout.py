"""Library chatter from the PDF parser must never reach stdout.

stdout is the MCP JSON-RPC frame channel: a stray line corrupts the stream and
the client drops the connection. PyMuPDF's `message()` writes to file
descriptor 1, so the guard has to work at the fd level, not via `sys.stdout`.
"""

import pymupdf

from research_mcp.parsers import pdf_extractor


def _make_pdf(path):
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Parking occupancy detection with YOLO and RT-DETR.")
    document.save(path)
    document.close()
    return path


def test_extract_markdown_writes_nothing_to_stdout(tmp_path, capfd):
    project_dir = tmp_path / "project"
    (project_dir / "pdfs").mkdir(parents=True)
    pdf_path = _make_pdf(project_dir / "pdfs" / "paper.pdf")

    markdown = pdf_extractor.extract_markdown(pdf_path, project_dir)

    captured = capfd.readouterr()
    assert captured.out == ""
    assert "Parking occupancy" in markdown


def test_parser_messages_are_rerouted_to_stderr(tmp_path, capfd, monkeypatch):
    project_dir = tmp_path / "project"
    (project_dir / "pdfs").mkdir(parents=True)
    pdf_path = _make_pdf(project_dir / "pdfs" / "paper.pdf")

    def noisy_parser(*args, **kwargs):
        print("=== Document parser messages ===")
        return [{"text": "# Paper\n\nBody.", "metadata": {}}]

    monkeypatch.setattr(pdf_extractor.pymupdf4llm, "to_markdown", noisy_parser)

    pdf_extractor.extract_markdown(pdf_path, project_dir)

    captured = capfd.readouterr()
    assert "Document parser messages" not in captured.out
    assert "Document parser messages" in captured.err


def test_metadata_extraction_writes_nothing_to_stdout(tmp_path, capfd):
    project_dir = tmp_path / "project"
    (project_dir / "pdfs").mkdir(parents=True)
    pdf_path = _make_pdf(project_dir / "pdfs" / "paper.pdf")

    pdf_extractor.extract_pdf_metadata(pdf_path, project_dir)

    assert capfd.readouterr().out == ""
