"""第二人：文獻查詢與摘要取得，實作 ``ReferenceLookupAdapter``.

查詢順序：Semantic Scholar 用 DOI → arXiv ID → 篇名精確比對 → 篇名關聯查詢 → OpenAlex（DOI、篇名）。

來源狀態：
- 拿到摘要：找到這篇且有摘要
- 有文獻但沒摘要：找到這篇但資料庫沒提供摘要
- 查不到：兩邊都查無，或 ref 沒有 DOI、arXiv ID 也沒有篇名
- 查詢失敗：過程中被限流或連不上，沒有查成功。這不代表文獻不存在

金鑰都可省略，但都建議給：
- Semantic Scholar 不給金鑰也能用，容易被限流（HTTP 429）。金鑰以 x-api-key 標頭送出，
  核發的額度是 1 RPS 且跨所有 endpoint 累計，RequestsClient 的間隔已設 1.2 秒
- OpenAlex 自 2026-02-13 起改為金鑰制，polite pool 已退場。沒有金鑰每天只有
  100 credits，超過就會被擋；免費金鑰可拉到 100,000。沒給金鑰仍會嘗試查詢

在 app.py 裡用法：
    ReferenceLookupAdapter(
        s2_key=st.secrets.get("S2_API_KEY"),
        openalex_key=st.secrets.get("OPENALEX_API_KEY"),
    )
"""

from __future__ import annotations

from difflib import SequenceMatcher
import re
import time
from typing import Protocol

import requests

from src.adapters import ReferenceLookupResult
from src.reference_metadata import ReferenceMetadata, extract_reference_metadata
from src.schemas import CitationPair, SourceStatus

S2_BASE = "https://api.semanticscholar.org/graph/v1"
OPENALEX_BASE = "https://api.openalex.org"
S2_FIELDS = "title,abstract,externalIds"
S2_ARXIV_PREFIX = "ARXIV:"
OPENALEX_SELECT = "id,doi,display_name,abstract_inverted_index"

TITLE_MATCH = 0.90  # 用篇名查到的論文，篇名相似度要到這個門檻才算同一篇
DOI_TITLE_MISMATCH = 0.60  # DOI 對到的篇名低於這個相似度 → 視為 DOI 可能寫錯

# (HTTP status, JSON, error text); status None means the request never succeeded.
HttpResult = tuple[int | None, dict | None, str]


class HttpClient(Protocol):
    def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> HttpResult:
        ...


def normalize_title(title: str) -> str:
    title = re.sub(r"[^a-z0-9]+", " ", (title or "").lower())
    return " ".join(title.split())


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def rebuild_openalex_abstract(inverted: dict | None) -> str:
    """OpenAlex returns {word: [positions]}; rebuild the plain abstract."""

    if not inverted:
        return ""
    positions = {pos: word for word, indices in inverted.items() for pos in indices}
    return " ".join(positions[pos] for pos in sorted(positions))


class RequestsClient:
    """requests wrapper with timeout, retry on 429/5xx and a minimum call interval."""

    # Semantic Scholar 核發金鑰的信寫「1 request per second, cumulative across all
    # endpoints」並要求 set your rate limit to below this threshold，所以留 20% 餘裕，
    # 不要剛好卡在 1.0。這個間隔是全域的，涵蓋 Semantic Scholar 與 OpenAlex 的呼叫。
    def __init__(self, min_interval: float = 1.2, retries: int = 3, timeout: float = 15) -> None:
        self.session = requests.Session()
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self._last_call = 0.0

    def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> HttpResult:
        error = ""
        for attempt in range(self.retries):
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            try:
                response = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                error = f"連線失敗：{type(exc).__name__}"
                time.sleep(2**attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                error = f"HTTP {response.status_code}"
                time.sleep(2**attempt)
                continue
            try:
                data = response.json()
            except ValueError:
                data = None
            return response.status_code, data, "" if response.ok else f"HTTP {response.status_code}"
        return None, None, error


class _Lookup:
    """One lookup run for one reference; collects notes along the way."""

    def __init__(self, http: HttpClient, meta: ReferenceMetadata, s2_key: str | None,
                 openalex_key: str | None) -> None:
        self.http = http
        self.meta = meta
        self.s2_headers = {"x-api-key": s2_key} if s2_key else None
        self.openalex_key = openalex_key
        self.notes: list[str] = []
        self.found_without_abstract = False
        self.had_error = False

    # ---- result helpers ----
    def _result(self, status: SourceStatus, abstract: str = "", source: str = "") -> dict:
        if status is SourceStatus.NOT_FOUND and self.had_error:
            # 沒查成功 ≠ 文獻不存在，評估時這兩種不能混在一起算
            self.notes.append("過程中有連線或限流錯誤，沒有查成功，不代表文獻不存在")
            status = SourceStatus.LOOKUP_FAILED
        return {"source_status": status, "abstract": abstract, "abstract_source": source}

    # ---- Semantic Scholar ----
    def s2_by_arxiv(self) -> dict | None:
        code, paper, error = self.http.get(
            f"{S2_BASE}/paper/{S2_ARXIV_PREFIX}{self.meta.arxiv_id}", {"fields": S2_FIELDS}, self.s2_headers
        )
        if code == 200 and paper:
            if paper.get("abstract"):
                self.notes.append("Semantic Scholar 用 arXiv ID 找到")
                return self._result(SourceStatus.GOT_ABSTRACT, paper["abstract"], "semantic_scholar")
            self.found_without_abstract = True
            self.notes.append("Semantic Scholar 用 arXiv ID 找到文獻，但沒有摘要")
        elif code == 404:
            self.notes.append("Semantic Scholar 查無此 arXiv ID")
        else:
            self.had_error = True
            self.notes.append(f"Semantic Scholar arXiv 查詢失敗（{error}）")
        return None

    def s2_by_doi(self) -> dict | None:
        code, paper, error = self.http.get(
            f"{S2_BASE}/paper/DOI:{self.meta.doi}", {"fields": S2_FIELDS}, self.s2_headers
        )
        if code == 200 and paper:
            if self.meta.title and paper.get("title") and \
                    title_similarity(self.meta.title, paper["title"]) < DOI_TITLE_MISMATCH:
                self.notes.append(f"DOI 對到的篇名與 ref 不同（「{paper['title']}」），DOI 可能寫錯，改用篇名查")
                return None
            if paper.get("abstract"):
                self.notes.append("Semantic Scholar 用 DOI 找到")
                return self._result(SourceStatus.GOT_ABSTRACT, paper["abstract"], "semantic_scholar")
            self.found_without_abstract = True
            self.notes.append("Semantic Scholar 用 DOI 找到文獻，但沒有摘要")
        elif code == 404:
            self.notes.append("Semantic Scholar 查無此 DOI")
        else:
            self.had_error = True
            self.notes.append(f"Semantic Scholar DOI 查詢失敗（{error}）")
        return None

    def s2_by_relevance(self) -> dict | None:
        """search/match 要求篇名幾乎一字不差；PDF 把連字號吃掉時會整個查無。

        例：ref 寫 ``KYCbased``、實際是 ``KYC-based``。改用 relevance search
        撈回幾筆，再用同一道相似度門檻過濾，不會放寬判斷標準。
        """

        code, data, error = self.http.get(
            f"{S2_BASE}/paper/search",
            {"query": self.meta.title, "fields": S2_FIELDS, "limit": 5},
            self.s2_headers,
        )
        if code != 200:
            if code != 404:
                self.had_error = True
                self.notes.append(f"Semantic Scholar 關聯查詢失敗（{error}）")
            return None
        papers = (data or {}).get("data") or []
        best = max(papers, default=None,
                   key=lambda w: title_similarity(self.meta.title, w.get("title") or ""))
        if not best:
            self.notes.append("Semantic Scholar 關聯查詢無結果")
            return None
        similarity = title_similarity(self.meta.title, best.get("title") or "")
        if similarity < TITLE_MATCH:
            self.notes.append(
                f"Semantic Scholar 關聯查詢最接近的篇名不符（相似度 {similarity:.2f}：「{best.get('title')}」）"
            )
            return None
        if best.get("abstract"):
            self.notes.append(f"Semantic Scholar 關聯查詢找到（相似度 {similarity:.2f}）")
            return self._result(SourceStatus.GOT_ABSTRACT, best["abstract"], "semantic_scholar")
        self.found_without_abstract = True
        self.notes.append(f"Semantic Scholar 關聯查詢找到文獻（相似度 {similarity:.2f}），但沒有摘要")
        return None

    def s2_by_title(self) -> dict | None:
        code, data, error = self.http.get(
            f"{S2_BASE}/paper/search/match", {"query": self.meta.title, "fields": S2_FIELDS}, self.s2_headers
        )
        # data 可能是 {"data": []}，不可直接取 [0]，否則整條 pipeline 會 IndexError
        matches = (data or {}).get("data") or []
        paper = matches[0] if code == 200 and matches else None
        if paper:
            similarity = title_similarity(self.meta.title, paper.get("title") or "")
            if similarity < TITLE_MATCH:
                self.notes.append(
                    f"Semantic Scholar 最接近的篇名不符（相似度 {similarity:.2f}：「{paper.get('title')}」）"
                )
                return None
            if paper.get("abstract"):
                self.notes.append(f"Semantic Scholar 用篇名找到（相似度 {similarity:.2f}）")
                return self._result(SourceStatus.GOT_ABSTRACT, paper["abstract"], "semantic_scholar")
            self.found_without_abstract = True
            self.notes.append(f"Semantic Scholar 用篇名找到文獻（相似度 {similarity:.2f}），但沒有摘要")
        elif code in (200, 404):
            self.notes.append("Semantic Scholar 用篇名查無結果")
        else:
            self.had_error = True
            self.notes.append(f"Semantic Scholar 篇名查詢失敗（{error}）")
        return None

    # ---- OpenAlex ----
    def openalex(self) -> dict | None:
        if not self.openalex_key:
            self.notes.append("沒有 OpenAlex 金鑰，仍嘗試查詢（每日 100 credits 上限）")
        key = {"api_key": self.openalex_key} if self.openalex_key else {}
        attempts = []
        if self.meta.doi:
            attempts.append(("DOI", f"{OPENALEX_BASE}/works/doi:{self.meta.doi}",
                             {**key, "select": OPENALEX_SELECT}))
        if self.meta.title:
            attempts.append(("篇名", f"{OPENALEX_BASE}/works",
                             {**key, "search": self.meta.title,
                              "per_page": 5, "select": OPENALEX_SELECT}))
        for how, url, params in attempts:
            code, data, error = self.http.get(url, params)
            work = None
            if code == 200 and data:
                if how == "DOI":
                    work = data
                else:
                    work = max(data.get("results") or [], default=None,
                               key=lambda w: title_similarity(self.meta.title, w.get("display_name") or ""))
            if work:
                name = work.get("display_name") or ""
                threshold = TITLE_MATCH if how == "篇名" else DOI_TITLE_MISMATCH
                if self.meta.title and title_similarity(self.meta.title, name) < threshold:
                    self.notes.append(f"OpenAlex 用{how}找到的篇名不符（「{name}」）")
                    continue
                abstract = rebuild_openalex_abstract(work.get("abstract_inverted_index"))
                if abstract:
                    self.notes.append(f"OpenAlex 用{how}找到")
                    return self._result(SourceStatus.GOT_ABSTRACT, abstract, "openalex")
                self.found_without_abstract = True
                self.notes.append(f"OpenAlex 用{how}找到文獻，但沒有摘要")
                return None
            if code in (200, 404):
                self.notes.append(f"OpenAlex 用{how}查無結果")
            else:
                self.had_error = True
                self.notes.append(f"OpenAlex {how}查詢失敗（{error}）")
        return None

    def run(self) -> dict:
        if not self.meta.has_identifier and not self.meta.title:
            self.notes.append("ref 裡沒有 DOI、arXiv ID，也抓不到篇名，無法查詢")
            return self._result(SourceStatus.NOT_FOUND)
        steps = []
        if self.meta.doi:
            steps.append(self.s2_by_doi)
        if self.meta.arxiv_id:
            steps.append(lambda: None if self.found_without_abstract else self.s2_by_arxiv())
        steps.append(lambda: None if (self.found_without_abstract or not self.meta.title) else self.s2_by_title())
        steps.append(lambda: None if (self.found_without_abstract or not self.meta.title) else self.s2_by_relevance())
        steps.append(self.openalex)
        for step in steps:
            result = step()
            if result:
                return result
        if self.found_without_abstract:
            return self._result(SourceStatus.NO_ABSTRACT)
        return self._result(SourceStatus.NOT_FOUND)


class ReferenceLookupAdapter:
    """Second-person implementation of ``src.adapters.ReferenceLookupAdapter``.

    Results are cached by ref number, so a reference cited by several claims is
    looked up only once per adapter instance.
    """

    def __init__(self, s2_key: str | None = None, openalex_key: str | None = None,
                 http: HttpClient | None = None) -> None:
        self.s2_key = s2_key or None
        self.openalex_key = openalex_key or None
        self.http = http or RequestsClient()
        self._cache: dict[int, ReferenceLookupResult] = {}

    def lookup(self, pair: CitationPair) -> ReferenceLookupResult:
        if pair.ref_number in self._cache:
            return self._cache[pair.ref_number]

        meta = extract_reference_metadata(pair.ref_raw)
        run = _Lookup(self.http, meta, self.s2_key, self.openalex_key)
        found = run.run()
        notes = ([meta.parse_note] if meta.parse_note else []) + run.notes
        status: SourceStatus = found["source_status"]
        result = ReferenceLookupResult(
            ref_number=pair.ref_number,
            title=meta.title,
            doi=meta.doi,
            url=meta.url,
            abstract=found["abstract"],
            source_status=status,
            error_message="；".join(notes)
            if status in (SourceStatus.NOT_FOUND, SourceStatus.LOOKUP_FAILED)
            else "",
            source_note="；".join(notes),
            abstract_source=found["abstract_source"],
        )
        self._cache[pair.ref_number] = result
        return result
