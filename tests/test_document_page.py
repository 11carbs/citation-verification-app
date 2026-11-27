"""Streamlit preview for the second-stage whole-paper handoff."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


PAGE = Path(__file__).parents[1] / "pages" / "1_Document_conversion.py"


def test_pdf_upload_previews_pairs_and_creates_handoff_download_data():
    app = AppTest.from_file(str(PAGE), default_timeout=10).run()
    assert not app.exception
    assert len(app.text_area) == 0
    assert len(app.radio) == 0
    assert app.file_uploader[0].allowed_type == [".pdf", ".docx"]
    app.file_uploader[0].set_value(("paper.pdf", _paper_file("pdf"), "application/pdf")).run()
    app.button[0].click().run()
    assert not app.exception
    bundle = app.session_state["handoff_bundle"]
    assert len(bundle.ready_pairs) == 1
    assert not bundle.review_pairs
    assert len(app.dataframe) >= 2  # Reference list and pair preview.
    assert b'"ready_pairs"' in bundle.to_json_bytes()
    assert any("方法 A 支持分數" in frame.value.columns for frame in app.dataframe)
    assert any("方法 B 支持分數" in frame.value.columns for frame in app.dataframe)
    assert any("方法 A 支持分類（暫定）" in frame.value.columns for frame in app.dataframe)
    assert any("方法 B 支持分類（暫定）" in frame.value.columns for frame in app.dataframe)
    assert not app.code  # Full Markdown is downloadable, not displayed on the page.


def test_missing_upload_shows_error():
    app = AppTest.from_file(str(PAGE), default_timeout=10).run()
    app.button[0].click().run()
    assert not app.exception
    assert any("請先上傳檔案" in error.value for error in app.error)


def _paper_file(extension: str) -> bytes:
    claim = "Claim [1]."
    reference = "[1] A. Author, Source."
    if extension == "pdf":
        import pymupdf
        paper = pymupdf.open()
        page = paper.new_page()
        page.insert_text((72, 72), claim)
        page.insert_text((72, 120), "References")
        page.insert_text((72, 145), reference)
        return paper.tobytes()
    if extension == "docx":
        from io import BytesIO
        from docx import Document
        paper = Document()
        paper.add_paragraph(claim)
        paper.add_heading("References", 1)
        paper.add_paragraph(reference)
        stream = BytesIO()
        paper.save(stream)
        return stream.getvalue()
    raise ValueError(extension)


@pytest.mark.parametrize("extension", ("pdf", "docx"))
def test_each_upload_format_uses_whole_paper_pairing(extension):
    app = AppTest.from_file(str(PAGE), default_timeout=10).run()
    assert not app.exception
    assert f".{extension}" in app.file_uploader[0].allowed_type
    app.file_uploader[0].set_value((f"paper.{extension}", _paper_file(extension),
                                    "application/octet-stream")).run()
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state["converted_document"].source_format == extension
    assert app.session_state["paper_extraction"].pairs[0].reference.ref_id == "ref_n_1"
    assert len(app.session_state["handoff_bundle"].ready_pairs) == 1


@pytest.mark.parametrize("data,expected_error", (
    (b"", "EMPTY_FILE"),
    (b"invalid PDF", "CONVERSION_FAILED"),
))
def test_upload_error_is_visible(data, expected_error):
    app = AppTest.from_file(str(PAGE), default_timeout=10).run()
    app.file_uploader[0].set_value(("paper.pdf", data, "application/pdf")).run()
    app.button[0].click().run()
    assert not app.exception
    assert any(expected_error in error.value for error in app.error)


def test_scanned_pdf_error_is_visible_on_upload_page():
    import pymupdf
    paper = pymupdf.open()
    page = paper.new_page()
    pixels = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), False)
    pixels.clear_with(255)
    page.insert_image(pymupdf.Rect(72, 72, 200, 200), stream=pixels.tobytes("png"))

    app = AppTest.from_file(str(PAGE), default_timeout=10).run()
    app.file_uploader[0].set_value(("scanned.pdf", paper.tobytes(), "application/pdf")).run()
    app.button[0].click().run()
    assert not app.exception
    assert any("NO_EXTRACTABLE_TEXT" in error.value for error in app.error)
