from src.mock_data import MOCK_CLAIM, MOCK_REFERENCES
from src.pairing import build_citation_pairs
from src.reference_parser import parse_reference_list
from src.schemas import ReferenceMatchStatus


def test_parses_multiline_reference_entries() -> None:
    text = """[1] First title,
continued on a second line.
[2] Second title."""
    entries = parse_reference_list(text)
    assert list(entries) == [1, 2]
    assert "continued on a second line" in entries[1].raw_text


def test_pairs_only_cited_references_and_skips_uncited_entry() -> None:
    pairs = build_citation_pairs(MOCK_CLAIM, MOCK_REFERENCES)
    assert [pair.ref_number for pair in pairs] == [47, 107, 109, 110]
    assert 108 not in {pair.ref_number for pair in pairs}
    assert all(pair.match_status is ReferenceMatchStatus.MATCHED for pair in pairs)
    assert all(pair.is_compound for pair in pairs)


def test_missing_reference_is_recorded_without_crashing() -> None:
    pairs = build_citation_pairs("Claim [1], [2].", "[1] Existing source.")
    assert len(pairs) == 2
    assert pairs[0].match_status is ReferenceMatchStatus.MATCHED
    assert pairs[1].match_status is ReferenceMatchStatus.MISSING_REFERENCE
    assert pairs[1].ref_raw == ""
    assert pairs[1].error_message == "ref 清單找不到 [2]"


def test_range_produces_one_pair_per_reference() -> None:
    refs = "\n".join(f"[{number}] Source {number}." for number in range(3, 7))
    pairs = build_citation_pairs("Summary [3]-[6].", refs)
    assert [pair.ref_number for pair in pairs] == [3, 4, 5, 6]


def test_same_source_in_two_claims_produces_two_traceable_pairs() -> None:
    pairs = build_citation_pairs("First claim [1]. Second claim [1].", "[1] Source one.")
    assert len(pairs) == 2
    assert pairs[0].claim_text == "First claim [1]."
    assert pairs[1].claim_text == "Second claim [1]."
    assert pairs[0].pair_id != pairs[1].pair_id


def test_no_numeric_citations_returns_no_pairs() -> None:
    assert build_citation_pairs("No citation here.", "[1] Source one.") == []

