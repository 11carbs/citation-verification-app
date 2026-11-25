"""Versioned proposed person-one -> person-two contract, independent of numeric styles."""
from dataclasses import asdict, dataclass
from typing import Literal


@dataclass(frozen=True)
class ReferenceCandidate:
    ref_id: str
    raw_text: str
    title: str = ""
    authors: tuple[str, ...] = ()
    year: str = ""
    doi: str = ""
    url: str = ""


@dataclass(frozen=True)
class CitationHandoff:
    document_id: str
    claim_id: str
    pair_id: str
    citation_group_id: str
    claim_text: str
    context: str
    citation_marker: str
    block_ids: tuple[str, ...]
    match_status: Literal["matched", "missing_reference", "ambiguous", "unparsed"]
    reference: ReferenceCandidate | None = None
    candidate_ref_ids: tuple[str, ...] = ()
    error_message: str = ""
    schema_version: str = "citation-handoff.v1"

    def __post_init__(self):
        if self.match_status not in {"matched", "missing_reference", "ambiguous", "unparsed"}:
            raise ValueError("Unknown match_status")
        if self.match_status == "matched" and self.reference is None:
            raise ValueError("Matched pairs require a reference")
        if self.match_status != "matched" and self.reference is not None:
            raise ValueError("Unresolved pairs must not select a reference")
        if self.match_status == "ambiguous" and len(self.candidate_ref_ids) < 2:
            raise ValueError("Ambiguous pairs require at least two candidates")

    def to_dict(self):
        return asdict(self)
