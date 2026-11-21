import pytest

from src.adapters import JevJudgmentResult, TitleRelevance
from src.mock_adapters import MockJevAdapter, MockReferenceLookupAdapter
from src.mock_data import MOCK_CLAIM, MOCK_REFERENCES
from src.pairing import build_citation_pairs
from src.schemas import EvidenceBasis, SourceStatus


def test_mock_reference_adapter_simulates_abstract() -> None:
    pair = build_citation_pairs(MOCK_CLAIM, MOCK_REFERENCES)[0]
    result = MockReferenceLookupAdapter().lookup(pair)

    assert result.ref_number == 47
    assert result.source_status is SourceStatus.GOT_ABSTRACT
    assert result.abstract.startswith("[MOCK]")


def test_mock_jev_adapter_is_deterministic() -> None:
    pair = build_citation_pairs(MOCK_CLAIM, MOCK_REFERENCES)[0]
    reference = MockReferenceLookupAdapter().lookup(pair)
    result = MockJevAdapter().judge(pair, reference)

    assert result.title_choice is TitleRelevance.RELATED
    assert result.support_score == 0.82
    assert result.evidence_basis is EvidenceBasis.ABSTRACT
    assert sum(result.title_probabilities.values()) == pytest.approx(1.0)


def test_jev_contract_rejects_invalid_probability_total() -> None:
    with pytest.raises(ValueError):
        JevJudgmentResult(
            title_choice=TitleRelevance.RELATED,
            title_probabilities={"相關": 0.8, "不確定": 0.8, "不相關": 0.1},
            confidence=0.8,
            support_score=0.7,
            evidence_basis=EvidenceBasis.ABSTRACT,
        )


def test_fixture_pipeline_fills_method_a_and_keeps_method_b_blank(paired_mock_rows) -> None:
    assert len(paired_mock_rows) == 4
    assert all(row.method_a is not None for row in paired_mock_rows)
    assert all(row.method_b is None for row in paired_mock_rows)

