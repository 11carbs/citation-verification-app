"""Input and pairing diagnostics shown without aborting usable results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    severity: IssueSeverity


def validate_required_inputs(claim_text: str, reference_text: str) -> list[ValidationIssue]:
    """Check only conditions that prevent citation pairing from starting."""

    issues: list[ValidationIssue] = []
    if not claim_text.strip():
        issues.append(
            ValidationIssue(
                code="EMPTY_CLAIM",
                message="請先貼上欲比對的段落。",
                severity=IssueSeverity.ERROR,
            )
        )
    if not reference_text.strip():
        issues.append(
            ValidationIssue(
                code="EMPTY_REFERENCES",
                message="請先貼上參考文獻清單。",
                severity=IssueSeverity.ERROR,
            )
        )
    return issues

