"""Shared data types for the citation-verification prototype.

The support bands are temporary engineering labels from the current meeting
decision. They are kept separate from source availability and evidence level
so later research revisions do not require rewriting the website.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SourceStatus(StrEnum):
    """Whether a cited source can currently be verified."""

    VERIFIED = "可確認"
    NOT_FOUND = "查不到"
    PENDING = "待查詢"


class EvidenceLevel(StrEnum):
    """The strongest evidence actually used for a judgment."""

    TITLE = "篇名"
    ABSTRACT = "摘要"
    FULL_TEXT = "全文"
    METADATA = "書目資料"
    UNAVAILABLE = "無可用證據"


class SupportBand(StrEnum):
    """Temporary three-level display label used by the prototype."""

    SUPPORTED = "支持"
    UNCERTAIN = "不確定"
    UNSUPPORTED = "不支持"


class ReferenceMatchStatus(StrEnum):
    """Whether an in-text citation number has a pasted ref entry."""

    MATCHED = "已配對"
    MISSING_REFERENCE = "缺少 ref"


def support_band(score: float) -> SupportBand:
    """Map a 0.1-0.99 score to the temporary display band."""

    if not 0.1 <= score <= 0.99:
        raise ValueError("support_score must be between 0.1 and 0.99")
    if score >= 0.70:
        return SupportBand.SUPPORTED
    if score >= 0.40:
        return SupportBand.UNCERTAIN
    return SupportBand.UNSUPPORTED


@dataclass(frozen=True, slots=True)
class CitationPair:
    """One citation claim paired with one referenced source."""

    pair_id: str
    claim_text: str
    citation_marker: str
    ref_number: int
    ref_raw: str
    is_compound: bool = False
    match_status: ReferenceMatchStatus = ReferenceMatchStatus.MATCHED
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class MethodResult:
    """A method's result for one claim-source pair."""

    support_score: float | None
    evidence_level: EvidenceLevel | None
    source_status: SourceStatus = SourceStatus.PENDING

    def __post_init__(self) -> None:
        if self.support_score is not None and not 0.1 <= self.support_score <= 0.99:
            raise ValueError("support_score must be between 0.1 and 0.99")

    @property
    def band(self) -> SupportBand | None:
        if self.support_score is None:
            return None
        return support_band(self.support_score)


@dataclass(frozen=True, slots=True)
class AnalysisRow:
    """One output-table row shared by the website and CSV exporter."""

    pair: CitationPair
    method_a: MethodResult | None = None
    method_b: MethodResult | None = None

    def to_record(self) -> dict[str, object]:
        """Return a flat, stable record for Streamlit and CSV output."""

        return {
            "pair_id": self.pair.pair_id,
            "引用陳述": self.pair.claim_text,
            "引用標記": self.pair.citation_marker,
            "ref 編號": self.pair.ref_number,
            "被引文獻": self.pair.ref_raw,
            "ref 配對狀態": self.pair.match_status.value,
            "錯誤狀態": self.pair.error_message,
            "方法 A 支持分數": _score(self.method_a),
            "方法 A 證據層級": _evidence(self.method_a),
            "方法 A 暫定分級": _band(self.method_a),
            "方法 B 支持分數": _score(self.method_b),
            "方法 B 證據層級": _evidence(self.method_b),
            "方法 B 暫定分級": _band(self.method_b),
        }


def _score(result: MethodResult | None) -> float | None:
    return None if result is None else result.support_score


def _evidence(result: MethodResult | None) -> str:
    if result is None or result.evidence_level is None:
        return ""
    return result.evidence_level.value


def _band(result: MethodResult | None) -> str:
    if result is None or result.band is None:
        return ""
    return result.band.value

