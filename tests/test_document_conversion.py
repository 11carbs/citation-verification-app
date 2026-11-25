from io import BytesIO
import subprocess
from pathlib import Path
import pytest
from src.document_conversion import ConversionError, convert_document
from src.document_handoff import CitationHandoff, ReferenceCandidate


@pytest.mark.parametrize("extension,content", [
    ("txt", "研究主張 [1]."), ("md", "# Study\n\nClaim [1]."),
    ("html", "<h1>Study</h1><p>Claim [1].</p><script>bad()</script>"),
    ("xml", '<article><title>Study</title><p>Claim <xref rid="r1">[1]</xref>.</p><ref id="r1">Source</ref></article>'),
])
def test_text_formats(extension, content):
    result = convert_document(content.encode(), f"paper.{extension}")
    assert "[1]" in result.markdown
    assert result.blocks[0].locator
    assert "bad()" not in result.markdown
    assert result.document_id == convert_document(content.encode(), f"other.{extension}").document_id


def test_docx_order_and_table():
    from docx import Document
    doc = Document()
    doc.add_heading("Study", 1)
    doc.add_paragraph("Claim [1].")
    doc.add_table(rows=1, cols=1).cell(0, 0).text = "Evidence"
    stream = BytesIO()
    doc.save(stream)
    result = convert_document(stream.getvalue(), "paper.docx")
    assert result.markdown.index("# Study") < result.markdown.index("Claim [1]") < result.markdown.index("Evidence")
    assert "| Evidence |" in result.markdown


def test_pdf_positions_and_empty_page_warning():
    import pymupdf
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Claim [1].")
    doc.new_page()
    result = convert_document(doc.tobytes(), "paper.pdf")
    assert result.blocks[0].page == 1
    assert result.blocks[0].bbox
    assert any("2" in warning and "OCR" in warning for warning in result.warnings)


def test_image_only_pdf_is_not_empty_success():
    import pymupdf
    doc = pymupdf.open()
    doc.new_page()
    with pytest.raises(ConversionError) as error:
        convert_document(doc.tobytes(), "scan.pdf")
    assert error.value.code == "NO_EXTRACTABLE_TEXT"


def test_doc_dependency_error(monkeypatch):
    monkeypatch.setattr("src.document_conversion.find_libreoffice", lambda: None)
    with pytest.raises(ConversionError) as error:
        convert_document(b"not a real doc", "paper.doc")
    assert error.value.code == "DOC_DEPENDENCY_MISSING"


def test_doc_conversion_contract_with_simulated_executable(monkeypatch):
    from docx import Document
    monkeypatch.setattr("src.document_conversion.find_libreoffice", lambda: "soffice")
    def run(command, **kwargs):
        assert kwargs["timeout"] == 60
        assert "--headless" in command
        root = Path(command[-1]).parent
        doc = Document()
        doc.add_paragraph("Converted [1].")
        doc.save(root / "input.docx")
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr("src.document_conversion.subprocess.run", run)
    result = convert_document(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"fixture", "paper.doc")
    assert "Converted [1]" in result.markdown


@pytest.mark.parametrize("name,data,code", [
    ("a.txt", b"", "EMPTY_FILE"), ("a.exe", b"x", "UNSUPPORTED_FORMAT"),
    ("a.pdf", b"invalid", "CONVERSION_FAILED"),
    ("a.xml", b'<!DOCTYPE x [<!ENTITY e "oops">]><x>&e;</x>', "CONVERSION_FAILED"),
])
def test_invalid_inputs(name, data, code):
    with pytest.raises(ConversionError) as error:
        convert_document(data, name)
    assert error.value.code == code


def test_handoff_author_year_and_ambiguity():
    kwargs = dict(document_id="d", claim_id="c", pair_id="p", citation_group_id="g",
                  claim_text="Claim", context="Context", citation_marker="(Wang, 2024)", block_ids=("b",))
    pair = CitationHandoff(**kwargs, match_status="matched",
                           reference=ReferenceCandidate("ref_wang", "Wang (2024). Study."))
    assert pair.to_dict()["reference"]["ref_id"] == "ref_wang"
    with pytest.raises(ValueError):
        CitationHandoff(**kwargs, match_status="matched")
    with pytest.raises(ValueError):
        CitationHandoff(**kwargs, match_status="ambiguous", candidate_ref_ids=("one",))
