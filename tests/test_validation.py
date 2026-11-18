from src.pairing import build_pairing_outcome
from src.validation import IssueSeverity, validate_required_inputs


def test_empty_claim_is_an_error() -> None:
    issues = validate_required_inputs("   ", "[1] Source.")
    assert [(issue.code, issue.severity) for issue in issues] == [
        ("EMPTY_CLAIM", IssueSeverity.ERROR)
    ]


def test_empty_reference_list_is_an_error() -> None:
    issues = validate_required_inputs("Claim [1].", "")
    assert [(issue.code, issue.severity) for issue in issues] == [
        ("EMPTY_REFERENCES", IssueSeverity.ERROR)
    ]


def test_both_empty_inputs_report_both_errors() -> None:
    issues = validate_required_inputs("", "")
    assert {issue.code for issue in issues} == {"EMPTY_CLAIM", "EMPTY_REFERENCES"}


def test_no_citation_is_a_non_fatal_warning() -> None:
    outcome = build_pairing_outcome("No citation here.", "[1] Source.")
    assert outcome.pairs == []
    assert [issue.code for issue in outcome.issues] == ["NO_CITATIONS"]


def test_missing_reference_is_retained_and_warned() -> None:
    outcome = build_pairing_outcome("Claim [1], [2].", "[1] Source.")
    assert len(outcome.pairs) == 2
    assert "MISSING_REFERENCE" in {issue.code for issue in outcome.issues}


def test_duplicate_citation_is_merged_and_warned() -> None:
    outcome = build_pairing_outcome("Repeated source [7] and again [7].", "[7] Source.")
    assert len(outcome.pairs) == 1
    assert outcome.pairs[0].ref_number == 7
    assert [issue.code for issue in outcome.issues] == ["DUPLICATE_CITATION"]

