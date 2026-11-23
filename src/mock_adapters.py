"""Deterministic mock adapters for UI and integration development only."""

from __future__ import annotations

from collections.abc import Callable
import re

from src.adapters import (
    JevJudgmentResult,
    JudgmentStatus,
    ReferenceLookupResult,
    TitleRelevance,
)
from src.schemas import CitationPair, EvidenceBasis, SourceStatus


_MOCK_SCORES = {47: 0.82, 79: 0.74, 107: 0.80, 109: 0.55, 110: 0.32}
_MOCK_CHOICES = {
    47: TitleRelevance.RELATED,
    79: TitleRelevance.RELATED,
    107: TitleRelevance.RELATED,
    109: TitleRelevance.UNCERTAIN,
    110: TitleRelevance.RELATED,
}


class MockReferenceLookupAdapter:
    """Simulate the second person's output without making network requests."""

    def lookup(self, pair: CitationPair) -> ReferenceLookupResult:
        title = _title_from_raw_reference(pair.ref_raw)
        return ReferenceLookupResult(
            ref_number=pair.ref_number,
            title=title,
            abstract=(
                f"[MOCK] 摘要資料，僅供測試 ref [{pair.ref_number}] 的串接與畫面。"
            ),
            source_status=SourceStatus.GOT_ABSTRACT,
        )


class MockJevAdapter:
    """Simulate the third person's Jev output with reproducible values."""

    def judge(
        self,
        pair: CitationPair,
        reference: ReferenceLookupResult,
        *,
        lookup_abstract: Callable[[], ReferenceLookupResult] | None = None,
    ) -> JevJudgmentResult:
        choice = _MOCK_CHOICES.get(pair.ref_number, TitleRelevance.UNCERTAIN)
        score = _MOCK_SCORES.get(pair.ref_number, 0.50)
        probabilities = _probabilities_for(choice)
        if choice is TitleRelevance.UNRELATED:
            return JevJudgmentResult(
                title_choice=choice,
                title_probabilities=probabilities,
                confidence=max(probabilities.values()),
                evidence_basis=EvidenceBasis.TITLE,
                status=JudgmentStatus.STOPPED,
            )
        if lookup_abstract is not None:
            reference = lookup_abstract()
        return JevJudgmentResult(
            title_choice=choice,
            title_probabilities=probabilities,
            confidence=max(probabilities.values()),
            support_score=score,
            status=JudgmentStatus.MOCK,
            evidence_basis=(
                EvidenceBasis.ABSTRACT if reference.abstract else EvidenceBasis.TITLE
            ),
        )


def _title_from_raw_reference(raw_reference: str) -> str:
    without_number = re.sub(r"^\s*\[\d+\]\s*", "", raw_reference).strip()
    first_line = without_number.splitlines()[0] if without_number else ""
    return first_line[:200]


def _probabilities_for(choice: TitleRelevance) -> dict[str, float]:
    if choice is TitleRelevance.RELATED:
        return {"相關": 0.84, "不確定": 0.11, "不相關": 0.05}
    if choice is TitleRelevance.UNRELATED:
        return {"相關": 0.05, "不確定": 0.10, "不相關": 0.85}
    return {"相關": 0.24, "不確定": 0.61, "不相關": 0.15}

