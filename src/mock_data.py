"""Reproducible mock data for developing the website before API integration."""

from __future__ import annotations

from src.schemas import AnalysisRow, CitationPair, EvidenceBasis, MethodResult, SourceStatus


MOCK_CLAIM = (
    "Innovative Proof Reuse and Revocation: Existing solutions utilize W3C "
    "[47], [107], [109], [110] standards for VCs, VP generation, and revocation."
)

MOCK_REFERENCES = """[47] "Verifiable credentials data model v1.1," W3C. Accessed: Aug. 9, 2024. [Online]. Available: https://www.w3.org/TR/vc-data-model/
[107] "Verifiable credentials data model v2.0," W3C. Accessed: Aug. 9, 2024. [Online]. Available: https://www.w3.org/TR/vc-data-model-2.0/
[108] "General data protection regulation (GDPR)," European Union, 2018. Accessed: Mar. 23, 2025. [Online]. Available: https://gdpr.eu/
[109] "Verifiable credentials overview," W3C. Accessed: Aug. 9, 2024. [Online]. Available: https://www.w3.org/TR/vc-overview/
[110] "Verifiable credentials bitstring status list," W3C. Accessed: Aug. 9, 2024. [Online]. Available: https://www.w3.org/TR/vc-bitstring-status-list/
"""


def mock_analysis_rows() -> list[AnalysisRow]:
    """Return four rows; uncited reference [108] is intentionally absent."""

    references = {
        47: "[47] Verifiable credentials data model v1.1",
        107: "[107] Verifiable credentials data model v2.0",
        109: "[109] Verifiable credentials overview",
        110: "[110] Verifiable credentials bitstring status list",
    }
    scores = {47: 0.82, 107: 0.80, 109: 0.55, 110: 0.77}

    rows: list[AnalysisRow] = []
    for index, ref_number in enumerate((47, 107, 109, 110), start=1):
        pair = CitationPair(
            pair_id=f"pair_{index:03d}",
            claim_text=MOCK_CLAIM,
            citation_marker=f"[{ref_number}]",
            ref_number=ref_number,
            ref_raw=references[ref_number],
            is_compound=True,
        )
        method_a = MethodResult(
            support_score=scores[ref_number],
            evidence_basis=EvidenceBasis.ABSTRACT,
            source_status=SourceStatus.GOT_ABSTRACT,
        )
        rows.append(AnalysisRow(pair=pair, method_a=method_a, method_b=None))
    return rows

