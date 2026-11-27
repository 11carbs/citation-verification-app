"""Representative whole-paper extraction cases; no source lookup or model calls."""

from io import BytesIO

from src.document_conversion import ConvertedDocument, DocumentBlock, convert_document, convert_pasted_paper
from src.whole_paper import _repair_pdf_reference_url_gaps, extract_whole_paper


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


def test_section_heading_inside_pdf_text_block_is_not_part_of_cited_claim():
    blocks = (
        DocumentBlock("body", "Background\nThe literature has grown [1].\nResults\nA second finding [1].",
                      "page:1/block:1", 1, (72, 80, 400, 180)),
        DocumentBlock("heading", "References", "page:2/block:1", 2, (72, 80, 200, 90)),
        DocumentBlock("ref", "[1] A. Author, Source.", "page:2/block:2", 2,
                      (72, 105, 400, 120)),
    )
    document = ConvertedDocument("doc_test", "study.pdf", "pdf", "", blocks, (), "test")
    pairs = extract_whole_paper(document).pairs
    assert [pair.claim_text for pair in pairs] == [
        "The literature has grown [1].", "A second finding [1].",
    ]
    assert [pair.source_locations[0].line_start for pair in pairs] == [2, 4]


def test_inline_abstract_label_and_docx_heading_are_excluded_from_claim():
    paper = ("# Methods\n\nBackground: The first claim [1].\n\n"
             "## Discussion\n\nA second claim [1].\n\nReferences\n[1] Source.")
    pairs = extract_whole_paper(convert_pasted_paper(paper)).pairs
    assert [pair.claim_text for pair in pairs] == [
        "The first claim [1].", "A second claim [1].",
    ]


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


def test_author_surname_particle_matches_reference_even_after_pdf_line_break():
    paper = """The work exists (van
Rensburg, 2025).

van Rensburg (2025) discusses verification.

References
van Rensburg, L. J. J. (2025). AI-powered citation auditing. Journal.
"""
    outcome = extract_whole_paper(convert_pasted_paper(paper))
    assert outcome.references[0].authors == ("van Rensburg",)
    assert [pair.match_status for pair in outcome.pairs] == ["matched", "matched"]
    assert all(pair.reference.ref_id == "ref_a_001" for pair in outcome.pairs)


def test_pdf_acl_references_split_at_author_year_blocks_and_stop_before_appendix():
    contents = [
        (1, 72, 40, "Bibliographical References"),
        (1, 72, 70, "AI@Meta. 2024. The llama 3 herd of models."),
        (1, 72, 90, "Frederique Bordignon. 2022."),
        (1, 82, 102, "Critical citations in knowledge construction."),
        (1, 306, 70, "DeepSeek-AI. 2025. Deepseek-v3.2-exp: Boosting long-context efficiency."),
        (1, 306, 90, "Claire Glenton and Benedicte Carlsen. 2019."),
        (1, 316, 102, "When normal becomes normative. Journal."),
        (2, 72, 70, "A\nModel implementation details"),
        (2, 72, 90, "This appendix is not a reference."),
    ]
    blocks = tuple(DocumentBlock(f"block_{i:04d}", content, f"page:{page}/block:{i}", page,
                                 (x, y, x + 200, y + 10))
                   for i, (page, x, y, content) in enumerate(contents, 1))
    document = ConvertedDocument("doc_test", "study.pdf", "pdf", "", blocks, (), "test")
    refs = extract_whole_paper(document).references
    assert len(refs) == 4
    assert [ref.raw_text.split(". ", 1)[0] for ref in refs] == [
        "AI@Meta", "Frederique Bordignon", "DeepSeek-AI",
        "Claire Glenton and Benedicte Carlsen",
    ]
    assert all("appendix" not in ref.raw_text.lower() for ref in refs)


def test_pdf_hanging_indent_references_split_with_year_at_end():
    contents = [
        (108, 40, "REFERENCES"),
        (108, 70, "Martín Abadi, Andy Chu,"),
        (118, 82, "and Li Zhang. Source title, 2016."),
        (108, 105, "Ann Cavoukian et al. Privacy by design,"),
        (118, 117, "Information and privacy commissioner, 2009."),
        (108, 140, "Fast Company. Article title, 2025."),
        (108, 165, "Di Jin, Eileen Pan, and Peter Szolovits."),
        (118, 177, "arXiv:2009.13081, 2020. URL https://arxiv.org/abs/2009.13081"),
        (301, 752, "10"),
    ]
    blocks = tuple(DocumentBlock(f"block_{i:04d}", content, f"page:1/block:{i}", 1,
                                 (x, y, x + 200, y + 10))
                   for i, (x, y, content) in enumerate(contents, 1))
    document = ConvertedDocument("doc_test", "study.pdf", "pdf", "", blocks, (), "test")
    refs = extract_whole_paper(document).references
    assert len(refs) == 4
    assert [ref.year for ref in refs] == ["2016", "2009", "2025", "2020"]
    assert refs[1].authors == ("Cavoukian",)
    assert all("10" != ref.raw_text for ref in refs)


def test_acl_bibliographical_references_and_full_name_authors():
    paper = """A finding was reported (Abalkina & Bishop, 2023).

Bibliographical References
Anna Abalkina and Dorothy Bishop. 2023. Paper mills. Journal.
Ayush Agrawal, Mirac Suzgun, and Adam Kalai. 2024. A second paper.
"""
    outcome = extract_whole_paper(convert_pasted_paper(paper))
    assert [reference.authors for reference in outcome.references] == [
        ("Abalkina", "Bishop"), ("Agrawal", "Suzgun", "Kalai")]
    assert outcome.pairs[0].match_status == "matched"


def test_chinese_prose_with_fullwidth_author_year_parentheses():
    paper = """Liu 等人（2026）提出資料集。另一結果見（Wang，2024）。

參考文獻
Liu, Q. (2026). Dataset paper.
Wang, T. (2024). Other paper.
"""
    outcome = extract_whole_paper(convert_pasted_paper(paper))
    assert [pair.reference.ref_id for pair in outcome.pairs] == ["ref_a_001", "ref_a_002"]
    assert all(pair.match_status == "matched" for pair in outcome.pairs)


def test_numeric_references_on_separate_lines_ignore_year_and_appendix_samples():
    paper = """A finding [1] was reported.

References
1.
Author One. Title.
2. Author Two. Title.
2023. DOI continuation text.
Appendix A: Example references
[1] Fabricated example.
"""
    outcome = extract_whole_paper(convert_pasted_paper(paper))
    assert [reference.ref_id for reference in outcome.references] == ["ref_n_1", "ref_n_2"]
    assert "Author One" in outcome.pairs[0].reference.raw_text
    assert outcome.pairs[0].match_status == "matched"


def test_long_publication_identifier_is_not_a_reference_number():
    paper = """A claim [1].

References
13081. Identifier continuation, not a reference.
08223. Another identifier continuation.
9798400714542. Publisher identifier.
"""
    outcome = extract_whole_paper(convert_pasted_paper(paper))
    assert outcome.references == ()
    assert outcome.pairs[0].match_status == "missing_reference"


def test_pdf_license_footer_stops_last_reference_before_author_biography():
    paper = """Claim [1].

References
[1] A. Author, "A source," 2026.
Authorized licensed use limited to: University. Downloaded on November 16, 2026 from IEEE Xplore. Restrictions apply.
MA et al.: PAPER TITLE
Wanlun Ma (Member, IEEE) received the Bachelor's degree in engineering.
"""
    outcome = extract_whole_paper(convert_pasted_paper(paper))
    assert len(outcome.references) == 1
    assert outcome.references[0].raw_text == '[1] A. Author, "A source," 2026.'


def test_marker_only_reference_is_flagged_and_not_used_as_evidence():
    paper = """Claim [1] and [2].

References
[1]
[2] B. Author, "Complete source," 2025.
"""
    outcome = extract_whole_paper(convert_pasted_paper(paper))
    assert [ref.ref_id for ref in outcome.references] == ["ref_n_2"]
    assert [pair.match_status for pair in outcome.pairs] == ["missing_reference", "matched"]
    assert any(issue.code == "INCOMPLETE_REFERENCE_ENTRY" for issue in outcome.issues)


def test_structured_reference_marker_stays_with_its_following_entry():
    paper = """Claim [@B1].

References
[@B1]
Agarwal S, Choubey L, Yu H. Automatically classifying citations. 2010.
[@B2]
Wang T. A second article. 2024.
"""
    outcome = extract_whole_paper(convert_pasted_paper(paper))
    assert len(outcome.references) == 2
    assert "Agarwal S" in outcome.references[0].raw_text
    assert outcome.pairs[0].match_status == "matched"


def test_pdf_reference_url_line_gaps_are_joined_without_swallowing_access_date():
    raw = ("[29] Available: https://github.com/ badlogic/pi-mono. "
           "Accessed on: Mar. 22, 2026. Other: https://www. example.org/ "
           "Also: htt- ps://github. com/team/repo.")
    fixed = _repair_pdf_reference_url_gaps(raw)
    assert "https://github.com/badlogic/pi-mono. Accessed" in fixed
    assert "https://www.example.org/" in fixed
    assert "https://github.com/team/repo." in fixed


def test_author_year_references_with_year_on_later_line_are_separate_entries():
    paper = """A finding (Asai et al., 2026) and another (Bhattacharyya et al., 2023).

References
arXiv. (2026). API documentation.
Asai, A., He, J., Shao, R., Shi, W., Singh, A., Chang, J. C., Lo, K.,
Soldaini, L., Feldman, S., D'Arcy, M., Wadden, D., Latzke, M.,
Sparks, J., Hwang, J. D., Kishore, V., Tian, M., Ji, P., Liu, S.,
Tong, H., … Hajishirzi, H. (2026). A research paper. Journal.
Bhattacharyya, M., Miller, V. M., Bhattacharyya, D., & Miller, L. E.
(2023). Another research paper. Journal.
How to cite this article: Example, A. (2026). This article.
"""
    outcome = extract_whole_paper(convert_pasted_paper(paper))
    assert len(outcome.references) == 3
    assert [ref.authors[0] for ref in outcome.references] == ["arXiv", "Asai", "Bhattacharyya"]
    assert [pair.match_status for pair in outcome.pairs] == ["matched", "matched"]
    assert all("How to cite" not in ref.raw_text for ref in outcome.references)


def test_author_year_paper_does_not_turn_numbered_steps_into_citations():
    paper = """The pipeline has (1) extraction and (2) verification.
A venue name (RAAISA 2024) is not a reference. A result (Wang, 2024) is cited.

References
Wang, T. (2024). A source. Journal.
"""
    outcome = extract_whole_paper(convert_pasted_paper(paper))
    assert [pair.citation_marker for pair in outcome.pairs] == ["(Wang, 2024)"]


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


def test_structured_ref_marker_can_be_on_its_own_line():
    paper = "A finding [@R7].\n\nReferences\n[@R7]\nAuthor. Source title."
    outcome = extract_whole_paper(convert_pasted_paper(paper))
    assert outcome.pairs[0].reference.ref_id == "ref_struct_R7"
    assert "Source title" in outcome.pairs[0].reference.raw_text


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
