"""Join extracted IEEE citations to the user's pasted reference list."""

from __future__ import annotations

from dataclasses import dataclass

from src.citation_parser import extract_claim_citations
from src.reference_parser import parse_reference_list
from src.schemas import CitationPair, ReferenceMatchStatus
from src.validation import IssueSeverity, ValidationIssue


@dataclass(frozen=True, slots=True)
class PairingOutcome:
    pairs: list[CitationPair]
    issues: list[ValidationIssue]


def build_citation_pairs(claim_text: str, reference_text: str) -> list[CitationPair]:
    """Create one traceable pair for each cited source in each claim segment."""

    return build_pairing_outcome(claim_text, reference_text).pairs


def build_pairing_outcome(claim_text: str, reference_text: str) -> PairingOutcome:
    """Return usable pairs plus non-fatal no-citation, duplicate and missing issues."""

    references = parse_reference_list(reference_text)
    pairs: list[CitationPair] = []
    issues: list[ValidationIssue] = []
    claims = extract_claim_citations(claim_text)

    if not claims:
        issues.append(
            ValidationIssue(
                code="NO_CITATIONS",
                message="段落中沒有偵測到可解析的 IEEE 數字引用。",
                severity=IssueSeverity.WARNING,
            )
        )

    for claim in claims:
        for duplicate_number in claim.duplicate_numbers:
            issues.append(
                ValidationIssue(
                    code="DUPLICATE_CITATION",
                    message=(
                        f"同一引用陳述重複出現 [{duplicate_number}]，"
                        "已合併為一筆查核項目。"
                    ),
                    severity=IssueSeverity.WARNING,
                )
            )
        for mention in claim.mentions:
            reference = references.get(mention.ref_number)
            if reference is None:
                status = ReferenceMatchStatus.MISSING_REFERENCE
                ref_raw = ""
                error_message = f"ref 清單找不到 [{mention.ref_number}]"
                issues.append(
                    ValidationIssue(
                        code="MISSING_REFERENCE",
                        message=error_message,
                        severity=IssueSeverity.WARNING,
                    )
                )
            else:
                status = ReferenceMatchStatus.MATCHED
                ref_raw = reference.raw_text
                error_message = ""

            pairs.append(
                CitationPair(
                    pair_id=f"pair_{len(pairs) + 1:03d}",
                    claim_text=claim.claim_text,
                    citation_marker=mention.marker,
                    ref_number=mention.ref_number,
                    ref_raw=ref_raw,
                    is_compound=claim.is_compound,
                    match_status=status,
                    error_message=error_message,
                )
            )
    return PairingOutcome(pairs=pairs, issues=issues)

