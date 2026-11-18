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

