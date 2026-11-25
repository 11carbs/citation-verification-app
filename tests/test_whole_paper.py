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


def test_multi_source_and_repeated_source_keep_distinct_claim_pairs():
    paper = ("First claim [1], [2]. Second claim uses the first source [1].\n\n"
             "References\n[1] A. Author, First source.\n[2] B. Author, Second source.")
    result = extract_whole_paper(convert_pasted_paper(paper))
    assert [(pair.claim_id, pair.reference.ref_id) for pair in result.pairs] == [
        ("claim_0001", "ref_n_1"), ("claim_0001", "ref_n_2"),
        ("claim_0002", "ref_n_1"),
    ]
    assert len({pair.pair_id for pair in result.pairs}) == 3
    assert result.pairs[0].citation_group_id == result.pairs[1].citation_group_id
    assert result.pairs[2].citation_group_id != result.pairs[0].citation_group_id


def test_missing_ref_does_not_borrow_another_reference():
    paper = "Claim [1], [9].\n\nReferences\n[1] A. Author, Source."
    result = extract_whole_paper(convert_pasted_paper(paper))
    assert [pair.match_status for pair in result.pairs] == ["matched", "missing_reference"]
    assert result.pairs[1].reference is None
    assert result.pairs[1].candidate_ref_ids == ()


def test_repeated_marker_in_one_claim_is_recorded_without_duplicate_pair():
    paper = "Claim [1] and again [1].\n\nReferences\n[1] A. Author, Source."
    result = extract_whole_paper(convert_pasted_paper(paper))
    assert len(result.pairs) == 1
    assert any(issue.code == "REPEATED_REFERENCE_IN_CLAIM" for issue in result.issues)


def test_unparsed_citation_is_retained_for_review():
    paper = ("Claim [4]-[2]. Another claim (Wang 2024).\n\nReferences\n"
             "[2] A. Author, Source.\nWang, T. (2024). Other source.")
    result = extract_whole_paper(convert_pasted_paper(paper))
    assert [pair.match_status for pair in result.pairs] == ["unparsed", "unparsed"]
    assert all(pair.reference is None for pair in result.pairs)
    assert sum(issue.code == "UNPARSED_CITATION" for issue in result.issues) == 2


def test_malformed_numeric_marker_does_not_hide_other_valid_marker():
    paper = "Claim [1] and invalid [4]-[2].\n\nReferences\n[1] A. Author, Source."
    result = extract_whole_paper(convert_pasted_paper(paper))
    assert [pair.match_status for pair in result.pairs] == ["matched", "unparsed"]
    assert result.pairs[0].reference.ref_id == "ref_n_1"


def test_claim_and_reference_locations_survive_one_block_full_paper():
    paper = ("# Study\n\nClaim [1].\nSecond claim [1].\n\nReferences\n"
             "[1] A. Author, Source title.\n  Source continuation.")
    result = extract_whole_paper(convert_pasted_paper(paper))
    first, second = result.pairs
    assert (first.source_locations[0].line_start, first.source_locations[0].line_end) == (3, 3)
    assert (second.source_locations[0].line_start, second.source_locations[0].line_end) == (4, 4)
    assert first.source_locations[0].locator == "text:document"
    assert first.paragraph_char_start == 0
    assert second.paragraph_char_start > first.paragraph_char_start
    ref_location = first.reference.source_locations[0]
    assert (ref_location.line_start, ref_location.line_end) == (7, 8)


def test_pdf_claim_and_reference_keep_page_and_box():
    import pymupdf

    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Claim [1].")
    page_two = pdf.new_page()
    page_two.insert_text((72, 72), "References")
    page_two.insert_text((72, 100), "[1] A. Author, Source.")
    result = extract_whole_paper(convert_document(pdf.tobytes(), "study.pdf"))
    pair = result.pairs[0]
    assert pair.source_locations[0].page == 1
    assert pair.source_locations[0].bbox
    assert pair.reference.source_locations[0].page == 2
    assert pair.reference.source_locations[0].bbox


def test_vancouver_parenthesized_numbers_and_superscript_numeric():
    paper = ("One result (1, 2). Another result.^{2} Third result.¹\n\n"
             "References\n1. A. Author, First source.\n2. B. Author, Second source.")
    result = extract_whole_paper(convert_pasted_paper(paper))
    assert [pair.reference.ref_id for pair in result.pairs] == [
        "ref_n_1", "ref_n_2", "ref_n_2", "ref_n_1",
    ]


def test_html_superscript_bibliography_link_and_footnote():
    html = ("<p>One result<sup><a href='#ref1'>1</a></sup>. "
            "Another claim<sup><a href='#fn2'>2</a></sup>.</p>"
            "<h2>References</h2><p id='ref1'>[1] A. Author, Source.</p>"
            "<section class='footnotes'><ol><li id='fn2'>2. B. Author, Footnote source.</li></ol></section>")
    converted = convert_document(html.encode(), "study.html")
    result = extract_whole_paper(converted)
    assert [pair.match_status for pair in result.pairs] == ["matched", "matched"]
    assert [pair.reference.ref_id for pair in result.pairs] == ["ref_n_1", "ref_note_fn2"]
    assert result.pairs[1].reference.source_locations[0].locator == "html:note:fn2"


def test_jats_bibliography_xref_and_footnote_xref_keep_targets():
    xml = ("<article><body><p>A finding <xref ref-type='bibr' rid='R1'>1</xref> "
           "and a note <xref ref-type='fn' rid='fn1'>1</xref>.</p>"
           "<fn-group><fn id='fn1'><p>C. Author, Footnote source.</p></fn></fn-group></body>"
           "<ref-list><title>References</title><ref id='R1'><label>1</label>"
           "<mixed-citation>A. Author, Main source.</mixed-citation></ref></ref-list></article>")
    converted = convert_document(xml.encode(), "study.xml")
    result = extract_whole_paper(converted)
    assert [pair.reference.ref_id for pair in result.pairs] == ["ref_n_1", "ref_note_fn1"]
    assert result.pairs[1].reference.source_locations[0].locator.endswith("/fn[1]")


def test_math_exponent_and_equation_number_are_not_citations():
    paper = ("The formula uses x^{2}, as in Eq. (1). A cited result.^{1}\n\n"
             "References\n[1] A. Author, Source.")
    result = extract_whole_paper(convert_pasted_paper(paper))
    assert len(result.pairs) == 1
    assert result.pairs[0].citation_marker == "^{1}"
    assert result.pairs[0].match_status == "matched"


def test_superscript_without_matching_reference_is_uncertain():
    result = extract_whole_paper(convert_pasted_paper("A result.^{7}"))
    assert result.pairs[0].match_status == "unparsed"


def test_pdf_small_raised_number_is_preserved_as_citation_when_adjacent():
    import pymupdf

    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Claim.", fontsize=12)
    page.insert_text((108, 66), "1", fontsize=6)
    page.insert_text((72, 125), "References")
    page.insert_text((72, 150), "[1] A. Author, Source.")
    converted = convert_document(pdf.tobytes(), "raised.pdf")
    assert "Claim.^{1}" in converted.markdown
    result = extract_whole_paper(converted)
    assert len(result.pairs) == 1
    assert result.pairs[0].reference.ref_id == "ref_n_1"


def test_jats_xref_with_hidden_label_uses_explicit_target_id():
    xml = ("<article><body><p>A claim <xref ref-type='bibr' rid='R7'/></p></body>"
           "<ref-list><title>References</title><ref id='R7'>"
           "<mixed-citation>A. Author, Source.</mixed-citation></ref></ref-list></article>")
    converted = convert_document(xml.encode(), "target.xml")
    assert "[@R7]" in converted.markdown
    result = extract_whole_paper(converted)
    assert len(result.pairs) == 1
    assert result.pairs[0].reference.ref_id == "ref_struct_R7"


def test_html_ordered_reference_list_and_superscript_anchor():
    html = ("<p>A result<sup><a href='#b1'>1</a></sup>.</p>"
            "<h2>References</h2><ol><li id='b1'>A. Author, Source.</li></ol>")
    result = extract_whole_paper(convert_document(html.encode(), "ordered.html"))
    assert len(result.pairs) == 1
    assert result.pairs[0].reference.ref_id == "ref_n_1"


def test_numeric_range_and_year_suffix_journal_variants():
    numeric = ("Several sources [1–3, 5].\n\nReferences\n"
               "[1] Source one.\n[2] Source two.\n[3] Source three.\n[5] Source five.")
    numeric_result = extract_whole_paper(convert_pasted_paper(numeric))
    assert [pair.reference.ref_id for pair in numeric_result.pairs] == [
        "ref_n_1", "ref_n_2", "ref_n_3", "ref_n_5",
    ]
    author = ("Wang et al. (2024a) reported a result.\n\nReferences\n"
              "Wang, T., & Li, Y. (2024a). Study A.\nWang, T. (2024b). Study B.")
    author_result = extract_whole_paper(convert_pasted_paper(author))
    assert len(author_result.pairs) == 1
    assert author_result.pairs[0].reference.ref_id == "ref_a_001"
