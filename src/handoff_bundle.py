"""Validated, versioned person-one -> person-two handoff for whole papers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
from io import StringIO
import json

from src.document_conversion import ConvertedDocument
from src.document_handoff import CitationHandoff, ReferenceCandidate
from src.whole_paper import WholePaperOutcome


class HandoffValidationError(ValueError):
    """The extracted pairs cannot safely be sent to source retrieval."""


@dataclass(frozen=True)
class HandoffBundle:
    document_id: str
    filename: str
    source_format: str
    references: tuple[ReferenceCandidate, ...]
    ready_pairs: tuple[CitationHandoff, ...]
    review_pairs: tuple[CitationHandoff, ...]
    issues: tuple[dict[str, str], ...]
    schema_version: str = "citation-batch.v1"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json_bytes(self) -> bytes:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")


def build_handoff_bundle(document: ConvertedDocument,
                         outcome: WholePaperOutcome) -> HandoffBundle:
    """Validate links and split confirmed pairs from items needing review."""
    if document.document_id != outcome.document_id:
        raise HandoffValidationError("文件 ID 與配對結果不一致。")
    references = {reference.ref_id: reference for reference in outcome.references}
    if len(references) != len(outcome.references):
        raise HandoffValidationError("參考文獻 ID 重複，不能安全交接。")
    blocks = {block.block_id: block for block in document.blocks}
    if len(blocks) != len(document.blocks):
        raise HandoffValidationError("文件區塊 ID 重複。")

    def valid_locations(locations) -> bool:
        return bool(locations) and all(
            location.block_id in blocks
            and location.locator == blocks[location.block_id].locator
            and location.page == blocks[location.block_id].page
            and 1 <= location.line_start <= location.line_end <= len(
                blocks[location.block_id].markdown.splitlines()
            ) for location in locations
        )
    pair_ids: set[str] = set()
    claims: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    ready: list[CitationHandoff] = []
    review: list[CitationHandoff] = []
    used_refs: set[str] = set()

    for reference in outcome.references:
        if not reference.raw_text.strip():
            raise HandoffValidationError(f"{reference.ref_id} 缺少書目原文。")
        if not valid_locations(reference.source_locations):
            raise HandoffValidationError(f"{reference.ref_id} 的原文位置無效。")

    for pair in outcome.pairs:
        if pair.pair_id in pair_ids:
            raise HandoffValidationError(f"配對 ID 重複：{pair.pair_id}")
        pair_ids.add(pair.pair_id)
        if pair.document_id != document.document_id:
            raise HandoffValidationError(f"{pair.pair_id} 的文件 ID 不一致。")
        if not pair.claim_text.strip() or not pair.citation_marker.strip():
            raise HandoffValidationError(f"{pair.pair_id} 缺少引用陳述或標記。")
        if not pair.block_ids or any(block_id not in blocks for block_id in pair.block_ids):
            raise HandoffValidationError(f"{pair.pair_id} 的原文區塊無效。")
        if not valid_locations(pair.source_locations):
            raise HandoffValidationError(f"{pair.pair_id} 缺少有效原文位置。")
        if tuple(location.block_id for location in pair.source_locations) != pair.block_ids:
            raise HandoffValidationError(f"{pair.pair_id} 的位置與區塊 ID 不一致。")
        if pair.paragraph_char_start is not None and pair.paragraph_char_end is not None:
            if not 0 <= pair.paragraph_char_start < pair.paragraph_char_end <= len(pair.context):
                raise HandoffValidationError(f"{pair.pair_id} 的字元位置無效。")
            if pair.context[pair.paragraph_char_start:pair.paragraph_char_end] != pair.claim_text:
                raise HandoffValidationError(f"{pair.pair_id} 的原文範圍與引用陳述不一致。")
        claim_key = (pair.claim_text, pair.context, pair.block_ids)
        if pair.claim_id in claims and claims[pair.claim_id] != claim_key:
            raise HandoffValidationError(f"{pair.claim_id} 的一對多配對內容不一致。")
        claims[pair.claim_id] = claim_key
        if pair.citation_group_id != pair.claim_id:
            raise HandoffValidationError(f"{pair.pair_id} 的引用群組 ID 不一致。")

        if pair.match_status == "matched":
            if pair.reference is None or pair.reference.ref_id not in references:
                raise HandoffValidationError(f"{pair.pair_id} 的 ref 不在參考文獻清單。")
            if pair.reference != references[pair.reference.ref_id]:
                raise HandoffValidationError(f"{pair.pair_id} 的 ref 內容與清單不一致。")
            if pair.candidate_ref_ids or pair.error_message:
                raise HandoffValidationError(f"{pair.pair_id} 已配對但仍帶有待處理狀態。")
            used_refs.add(pair.reference.ref_id)
            ready.append(pair)
        else:
            if not pair.error_message:
                raise HandoffValidationError(f"{pair.pair_id} 缺少待處理原因。")
            if pair.match_status == "ambiguous":
                if len(set(pair.candidate_ref_ids)) < 2 or any(
                    ref_id not in references for ref_id in pair.candidate_ref_ids
                ):
                    raise HandoffValidationError(f"{pair.pair_id} 的歧義候選 ref 無效。")
                used_refs.update(pair.candidate_ref_ids)
            elif pair.candidate_ref_ids:
                raise HandoffValidationError(f"{pair.pair_id} 未配對卻帶有候選 ref。")
            review.append(pair)

    return HandoffBundle(
        document_id=document.document_id,
        filename=document.filename,
        source_format=document.source_format,
        references=tuple(ref for ref in outcome.references if ref.ref_id in used_refs),
        ready_pairs=tuple(ready),
        review_pairs=tuple(review),
        issues=tuple(asdict(issue) for issue in outcome.issues),
    )


def pairing_preview_rows(bundle: HandoffBundle) -> list[dict[str, str]]:
    """Human-readable rows shared by the on-screen preview and CSV export."""
    rows: list[dict[str, str]] = []
    for pair in sorted((*bundle.ready_pairs, *bundle.review_pairs), key=lambda item: item.pair_id):
        rows.append({
            "pair_id": pair.pair_id,
            "claim_id": pair.claim_id,
            "citation_marker": pair.citation_marker,
            "claim_text": pair.claim_text,
            "match_status": pair.match_status,
            "ref_id": pair.reference.ref_id if pair.reference else "",
            "ref_raw": pair.reference.raw_text if pair.reference else "",
            "candidate_ref_ids": "; ".join(pair.candidate_ref_ids),
            "source_locations": "; ".join(
                f"{location.locator}:line:{location.line_start}-{location.line_end}"
                for location in pair.source_locations
            ),
            "error_message": pair.error_message,
        })
    return rows


def pairing_preview_csv_bytes(bundle: HandoffBundle) -> bytes:
    fields = ("pair_id", "claim_id", "citation_marker", "claim_text", "match_status",
              "ref_id", "ref_raw", "candidate_ref_ids", "source_locations", "error_message")
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(pairing_preview_rows(bundle))
    return output.getvalue().encode("utf-8-sig")
