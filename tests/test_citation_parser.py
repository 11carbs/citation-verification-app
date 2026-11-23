import pytest

from src.citation_parser import (
    CitationParseError,
    extract_citation_groups,
    extract_citation_numbers,
    extract_claim_citations,
)


def test_extracts_single_citation() -> None:
    assert extract_citation_numbers("Researchers should adopt the standard [79].") == [79]


def test_extracts_adjacent_multiple_citations_in_order() -> None:
    text = "Existing solutions use W3C [47], [107], [109], [110] standards."
    assert extract_citation_numbers(text) == [47, 107, 109, 110]


def test_extracts_multiple_numbers_inside_one_bracket() -> None:
    assert extract_citation_numbers("Prior work [3, 7, 11] agrees.") == [3, 7, 11]


@pytest.mark.parametrize(
    "marker",
    ["[47]-[50]", "[47]–[50]", "[47]—[50]", "[47-50]", "[47–50]"],
)
def test_expands_continuous_ranges(marker: str) -> None:
    assert extract_citation_numbers(f"Prior work {marker} agrees.") == [47, 48, 49, 50]


def test_ignores_non_numeric_square_brackets() -> None:
    text = "Available [Online] and discussed in [12]."
    assert extract_citation_numbers(text) == [12]


def test_deduplicates_same_number_inside_one_claim() -> None:
    claim = extract_claim_citations("A result [7] is consistent with earlier work [7].")
    assert [mention.ref_number for mention in claim[0].mentions] == [7]


def test_keeps_separate_claim_contexts() -> None:
    claims = extract_claim_citations("First claim [1]. Second claim [2], [3].")
    assert [claim.claim_text for claim in claims] == ["First claim [1].", "Second claim [2], [3]."]
    assert claims[0].is_compound is False
    assert claims[1].is_compound is True


def test_preserves_original_range_marker() -> None:
    groups = extract_citation_groups("Prior work [4]–[6] agrees.")
    assert groups[0].marker == "[4]–[6]"
    assert groups[0].numbers == (4, 5, 6)


def test_rejects_descending_range() -> None:
    with pytest.raises(CitationParseError):
        extract_citation_numbers("Invalid range [9]-[4].")



def test_pdf_line_breaks_do_not_cut_the_claim() -> None:
    text = (
        "Password-Free Authentication and Secure Backup: To enable secure and\n"
        "password-free authentication, researchers should adopt FIDO2/WebAuthn\n"
        "standards [79]."
    )
    claims = extract_claim_citations(text)
    assert len(claims) == 1
    assert claims[0].claim_text.startswith("Password-Free Authentication")
    assert claims[0].claim_text.endswith("standards [79].")


def test_hyphen_at_line_end_is_rejoined() -> None:
    claims = extract_claim_citations("A privacy-\npreserving scheme was proposed [2].")
    assert claims[0].claim_text == "A privacy-preserving scheme was proposed [2]."


def test_abbreviations_do_not_split_sentences() -> None:
    claims = extract_claim_citations(
        "Smith et al. [3] showed fabrication. Tools, e.g. RefChecker [4], check metadata. See Fig. 2 for details [5]."
    )
    assert [c.claim_text for c in claims] == [
        "Smith et al. [3] showed fabrication.",
        "Tools, e.g. RefChecker [4], check metadata.",
        "See Fig. 2 for details [5].",
    ]


def test_blank_line_still_separates_paragraphs() -> None:
    claims = extract_claim_citations("First paragraph [1]\n\nSecond paragraph [2].")
    assert [c.claim_text for c in claims] == ["First paragraph [1]", "Second paragraph [2]."]


def test_clause_separation_keeps_adjacent_citations_and_ranges_together() -> None:
    claims = extract_claim_citations("A lowered error [1], [2]; B increased recall [3]-[4].")
    assert [claim.claim_text for claim in claims] == ["A lowered error [1], [2]", "B increased recall [3]-[4]."]
    assert [[mention.ref_number for mention in claim.mentions] for claim in claims] == [[1, 2], [3, 4]]


def test_ambiguous_citation_scope_keeps_the_sentence() -> None:
    text = "A [1] increased recall, while B [2] improved precision."
    claims = extract_claim_citations(text)
    assert [claim.claim_text for claim in claims] == [text]


def test_shared_terminal_citations_allow_independent_type_claims() -> None:
    text = "ResNet and DenseNet are convolutional neural networks [2, 1]."
    [claim] = extract_claim_citations(text)
    assert claim.claim_text == text
    assert claim.subclaims == (
        "ResNet is a convolutional neural network.",
        "DenseNet is a convolutional neural network.",
    )
    assert [item.ref_number for item in claim.mentions] == [2, 1]


def test_shared_terminal_citations_split_explicit_complete_type_clauses() -> None:
    text = "BERT is a language model, and SciBERT is a pretrained language model [1], [2]."
    [claim] = extract_claim_citations(text)
    assert claim.subclaims == ("BERT is a language model.", "SciBERT is a pretrained language model.")
    assert [item.ref_number for item in claim.mentions] == [1, 2]


def test_locally_cited_type_claims_keep_their_own_sources() -> None:
    claims = extract_claim_citations(
        "ResNet is a neural network [2], and DenseNet is a neural network [1]."
    )
    assert [claim.claim_text for claim in claims] == [
        "ResNet is a neural network [2]",
        "DenseNet is a neural network [1].",
    ]
    assert [[item.ref_number for item in claim.mentions] for claim in claims] == [[2], [1]]
    assert all(not claim.subclaims for claim in claims)


@pytest.mark.parametrize("text", [
    "BERT and SciBERT are pretrained language models [1].",
    "BERT and SciBERT are pretrained language models [1], [2]?",
    "BERT and SciBERT are jointly trained models [1], [2].",
    "BERT [1] and SciBERT [2] are pretrained language models.",
    "BERT and SciBERT are better models than GPT [1], [2].",
    "BERT and SciBERT cause different effects when fine-tuned [1], [2].",
    "BERT [1], and SciBERT is a pretrained language model [2].",
    "BERT uses one procedure during training [1], while using a different one during inference [2].",
    "Existing solutions utilize W3C [47], [110] standards for VCs, VP generation, and revocation.",
])
def test_shared_scope_does_not_rewrite_other_sentences(text: str) -> None:
    [claim] = extract_claim_citations(text)
    assert claim.claim_text == text
    assert not claim.subclaims
