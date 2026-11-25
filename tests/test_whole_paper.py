"""Representative whole-paper extraction cases; no source lookup or model calls."""

from io import BytesIO

from src.document_conversion import convert_document, convert_pasted_paper
from src.whole_paper import extract_whole_paper


def test_numeric_citations_match_only_cited_refs_and_expand_range():
    paper = """# Study

The method uses two sources [1], [3]-[4]. Uncited background is ignored.

## References
[1] A. One, "First source," 2022.
[2] B. Two, "Not cited," 2023.
[3] C. Three, "Third source," 2024.
[4] D. Four, "Fourth source," 2025.
"""
    result = extract_whole_paper(convert_pasted_paper(paper))
    assert result.section_found
    assert len(result.references) == 4
    assert [pair.reference.ref_id for pair in result.pairs] == ["ref_n_1", "ref_n_3", "ref_n_4"]
    assert len({pair.claim_id for pair in result.pairs}) == 1
    assert all(pair.block_ids == ("block_0001",) for pair in result.pairs)
    assert "Uncited background" not in result.pairs[0].claim_text


def test_parenthetical_and_narrative_author_year():
    paper = """Study

Wang (2024) proposed a method. The result was extended (Li & Chen, 2023; Wang, 2024).

References
Wang, T. (2024). A method. Journal.
Li, K., & Chen, Y. (2023). An extension. Journal.
"""
    result = extract_whole_paper(convert_pasted_paper(paper))
    assert [pair.reference.ref_id for pair in result.pairs] == [
        "ref_a_001", "ref_a_002", "ref_a_001",
    ]
    assert len({pair.claim_id for pair in result.pairs}) == 2
    assert all(pair.match_status == "matched" for pair in result.pairs)


def test_ambiguous_author_year_is_not_arbitrarily_matched():
    paper = """A result was reported (Wang, 2024).

References
Wang, T. (2024). First study.
Wang, R. (2024). Second study.
"""
    result = extract_whole_paper(convert_pasted_paper(paper))
    assert result.pairs[0].match_status == "ambiguous"
    assert result.pairs[0].reference is None
    assert result.pairs[0].candidate_ref_ids == ("ref_a_001", "ref_a_002")


def test_missing_ref_and_missing_section_remain_explicit():
    paper = "The claim [7] lacks its source."
    result = extract_whole_paper(convert_pasted_paper(paper))
    assert result.pairs[0].match_status == "missing_reference"
    assert any(issue.code == "NO_REFERENCE_SECTION" for issue in result.issues)


def test_reference_entries_are_not_treated_as_claims():
    paper = "Background without a cited claim.\n\nReferences\n[1] Source [2], 2024."
    result = extract_whole_paper(convert_pasted_paper(paper))
    assert len(result.references) == 1
    assert not result.pairs
    assert any(issue.code == "NO_SUPPORTED_CITATIONS" for issue in result.issues)


def test_docx_heading_and_paragraph_locations():
    from docx import Document

    source = Document()
    source.add_heading("Study", 1)
    source.add_paragraph("The result follows Smith et al. (2020).")
    source.add_heading("References", 1)
    source.add_paragraph("Smith, J., & Doe, K. (2020). A result.")
    stream = BytesIO()
    source.save(stream)
    result = extract_whole_paper(convert_document(stream.getvalue(), "study.docx"))
    assert len(result.pairs) == 1
    assert result.pairs[0].match_status == "matched"
    assert result.pairs[0].block_ids == ("block_0002",)


def test_numeric_reference_with_same_label_is_ambiguous():
    paper = "Claim [1].\n\nReferences\n[1] First source.\n[1] Another source."
    result = extract_whole_paper(convert_pasted_paper(paper))
    assert result.pairs[0].match_status == "ambiguous"
    assert result.pairs[0].reference is None
    assert result.pairs[0].candidate_ref_ids == ("ref_n_1", "ref_n_1_dup2")


def test_html_document_uses_same_extraction_pipeline():
    html = ("<h1>Study</h1><p>A finding was reported (Wang, 2024).</p>"
            "<h2>References</h2><p>Wang, T. (2024). Source.</p>")
    converted = convert_document(html.encode(), "study.html")
    result = extract_whole_paper(converted)
    assert result.section_found
    assert len(result.pairs) == 1
    assert result.pairs[0].reference.raw_text.startswith("Wang, T.")


def test_pdf_text_and_reference_heading():
    import pymupdf

    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "A claim [1].")
    page.insert_text((72, 120), "References")
    page.insert_text((72, 145), "[1] A. Author, Source, 2024.")
    result = extract_whole_paper(convert_document(pdf.tobytes(), "study.pdf"))
    assert result.section_found
    assert len(result.pairs) == 1
    assert result.pairs[0].reference.ref_id == "ref_n_1"


def test_xml_ref_list_with_numeric_xref():
    xml = ("<article><body><p>A claim <xref rid='r1'>[1]</xref>.</p></body>"
           "<ref-list><title>References</title><ref id='r1'>"
           "[1] A. Author, Source, 2024.</ref></ref-list></article>")
    result = extract_whole_paper(convert_document(xml.encode(), "study.xml"))
    assert result.section_found
    assert len(result.pairs) == 1
    assert result.pairs[0].reference.ref_id == "ref_n_1"
