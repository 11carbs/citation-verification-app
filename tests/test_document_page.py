"""Streamlit preview for the second-stage whole-paper handoff."""

from pathlib import Path

from streamlit.testing.v1 import AppTest


PAGE = Path(__file__).parents[1] / "pages" / "1_Document_conversion.py"


def test_one_paste_box_previews_pairs_and_creates_handoff_download_data():
    app = AppTest.from_file(str(PAGE), default_timeout=10).run()
    assert not app.exception
    assert len(app.text_area) == 1
    app.text_area[0].set_value(
        "Claim [1], [2].\n\nReferences\n[1] A. Author, First.\n[2] B. Author, Second."
    ).run()
    app.button[0].click().run()
    assert not app.exception
    bundle = app.session_state["handoff_bundle"]
    assert len(bundle.ready_pairs) == 2
    assert not bundle.review_pairs
    assert len(app.dataframe) >= 2  # Reference list and pair preview.
    assert b'"ready_pairs"' in bundle.to_json_bytes()


def test_empty_paste_shows_conversion_error():
    app = AppTest.from_file(str(PAGE), default_timeout=10).run()
    app.button[0].click().run()
    assert not app.exception
    assert any("EMPTY_FILE" in error.value for error in app.error)
