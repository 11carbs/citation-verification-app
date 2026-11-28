"""第二人：把「取全文 → 退摘要 → 無可用證據」收成**一個**入口。

分工寫的是一句話：「自動搜尋並取得被引文獻的全文（找不到則取摘要）」「轉好的內容
暫存，**供兩個方法共用與重跑**」。所以這一層的責任是交出一份證據，不是把
``fulltext_sources`` 和 ``reference_lookup`` 兩個模組丟給判斷端自己接。

順序（現行架構第 3 點，2026-11-25 組內定）：

1. 開放存取全文 → ``EvidenceBasis.FULL_TEXT``
2. 拿不到全文就查摘要 → ``EvidenceBasis.ABSTRACT``
3. 兩邊都沒有 → ``EvidenceBasis.UNAVAILABLE``，由判斷端標「無法查核」、不給分數

**同一份書目只解析一次**（以書目當鍵，不是 ref 編號），方法 A（Jev）和方法 B
（一般 LLM）拿到的證據 ``==`` 相等。
證據不一致本身就是 11/24 A／B 分歧的來源之一，不可以讓兩邊各查一次。

``note`` 一定要同時寫出全文和摘要各自為什麼沒拿到，不可以併成一句「查詢失敗」：
下載失敗、限流、沒有開放存取、缺轉檔工具、文獻查不到是五件不同的事。

本模組不產生 ``SupportBand.UNVERIFIABLE``（「無法查核」），因為那是展示層的標籤，
在 ``fix/unverifiable-shared-evidence`` 分支上。這裡只誠實回報有沒有證據。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from src.adapters import ReferenceLookupResult
from src.evidence_retrieval import DEFAULT_TOP_K, RetrievalResult, find_evidence
from src.fulltext_sources import (
    _UNSET, FullTextCache, FullTextFetcher, FullTextResult, FullTextStatus, _Unset,
)
from src.reference_lookup import ReferenceLookupAdapter
from src.reference_matching import MatchDecision, match_reference
from src.reference_metadata import (
    ReferenceMetadata, extract_reference_metadata, reference_cache_key,
)
from src.schemas import CitationPair, EvidenceBasis, SourceStatus


@dataclass(frozen=True, slots=True)
class SourceEvidence:  # noqa: D101 - 見下方 docstring
    """一筆被引文獻實際拿到的證據，方法 A 與方法 B 共用這一份。"""

    ref_number: int
    text: str = ""
    basis: EvidenceBasis = EvidenceBasis.UNAVAILABLE
    source: str = ""        # arxiv_html / europe_pmc_xml / oa_pdf / semantic_scholar / openalex ...
    url: str = ""
    converter: str = ""     # 全文才有；摘要留空
    note: str = ""
    full_text: FullTextResult | None = None
    reference: ReferenceLookupResult | None = None
    # reused 不列入相等比較：方法 A 和方法 B 拿到的證據要能用 == 自我檢查是否一致
    reused: bool = field(default=False, compare=False)

    @property
    def has_evidence(self) -> bool:
        return self.basis is not EvidenceBasis.UNAVAILABLE and bool(self.text.strip())

    @property
    def unverifiable(self) -> bool:
        """沒有任何內容證據。判斷端據此標「無法查核」，不給分數。"""

        return not self.has_evidence


class EvidenceResolver:
    """取全文，拿不到就退摘要；同一筆 ref 只做一次。

    用法::

        resolver = EvidenceResolver.from_keys(
            openalex_key=st.secrets.get("OPENALEX_API_KEY"),
            s2_key=st.secrets.get("S2_API_KEY"),
        )
        evidence = resolver.resolve(pair)   # 方法 A
        evidence = resolver.resolve(pair)   # 方法 B 拿到同一份，不會再查一次
    """

    def __init__(self, fulltext: FullTextFetcher, lookup: ReferenceLookupAdapter,
                 *, match_references: bool = True) -> None:
        self.fulltext = fulltext
        self.lookup = lookup
        # 書目沒有 DOI／arXiv ID 時，把整條原文丟給 Crossref 比對，驗證過就補上
        # 識別碼與篇名。關掉的話就只靠本機 regex 解析（實測 7,294 筆語料只抽得到 26%）。
        self.match_references = match_references
        self._cache: dict[str, SourceEvidence] = {}
        self._match_cache: dict[str, MatchDecision] = {}

    @classmethod
    def from_keys(cls, *, openalex_key: str | None = None, s2_key: str | None = None,
                  cache: FullTextCache | None | _Unset = _UNSET,
                  http=None, scholar_cache: dict | None = None) -> EvidenceResolver:
        """正式接線用。``http`` 與 ``scholar_cache`` 要傳 app 的共用物件，
        否則 Semantic Scholar 的 429 冷卻與已取得的摘要不會跨 rerun 共用
        （見 ``docs/semantic-scholar-429.md``）。"""

        fetcher = (FullTextFetcher(openalex_key=openalex_key) if cache is _UNSET
                   else FullTextFetcher(openalex_key=openalex_key, cache=cache))
        lookup = ReferenceLookupAdapter(s2_key=s2_key, openalex_key=openalex_key,
                                        http=http, scholar_cache=scholar_cache)
        return cls(fetcher, lookup)

    def clear(self) -> None:
        """忘掉這次執行記住的證據。

        失敗也會被記住（否則方法 A、B 會各查一次，證據就不同了），所以限流或
        連線中斷之後要重試，必須先呼叫這個；全文的磁碟快取本來就不存失敗結果。
        """

        self._cache.clear()

    def retrieve(self, pair: CitationPair, *,
                 top_k: int = DEFAULT_TOP_K) -> tuple[SourceEvidence, RetrievalResult]:
        """取證據，再從裡面挑出與引用句相關的段落。

        判斷端只要呼叫這一個方法。回傳 ``(證據, 檢索結果)``：前者說證據是全文還是
        摘要、拿不拿得到；後者說在裡面找到什麼，以及哪些關鍵詞整篇都沒出現。
        證據是摘要時一樣會跑檢索，摘要短，通常整段都會被挑出來。
        """

        evidence = self.resolve(pair)
        # 一定要標明搜的是全文還是摘要：摘要上的「只有零星字詞相符」意義完全不同，
        # 摘要本來就不會覆蓋主張的細節
        label = evidence.basis.value if evidence.has_evidence else ""
        return evidence, find_evidence(pair.claim_text, evidence.text, top_k=top_k,
                                       source_label=label)

    def _match(self, raw: str, meta: ReferenceMetadata) -> tuple[ReferenceMetadata, str]:
        """沒有識別碼時用 Crossref 比對補上，比對結果一定要留下紀錄。

        只在「本機解析拿不到識別碼」時才送出，有 DOI 或 arXiv ID 就不浪費請求。
        """

        if not self.match_references or meta.has_identifier:
            return meta, ""
        key = " ".join(raw.split())
        decision = self._match_cache.get(key)
        if decision is None:
            try:
                decision = match_reference(raw, meta, self.lookup.http)
            except Exception as error:
                # 比對層壞掉不可以讓整筆取證失敗，退回本機解析就好
                return meta, f"書目比對發生錯誤（{type(error).__name__}）"
            self._match_cache[key] = decision
        if not decision.confirmed:
            return meta, f"書目比對未採用（{decision.status.value}；{decision.note}）"
        note = f"書目比對：{decision.note}"
        return replace(
            meta,
            title=meta.title or decision.title,
            doi=decision.doi or meta.doi,
        ), note

    def resolve(self, pair: CitationPair) -> SourceEvidence:
        meta = extract_reference_metadata(pair.ref_raw)
        meta, match_note = self._match(pair.ref_raw, meta)
        # 用書目當鍵，不可以用 ref 編號：不同論文的 [1] 是不同文獻，
        # 用編號當鍵會把別篇論文的全文拿來比對，產生假的「引用與來源不符」
        memo = reference_cache_key(meta) or "raw:" + " ".join(pair.ref_raw.split())
        cached = self._cache.get(memo)
        if cached is not None:
            return _reused(cached, pair.ref_number)

        full = self._fetch_full_text(meta)
        if full.ok:
            note = f"全文：{full.status.value}" + (f"（{full.note}）" if full.note else "")
            if match_note:
                note += "；" + match_note
            if full.cache_hit:
                note += "；沿用先前存下的全文"
            evidence = SourceEvidence(
                ref_number=pair.ref_number, text=full.markdown, basis=EvidenceBasis.FULL_TEXT,
                source=full.source, url=full.url, converter=full.converter,
                note=note, full_text=full,
            )
            self._cache[memo] = evidence
            return evidence

        reference = self._lookup_abstract(pair)
        notes = [_combined_note(full, reference)]
        if match_note:
            notes.append(match_note)
        if reference.ref_number != pair.ref_number:
            notes.append(f"⚠️ 查摘要回傳的 ref 編號（{reference.ref_number}）與這一筆（{pair.ref_number}）不符")
        if _status_value(reference.source_status) == SourceStatus.GOT_ABSTRACT.value \
                and reference.abstract.strip():
            if not reference.abstract_source:
                notes.append("⚠️ 摘要沒有附來源名稱，表格的摘要來源欄會是空的")
            evidence = SourceEvidence(
                ref_number=pair.ref_number, text=reference.abstract.strip(),
                basis=EvidenceBasis.ABSTRACT, source=reference.abstract_source,
                url=reference.abstract_url, note="；".join(notes),
                full_text=full, reference=reference,
            )
        else:
            evidence = SourceEvidence(
                ref_number=pair.ref_number, basis=EvidenceBasis.UNAVAILABLE,
                note="；".join(notes), full_text=full, reference=reference,
            )
        self._cache[memo] = evidence
        return evidence

    # ---- 兩個來源都不可以把例外丟給呼叫端 ----
    def _fetch_full_text(self, meta: ReferenceMetadata) -> FullTextResult:
        try:
            return self.fulltext.fetch(meta)
        except Exception as exc:  # noqa: BLE001 - 這一層的回傳要能直接進表格
            # 用 INTERNAL_ERROR 而不是 FETCH_FAILED：我們自己的 bug 不可以被算進
            # 「限流／連線失敗」那一桶，那一桶會被當成暫時性問題放過
            return FullTextResult(status=FullTextStatus.INTERNAL_ERROR,
                                  note=f"取全文時發生未預期的錯誤：{type(exc).__name__}")

    def _lookup_abstract(self, pair: CitationPair) -> ReferenceLookupResult:
        try:
            result = self.lookup.lookup(pair)
        except Exception as exc:  # noqa: BLE001
            return ReferenceLookupResult(
                ref_number=pair.ref_number, title="",
                source_status=SourceStatus.LOOKUP_FAILED,
                error_message=f"查摘要時發生未預期的錯誤：{type(exc).__name__}",
                source_note=f"查摘要時發生未預期的錯誤：{type(exc).__name__}",
            )
        if result is None:  # adapter 違約回了 None，要寫出真正的原因
            return ReferenceLookupResult(
                ref_number=pair.ref_number, title="",
                source_status=SourceStatus.LOOKUP_FAILED,
                error_message="查摘要的 adapter 回傳 None",
                source_note="查摘要的 adapter 回傳 None",
            )
        return result


def _status_value(status: object) -> str:
    """adapter 可能回純字串而不是 StrEnum（例如從 JSON 還原），不可以因此炸掉。"""

    return getattr(status, "value", str(status))


def _combined_note(full: FullTextResult, reference: ReferenceLookupResult) -> str:
    """全文和摘要各自為什麼沒拿到，要分開寫，不可以併成一句。"""

    parts = [f"全文：{_status_value(full.status)}" + (f"（{full.note}）" if full.note else "")]
    detail = reference.source_note or reference.error_message
    parts.append(f"摘要：{_status_value(reference.source_status)}" + (f"（{detail}）" if detail else ""))
    return "；".join(parts)


def _reused(evidence: SourceEvidence, ref_number: int) -> SourceEvidence:
    return SourceEvidence(
        ref_number=ref_number, text=evidence.text, basis=evidence.basis,
        source=evidence.source, url=evidence.url, converter=evidence.converter,
        note=evidence.note, full_text=evidence.full_text, reference=evidence.reference,
        reused=True,
    )
