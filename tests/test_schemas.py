import pytest

from src.mock_data import mock_analysis_rows
from src.schemas import EvidenceLevel, MethodResult, SupportBand, support_band


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
        MethodResult(support_score=score, evidence_level=EvidenceLevel.ABSTRACT)


def test_mock_data_has_four_cited_references_and_skips_108() -> None:
    rows = mock_analysis_rows()
    assert [row.pair.ref_number for row in rows] == [47, 107, 109, 110]


def test_method_b_is_blank_in_mock_output() -> None:
    records = [row.to_record() for row in mock_analysis_rows()]
    assert all(record["方法 B 支持分數"] is None for record in records)
    assert all(record["方法 B 證據層級"] == "" for record in records)

