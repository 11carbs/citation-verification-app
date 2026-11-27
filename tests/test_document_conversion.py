from io import BytesIO
from zipfile import ZipFile
import pytest
from src.document_conversion import (
    ConversionError, DocumentBlock, _order_pdf_page, _pdf_reference_label_first,
    conversion_capabilities, convert_document, convert_pasted_paper,
)
from src.document_handoff import CitationHandoff, ReferenceCandidate
from src.whole_paper import extract_whole_paper


@pytest.mark.parametrize("extension", ["html", "xml", "md", "txt", "doc"])
def test_retired_upload_formats_are_rejected(extension):
    with pytest.raises(ConversionError) as error:
        convert_document(b"Claim [1].", f"paper.{extension}")
    assert error.value.code == "UNSUPPORTED_FORMAT"


def test_pdf_margin_label_emitted_last_is_moved_before_its_reference_text():
    text = "Source author, Title.\n[29]"
    detail = {"lines": [
        {"bbox": (331, 386, 560, 394)},
        {"bbox": (314, 386.2, 327, 394.2)},
    ]}
    assert _pdf_reference_label_first(text, detail) == "[29]\nSource author, Title."


def test_pdf_near_equal_baselines_put_left_reference_label_first():
    content = DocumentBlock("body", "Source author, Title.", "page:1/block:1", 1,
                            (331, 54.0, 560, 72))
    label = DocumentBlock("label", "[67]", "page:1/block:2", 1,
                          (314, 54.2, 327, 62))
    assert [block.markdown for block in _order_pdf_page([content, label], 600)] == [
        "[67]", "Source author, Title.",
    ]


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


def test_docx_superscript_and_note_parts_keep_markers_and_locations():
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    doc = Document()
    paragraph = doc.add_paragraph("A cited claim")
    raised = paragraph.add_run("1,2")
    raised.font.superscript = True
    paragraph.add_run(" and a note")
    footnote = paragraph.add_run()
    footnote_ref = OxmlElement("w:footnoteReference")
    footnote_ref.set(qn("w:id"), "2")
    footnote._r.append(footnote_ref)
    endnote = paragraph.add_run()
    endnote_ref = OxmlElement("w:endnoteReference")
    endnote_ref.set(qn("w:id"), "3")
    endnote._r.append(endnote_ref)
    source = BytesIO()
    doc.save(source)
    output = BytesIO()
    with ZipFile(BytesIO(source.getvalue())) as original, ZipFile(output, "w") as written:
        for item in original.infolist():
            written.writestr(item, original.read(item.filename))
        written.writestr("word/footnotes.xml", (
            '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:footnote w:id="2"><w:p><w:r><w:t>Footnote source.</w:t></w:r></w:p></w:footnote>'
            '</w:footnotes>'
        ))
        written.writestr("word/endnotes.xml", (
            '<w:endnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:endnote w:id="3"><w:p><w:r><w:t>Endnote source.</w:t></w:r></w:p></w:endnote>'
            '</w:endnotes>'
        ))
    result = convert_document(output.getvalue(), "notes.docx")
    assert "A cited claim^{1,2} and a note[^fn2][^en3]" in result.markdown
    assert any(block.locator == "word:footnote:2" and "Footnote source" in block.markdown
               for block in result.blocks)
    assert any(block.locator == "word:endnote:3" and "Endnote source" in block.markdown
               for block in result.blocks)
    pairs = extract_whole_paper(result).pairs
    assert [(pair.citation_marker, pair.match_status) for pair in pairs] == [
        ("^{1,2}", "unparsed"), ("^{1,2}", "unparsed"),
        ("[^fn2]", "matched"), ("[^en3]", "matched"),
    ]
    assert pairs[2].reference.source_locations[0].locator == "word:footnote:2"
    assert pairs[3].reference.source_locations[0].locator == "word:endnote:3"


def test_internal_text_fixture_supports_parser_tests():
    text = "# Study\n\nClaim (Wang, 2024).\n\n## References\nWang (2024). Source."
    pasted = convert_pasted_paper(text)
    assert pasted.source_format == "internal_text"
    assert "## References" in pasted.markdown


def test_blank_paste_returns_explicit_error():
    with pytest.raises(ConversionError) as error:
        convert_pasted_paper(" \n\t ")
    assert error.value.code == "EMPTY_FILE"


def test_pdf_positions_and_empty_page_warning():
    import pymupdf
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Claim [1].")
    doc.new_page()
    result = convert_document(doc.tobytes(), "paper.pdf")
    assert result.blocks[0].page == 1
    assert result.blocks[0].bbox
    assert any("2" in warning and "OCR" in warning for warning in result.warnings)


def test_pdf_two_column_text_is_present_with_position_warning():
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Left column [1].")
    page.insert_text((330, 100), "Right column [2].")
    result = convert_document(doc.tobytes(), "columns.pdf")
    assert "Left column [1]" in result.markdown
    assert "Right column [2]" in result.markdown
    assert all(block.page == 1 and block.bbox for block in result.blocks)
    assert any("雙欄閱讀順序" in warning for warning in result.warnings)


def test_pdf_two_columns_read_top_to_bottom_with_running_header_removed():
    import pymupdf
    from src.document_conversion import DocumentBlock, _order_pdf_page

    blocks = []
    for index in range(3):
        blocks.append(DocumentBlock(f"left-{index}", f"Left {index + 1}.", "page:1",
                                    1, (72, 100 + 25 * index, 200, 115 + 25 * index)))
        blocks.append(DocumentBlock(f"right-{index}", f"Right {index + 1}.", "page:1",
                                    1, (340, 100 + 25 * index, 480, 115 + 25 * index)))
    assert [block.markdown for block in _order_pdf_page(blocks, 612)] == [
        "Left 1.", "Left 2.", "Left 3.", "Right 1.", "Right 2.", "Right 3."]

    doc = pymupdf.open()
    for page_number in (1, 2):
        page = doc.new_page()
        page.insert_text((72, 35), f"Page {page_number} of 2 Journal (2025)")
        for index in range(3):
            page.insert_text((72, 100 + 25 * index), f"Left {index + 1}.")
            page.insert_text((340, 100 + 25 * index), f"Right {index + 1}.")
    result = convert_document(doc.tobytes(), "columns.pdf")
    assert "Journal (2025)" not in result.markdown


def test_image_only_pdf_is_not_empty_success():
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), False)
    image.clear_with(255)
    page.insert_image(pymupdf.Rect(72, 72, 200, 200), stream=image.tobytes("png"))
    with pytest.raises(ConversionError) as error:
        convert_document(doc.tobytes(), "scan.pdf")
    assert error.value.code == "NO_EXTRACTABLE_TEXT"


def test_supported_formats_exclude_legacy_doc():
    assert conversion_capabilities()["extensions"] == ["pdf", "docx"]
    with pytest.raises(ConversionError) as error:
        convert_document(b"legacy Word file", "paper.doc")
    assert error.value.code == "UNSUPPORTED_FORMAT"


@pytest.mark.parametrize("name,data,code", [
    ("a.pdf", b"", "EMPTY_FILE"), ("a.exe", b"x", "UNSUPPORTED_FORMAT"),
    ("a.pdf", b"invalid", "CONVERSION_FAILED"),
    ("a.xml", b'<!DOCTYPE x [<!ENTITY e "oops">]><x>&e;</x>', "UNSUPPORTED_FORMAT"),
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
