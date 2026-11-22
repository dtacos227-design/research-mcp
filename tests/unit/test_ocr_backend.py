"""Backend OCR default: RapidOCR harus terpasang dan dipilih pymupdf4llm."""

from pathlib import Path


def test_pymupdf4llm_selects_rapidocr_without_missing_warning():
    from pymupdf4llm.helpers import document_layout

    document_layout.INFO_MESSAGES.seek(0)
    document_layout.INFO_MESSAGES.truncate(0)
    ocr_function = document_layout.select_ocr_function()
    dump = document_layout.INFO_MESSAGES.getvalue()

    assert callable(ocr_function)
    assert "RapidOCR is missing" not in dump
    assert "Using RapidOCR" in dump


def test_rapidocr_ships_onnx_models_for_offline_use():
    import rapidocr

    models = Path(rapidocr.__file__).parent / "models"
    names = {p.name for p in models.glob("*.onnx")}
    assert "PP-OCRv6_det_small.onnx" in names
    assert "PP-OCRv6_rec_small.onnx" in names
