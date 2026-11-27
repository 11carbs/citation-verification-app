"""Orchestrate adapters without coupling the website to either implementation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from src.adapters import (
    JevAdapter,
    JevJudgmentResult,
    JudgmentStatus,
    ReferenceLookupAdapter,
    ReferenceLookupResult,
)
from src.evidence_selection import select_evidence
from src.reference_metadata import extract_reference_metadata
from src.source_evidence import EvidenceResolver
from src.schemas import (
    AnalysisRow,
    CitationPair,
    EvidenceBasis,
    MethodResult,
    ReferenceMatchStatus,
    SourceStatus,
    SupportBand,
)


def run_method_a_pipeline(
    pairs: list[CitationPair],
    reference_adapter: ReferenceLookupAdapter | None,
    jev_adapter: JevAdapter,
    *,
    prepare_reference: Callable[[CitationPair], ReferenceLookupResult] | None = None,
    evidence_resolver: EvidenceResolver | None = None,
) -> list[AnalysisRow]:
    """Read local metadata first and defer every abstract lookup until Q1 passes.

    傳 ``evidence_resolver`` 時改走現行架構第 5 點：先由第二人的取證層拿到同一份
    證據（全文優先、退摘要），再交給 adapter 判定。Q1 仍然會問，但它已經不決定
    要不要取證。不傳的話維持原本的分階段查摘要，既有呼叫端不受影響。
    """

    rows: list[AnalysisRow] = []
    batch_error = ""
    for pair in pairs:
        if pair.match_status is ReferenceMatchStatus.MISSING_REFERENCE:
            rows.append(AnalysisRow(pair=pair))
            continue

        if batch_error:
            reference = ReferenceLookupResult(pair.ref_number, "")
            judgment = JevJudgmentResult(status=JudgmentStatus.BATCH_STOPPED, error_message=batch_error)
        else:
            if evidence_resolver is not None:
                reference, judgment = _judge_with_evidence(pair, evidence_resolver, jev_adapter)
            else:
                reference, judgment = _judge_pair(
                    pair, reference_adapter, jev_adapter, prepare_reference,
                )
            if judgment.stop_batch:
                batch_error = judgment.error_message
        source_status = reference.source_status
        stopped = judgment.status is JudgmentStatus.STOPPED
        if stopped and source_status is SourceStatus.PENDING:
            source_status = SourceStatus.SKIPPED
        method_a = MethodResult(
            support_score=judgment.support_score,
            evidence_basis=judgment.evidence_basis,
            source_status=source_status,
            band_override=(
                SupportBand.UNSUPPORTED if stopped
                else SupportBand.UNVERIFIABLE
                if judgment.status is JudgmentStatus.UNVERIFIABLE
                else None
            ),
        )
        rows.append(
            AnalysisRow(
                pair=pair,
                method_a=method_a,
                judgment=judgment,
                reference=reference,
            )
        )
    return rows


def _judge_with_evidence(
    pair: CitationPair,
    resolver: EvidenceResolver,
    jev_adapter: JevAdapter,
) -> tuple[ReferenceLookupResult, JevJudgmentResult]:
    """先取證、再判定。取證失敗不可以變成判斷結果。"""

    try:
        evidence = resolver.resolve(pair)
    except Exception as error:
        # 取證層自己壞掉，與「這篇沒有開放存取全文」是兩回事，不可以混
        return ReferenceLookupResult(pair.ref_number, ""), JevJudgmentResult(
            status=JudgmentStatus.REFERENCE_ERROR,
            error_message=f"取得來源內容時發生錯誤（{type(error).__name__}）。",
        )
    metadata = extract_reference_metadata(pair.ref_raw)
    reference = evidence.reference or ReferenceLookupResult(pair.ref_number, metadata.title)
    if not reference.title.strip():
        # Q1 要篇名。取證層交回的紀錄沒有篇名時補上本機解析出來的
        reference = replace(reference, title=metadata.title)
    if reference.ref_number != pair.ref_number:
        # 取證層交回別筆 ref 的結果，拿去比對會產生假的「引用與來源不符」
        return ReferenceLookupResult(pair.ref_number, ""), JevJudgmentResult(
            status=JudgmentStatus.REFERENCE_ERROR,
            error_message=f"取得的來源 ref 編號（{reference.ref_number}）與這一筆不符。",
        )
    if evidence.unverifiable:
        # 全文與摘要都沒有。交給 adapter 決定：預設用篇名評分並標「判斷依據＝篇名」
        # （陳祖旭的做法），傳 title_only_scoring=False 時改標「無法查核」、不給分數。
        # 取不到的原因一定要跟著走，不可以在這裡丟掉。
        judgment = jev_adapter.judge(pair, reference)
        if evidence.note and not judgment.error_message:
            judgment = replace(judgment, error_message=evidence.note)
        return reference, judgment
    selected = select_evidence(
        pair.claim_text, evidence.text, source_label=evidence.basis.value,
    )
    judgment = jev_adapter.judge(
        pair, reference,
        evidence_text=selected.text_for_judgement(),
        evidence_basis=evidence.basis,
    )
    return reference, judgment


def _judge_pair(
    pair: CitationPair,
    reference_adapter: ReferenceLookupAdapter | None,
    jev_adapter: JevAdapter,
    prepare_reference: Callable[[CitationPair], ReferenceLookupResult] | None,
) -> tuple[ReferenceLookupResult, JevJudgmentResult]:
    try:
        if prepare_reference is None:
            metadata = extract_reference_metadata(pair.ref_raw)
            reference = ReferenceLookupResult(
                ref_number=pair.ref_number,
                title=metadata.title,
                doi=metadata.doi,
                url=metadata.url,
                source_note=metadata.parse_note,
            )
        else:
            reference = _validate_reference(pair, prepare_reference(pair))
    except Exception as error:
        return ReferenceLookupResult(pair.ref_number, ""), JevJudgmentResult(
            status=JudgmentStatus.REFERENCE_ERROR,
            error_message=f"無法讀取本筆書目資料（{type(error).__name__}）。",
        )
    if reference_adapter is None:
        judgment = jev_adapter.judge(pair, reference)
        if judgment.status is JudgmentStatus.UNVERIFIABLE and not judgment.error_message:
            # 這一輪根本沒接摘要查詢（離線批次、單元測試）。不寫清楚的話，
            # 輸出會是「無法查核 ＋ 來源狀態待查詢 ＋ 原因全空」，
            # 看的人分不出是查過查不到、還是沒查。
            judgment = replace(
                judgment,
                error_message="本次未接上摘要查詢，沒有可用的內容證據。",
            )
        return reference, judgment

    def lookup_abstract() -> ReferenceLookupResult:
        nonlocal reference
        try:
            found = _validate_reference(pair, reference_adapter.lookup(pair))
            reference = replace(
                found,
                title=reference.title or found.title,
                doi=reference.doi or found.doi,
                url=reference.url or found.url,
            )
        except Exception as error:
            reference = replace(
                reference,
                abstract="",
                abstract_source="",
                source_status=SourceStatus.LOOKUP_FAILED,
                error_message=f"摘要查詢失敗，無法確認摘要（{type(error).__name__}）。",
            )
        if reference.source_status is SourceStatus.NOT_FOUND and not reference.error_message:
            reference = replace(
                reference, error_message="摘要查詢未找到可確認的文獻。",
            )
        return reference

    judgment = jev_adapter.judge(pair, reference, lookup_abstract=lookup_abstract)
    return reference, judgment


def _validate_reference(pair: CitationPair, reference: ReferenceLookupResult) -> ReferenceLookupResult:
    """Validate the handoff before using evidence from another module."""
    if not isinstance(reference, ReferenceLookupResult):
        raise TypeError("Expected ReferenceLookupResult")
    if type(reference.ref_number) is not int or reference.ref_number != pair.ref_number:
        raise ValueError("Reference number does not match the citation pair")
    if not isinstance(reference.source_status, SourceStatus) or any(
        not isinstance(value, str)
        for value in (
            reference.title,
            reference.doi,
            reference.url,
            reference.abstract,
            reference.error_message,
            reference.source_note,
            reference.abstract_source,
        )
    ):
        raise TypeError("Invalid reference fields")
    has_abstract = bool(reference.abstract.strip())
    if reference.source_status is SourceStatus.GOT_ABSTRACT and not has_abstract:
        raise ValueError("The source reported an abstract but supplied none")
    if reference.source_status is SourceStatus.NO_ABSTRACT and has_abstract:
        raise ValueError("The source reported no abstract but supplied one")
    return reference

