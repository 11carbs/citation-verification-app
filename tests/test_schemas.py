import pytest

from src.mock_data import mock_analysis_rows
from src.schemas import (
    AnalysisRow,
    CitationPair,
    EvidenceBasis,
    MethodResult,
    SourceStatus,
    SupportBand,
    support_band,
)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.99, SupportBand.SUPPORTED),
        (0.70, SupportBand.SUPPORTED),
        (0.69, SupportBand.UNCERTAIN),
        (0.40, SupportBand.UNCERTAIN),
        (0.39, SupportBand.UNSUPPORTED),
        (0.10, SupportBand.UNSUPPORTED),
    ],
)
def test_temporary_support_band_boundaries(score: float, expected: SupportBand) -> None:
    assert support_band(score) is expected


@pytest.mark.parametrize("score", [0.0, 0.09, 1.0, 2.0])
def test_support_score_outside_range_is_rejected(score: float) -> None:
    with pytest.raises(ValueError):
        MethodResult(support_score=score, evidence_basis=EvidenceBasis.ABSTRACT)


def test_mock_data_has_four_cited_references_and_skips_108() -> None:
    rows = mock_analysis_rows()
    assert [row.pair.ref_number for row in rows] == [47, 107, 109, 110]


def test_method_b_is_blank_in_mock_output() -> None:
    records = [row.to_record() for row in mock_analysis_rows()]
    assert all(record["方法 B 支持分數"] is None for record in records)
    assert all(record["方法 B 判斷依據"] == "" for record in records)



def test_unreachable_source_leaves_score_and_band_blank() -> None:
    """查不到來源時不給分數，也不拿「不支持」代替。"""

    pair = CitationPair(
        pair_id="s1-r9",
        claim_text="Some claim [9].",
        citation_marker="[9]",
        ref_number=9,
        ref_raw='[9] "Unreachable work," 2026.',
    )
    missing = AnalysisRow(
        pair=pair,
        method_a=MethodResult(
            support_score=None,
            evidence_basis=EvidenceBasis.UNAVAILABLE,
            source_status=SourceStatus.NOT_FOUND,
        ),
    )
    record = missing.to_record()

    assert missing.method_a.source_status is SourceStatus.NOT_FOUND
    assert record["方法 A 支持分數"] is None
    assert record["方法 A 證據層級"] == ""
