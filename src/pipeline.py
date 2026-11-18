"""Orchestrate adapters without coupling the website to either implementation."""

from __future__ import annotations

from src.adapters import JevAdapter, ReferenceLookupAdapter
from src.schemas import (
    AnalysisRow,
    CitationPair,
    MethodResult,
    ReferenceMatchStatus,
)


def run_method_a_pipeline(
    pairs: list[CitationPair],
    reference_adapter: ReferenceLookupAdapter,
    jev_adapter: JevAdapter,
) -> list[AnalysisRow]:
    """Run the second-person adapter followed by the third-person adapter."""

    rows: list[AnalysisRow] = []
    for pair in pairs:
        if pair.match_status is ReferenceMatchStatus.MISSING_REFERENCE:
            rows.append(AnalysisRow(pair=pair))
            continue

        reference = reference_adapter.lookup(pair)
        judgment = jev_adapter.judge(pair, reference)
        method_a = MethodResult(
            support_score=judgment.support_score,
            evidence_level=judgment.evidence_level,
            source_status=reference.source_status,
        )
        rows.append(AnalysisRow(pair=pair, method_a=method_a, method_b=None))
    return rows

