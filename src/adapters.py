"""Stable interfaces for integrating the second and third team members."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from src.schemas import CitationPair, EvidenceBasis, SourceStatus


class TitleRelevance(StrEnum):
    """Temporary three-choice output of the Jev title question."""

    RELATED = "相關"
    UNCERTAIN = "不確定"
    UNRELATED = "不相關"


@dataclass(frozen=True, slots=True)
class ReferenceLookupResult:
    """Data contract supplied by the second team member."""

    ref_number: int
    title: str
    doi: str = ""
    url: str = ""
    abstract: str = ""
    source_status: SourceStatus = SourceStatus.PENDING
    error_message: str = ""
    source_note: str = ""
    abstract_source: str = ""


@dataclass(frozen=True, slots=True)
class JevJudgmentResult:
    """Data contract supplied by the third team member."""

    title_choice: TitleRelevance
    title_probabilities: dict[str, float]
    confidence: float
    support_score: float
    evidence_basis: EvidenceBasis

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        if not 0.1 <= self.support_score <= 0.99:
            raise ValueError("support_score must be between 0.1 and 0.99")
        if self.title_probabilities:
            total = sum(self.title_probabilities.values())
            if any(not 0.0 <= value <= 1.0 for value in self.title_probabilities.values()):
                raise ValueError("title probabilities must be between 0.0 and 1.0")
            if abs(total - 1.0) > 0.01:
                raise ValueError("title probabilities must sum to 1.0")


class ReferenceLookupAdapter(Protocol):
    """Interface implemented by the second team member's lookup module."""

    def lookup(self, pair: CitationPair) -> ReferenceLookupResult:
        """Return parsed metadata, abstract and source status for one pair."""


class JevAdapter(Protocol):
    """Interface implemented by the third team member's Jev module."""

    def judge(
        self,
        pair: CitationPair,
        reference: ReferenceLookupResult,
    ) -> JevJudgmentResult:
        """Return the title choice and support score for one pair."""

