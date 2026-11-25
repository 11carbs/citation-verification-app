"""第二人：文獻查詢與摘要取得，實作 ``ReferenceLookupAdapter``.

Semantic Scholar 用 DOI、arXiv ID、篇名查詢；無可用摘要時依識別碼嘗試適用備援。
可由 ACL DOI 辨認出 Anthology 編號時，優先用該編號向 Semantic Scholar 查詢。

來源狀態：
- 拿到摘要：找到這篇且有摘要
- 有文獻但沒摘要：找到這篇但資料庫沒提供摘要
- 查不到：兩邊都查無，或 ref 沒有 DOI、arXiv ID 也沒有篇名
- 查詢失敗：過程中被限流或連不上，沒有查成功。這不代表文獻不存在

金鑰都可省略，但都建議給：
- Semantic Scholar 不給金鑰也能用，容易被限流（HTTP 429）。金鑰以 x-api-key 標頭送出，
  核發的額度是 1 RPS 且跨所有 endpoint 累計，RequestsClient 的間隔已設 1.2 秒
- OpenAlex 的 polite pool 已退場，改成金鑰加每日預算制（2026-11-26 查官方說明並實測金鑰）。
  免費金鑰每天 US$1，等於 10,000 credits，速率 30 req/s；沒有金鑰是 10 req/s、
  預算只有十分之一。用 DOI 直查（singleton）0 credits，用篇名查（search）每次 10 credits，
  所以額度幾乎都花在篇名查。沒給金鑰仍會嘗試查詢
  https://help.openalex.org/api/authentication/

在 app.py 裡用法：
    ReferenceLookupAdapter(
        s2_key=st.secrets.get("S2_API_KEY"),
        openalex_key=st.secrets.get("OPENALEX_API_KEY"),
    )
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
import re
from threading import Lock
import time
from typing import Protocol
from urllib.parse import quote, urlsplit

import requests

from src.adapters import ReferenceLookupResult
from src.abstract_sources import (
    ARXIV_ID, AbstractHTML, AbstractMarkup, OfficialAbstractHTML, abstract_problem,
    acl_anthology_id, clean_text, doi_value, official_web_source,
)
from src.reference_metadata import (
    ReferenceMetadata, extract_reference_metadata, reference_cache_key,
)
from src.schemas import CitationPair, SourceStatus
from src.w3c_sources import W3C, W3CPage, access_date, comparable_titles, history_names, report_path

S2_BASE = "https://api.semanticscholar.org/graph/v1"
OPENALEX_BASE = "https://api.openalex.org"
CROSSREF_BASE = "https://api.crossref.org/works"
EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
S2_FIELDS = "title,abstract,externalIds"
S2_ARXIV_PREFIX = "ARXIV:"
OPENALEX_SELECT = "id,doi,display_name,abstract_inverted_index,authorships,locations,publication_year"
ARXIV_DOI_PREFIX = "10.48550/arxiv."
DEFAULT_RETRY_AFTER = 8.0
_S2_HOST = urlsplit(S2_BASE).netloc
_HTML_HOSTS = {"aclanthology.org", "arxiv.org", "www.w3.org"}
_TRACE_HEADERS = {
    "date", "retry-after", "server", "content-type", "x-cache", "age", "via",
    "x-amz-apigw-id", "x-amzn-requestid", "x-amzn-errortype", "cf-ray",
    "x-request-id", "ratelimit", "ratelimit-policy", "ratelimit-limit",
    "ratelimit-remaining", "ratelimit-reset", "x-ratelimit-limit",
    "x-ratelimit-remaining", "x-ratelimit-reset", "x-rate-limit-limit", "x-rate-limit-interval",
}

TITLE_MATCH = 0.90  # 用篇名查到的論文，篇名相似度要到這個門檻才算同一篇
OPENALEX_TITLE_VARIANT_MATCH = 0.95
_TITLE_VERSION = re.compile(
    r"\bv(?:ersion)?\.?\s*(\d+(?:\.\d+)*)\b|(?<![\w.])(\d+\.\d+(?:\.\d+)*)\s*\)?$",
    re.IGNORECASE,
)
_AUTHOR_INITIALS = re.compile(r"^(?:[^\W\d_]\.\s*)+")

# (HTTP status, JSON, error text); status None means the request never succeeded.
HttpResult = tuple[int | None, dict | None, str]


class HttpClient(Protocol):
    def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> HttpResult:
        ...


def normalize_title(title: str) -> str:
    if not isinstance(title, str):
        return ""
    return " ".join("".join(char if char.isalnum() else " " for char in clean_text(title).casefold()).split())


def title_similarity(a: str, b: str) -> float:
    left, right = normalize_title(a), normalize_title(b)
    return SequenceMatcher(None, left, right).ratio() if left and right else 0.0


def _record(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _records(value: object) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _author_names(names: list[str]) -> tuple[str, ...]:
    """Ignore name punctuation and given/family order, keeping the author order."""
    normalized = tuple(" ".join(sorted(normalize_title(name).split())) for name in names)
    return normalized if all(normalized) else ()


def _first_author_matches(cited: str, indexed: object) -> bool:
    """Match full names and IEEE forms such as 'J. Devlin'."""
    cited_parts = normalize_title(cited).split()
    indexed_parts = normalize_title(indexed).split()
    if len(cited_parts) < 2 or len(indexed_parts) < 2 or len(cited_parts[-1]) < 2:
        return False
    if cited_parts == indexed_parts:
        return True
    prefix = _AUTHOR_INITIALS.match(cited)
    if prefix is None:
        return False
    initials = normalize_title(prefix[0]).split()
    family = cited_parts[len(initials):]
    if not family:
        return False
    given = indexed_parts[:-len(family)]
    return (
        family == indexed_parts[-len(family):]
        and len(given) >= len(initials)
        and all(initial == part[0] for initial, part in zip(initials, given))
    )


def _title_versions(title: str) -> set[str]:
    return {explicit or trailing for explicit, trailing in _TITLE_VERSION.findall(title)}


def _openalex_arxiv_ids(work: dict) -> set[str]:
    ids: set[str] = set()
    doi = doi_value(work.get("doi"))
    if doi.startswith(ARXIV_DOI_PREFIX):
        ids.add(doi.removeprefix(ARXIV_DOI_PREFIX))
    for location in _records(work.get("locations")):
        url = location.get("landing_page_url") or ""
        for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/"):
            if isinstance(url, str) and url.startswith(prefix):
                ids.add(url.removeprefix(prefix).lower())
    return {identifier for identifier in ids if ARXIV_ID.fullmatch(identifier)}


def rebuild_openalex_abstract(inverted: dict | None) -> str:
    """Rebuild an index only when the token sequence is contiguous and unambiguous."""

    if not isinstance(inverted, dict) or not inverted:
        return ""
    positions: dict[int, str] = {}
    for word, indices in inverted.items():
        if not isinstance(word, str) or not word.strip() or not isinstance(indices, list) or not indices:
            return ""
        for pos in indices:
            if type(pos) is not int or pos < 0 or pos in positions:
                return ""
            positions[pos] = word
    if max(positions, default=-1) != len(positions) - 1:
        return ""
    return " ".join(positions[pos] for pos in range(len(positions)))


def _retry_after_seconds(value: str) -> float:
    """Read either form of Retry-After, using a short delay if omitted or invalid."""
    try:
        value = value.strip()
        if value.isascii() and value.isdecimal():
            return float(int(value))
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, retry_at.timestamp() - time.time())
    except (ValueError, TypeError, OverflowError, OSError):
        return DEFAULT_RETRY_AFTER


@dataclass
class ScholarRequestSchedule:
    """Request timing shared by clients that use the same website process."""

    lock: Lock = field(default_factory=Lock, repr=False)
    last_call: float = -float("inf")
    cooldown_until: float = 0.0


SCHOLAR_REQUEST_SCHEDULE = ScholarRequestSchedule()
_ARXIV_SCHEDULE = ScholarRequestSchedule()


class RequestsClient:
    """Throttle requests and let the other provider take over after a 429."""

    # Semantic Scholar 核發金鑰的信寫「1 request per second, cumulative across all
    # endpoints」。每個 client 的請求保留 1.2 秒間隔，網站也跨 client 共用
    # Semantic Scholar 的請求時間。
    def __init__(
        self,
        min_interval: float = 1.2,
        retries: int = 3,
        timeout: float = 15,
        *,
        cooldowns: dict[str, float] | None = None,
        retry_scholar: bool = False,
        scholar_schedule: ScholarRequestSchedule | None = None,
        request_source: str = "local-app",
    ) -> None:
        self.session = requests.Session()
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self._last_call = -float("inf")
        self.cooldowns = cooldowns if cooldowns is not None else {}
        self._scholar_retry_available = retry_scholar
        self._scholar_schedule = scholar_schedule
        self.request_source = request_source
        self.attempts: list[dict] = []

    def _wait_for_scholar(self, host: str, delay: float) -> bool:
        if host != _S2_HOST or not self._scholar_retry_available or delay > DEFAULT_RETRY_AFTER:
            return False
        self._scholar_retry_available = False
        time.sleep(delay)
        return True

    def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> HttpResult:
        host = urlsplit(url).netloc
        schedule = self._scholar_schedule if host == _S2_HOST else _ARXIV_SCHEDULE if host == "arxiv.org" else None
        if schedule is not None:
            with schedule.lock:
                return self._request(url, host, params, headers, schedule)
        return self._request(url, host, params, headers)

    def _request(
        self,
        url: str,
        host: str,
        params: dict | None,
        headers: dict | None,
        schedule: ScholarRequestSchedule | None = None,
    ) -> HttpResult:
        cooldown_until = self.cooldowns.get(host, 0.0)
        if schedule is not None:
            cooldown_until = max(cooldown_until, schedule.cooldown_until)
        remaining = cooldown_until - time.monotonic()
        if remaining > 0 and not self._wait_for_scholar(host, remaining):
            self._trace(url, headers, params, sent=False, reason="cooldown_after_429")
            return None, None, "上一筆收到 HTTP 429，暫緩查詢"

        error = ""
        for attempt in range(self.retries):
            last_call = self._last_call
            if schedule is not None:
                last_call = max(last_call, schedule.last_call)
            interval = max(self.min_interval, 3.1) if host == "arxiv.org" else self.min_interval
            wait = interval - (time.monotonic() - last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            if schedule is not None:
                schedule.last_call = self._last_call
            event = self._trace(url, headers, params, sent=True)
            try:
                response = self.session.get(url, params=params, headers=headers, timeout=self.timeout,
                                            allow_redirects=False)
            except requests.RequestException as exc:
                error = f"連線失敗：{type(exc).__name__}"
                event.update(reason=type(exc).__name__, elapsed_ms=round((time.monotonic() - self._last_call) * 1000))
                if attempt + 1 < self.retries:
                    time.sleep(2**attempt)
                continue
            event.update(status=response.status_code, elapsed_ms=round((time.monotonic() - self._last_call) * 1000))
            prepared = response.request
            if isinstance(prepared, requests.PreparedRequest):
                if host == _S2_HOST:
                    event["key_attached"] = bool(prepared.headers.get("x-api-key"))
                    event["key_matches_configured"] = bool(self._configured_key_matches(prepared, headers))
            if isinstance(response.headers, Mapping):
                event["headers"] = {
                    name.lower(): self._redact(value, headers, params)
                    for name, value in response.headers.items() if name.lower() in _TRACE_HEADERS
                }
            if response.status_code == 429:
                delay = max(interval, _retry_after_seconds(response.headers.get("Retry-After", "")))
                event["cooldown_seconds"] = delay
                if isinstance(response.text, str):
                    event["body_excerpt"] = self._redact(response.text, headers, params)
                cooldown_until = time.monotonic() + delay
                self.cooldowns[host] = cooldown_until
                if schedule is not None:
                    schedule.cooldown_until = cooldown_until
                response.close()
                if attempt + 1 < self.retries and self._wait_for_scholar(host, delay):
                    continue
                return 429, None, "HTTP 429，資料庫要求暫停查詢"
            if response.status_code >= 500:
                error = f"HTTP {response.status_code}"
                if attempt + 1 < self.retries:
                    time.sleep(2**attempt)
                continue
            if (host in _HTML_HOSTS or official_web_source(url)) and response.ok:
                return response.status_code, {"html": response.text}, ""
            try:
                data = response.json()
            except ValueError:
                data = None
            return response.status_code, data, "" if response.ok else f"HTTP {response.status_code}"
        return None, None, error

    def _trace(self, url: str, headers: dict | None, params: dict | None, **details) -> dict:
        target = urlsplit(url)
        record = {
            "utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "source": self.request_source,
            "method": "GET",
            "endpoint": f"{target.scheme}://{target.netloc}{target.path}",
            "key_attached": bool((headers or {}).get("x-api-key") or (params or {}).get("api_key")),
            **details,
        }
        self.attempts.append(record)
        return record

    @staticmethod
    def _configured_key_matches(request: requests.PreparedRequest, headers: dict | None) -> bool:
        expected = (headers or {}).get("x-api-key")
        return bool(expected and request.headers.get("x-api-key") == expected)

    @staticmethod
    def _redact(value: str, headers: dict | None, params: dict | None) -> str:
        for secret in ((headers or {}).get("x-api-key"), (params or {}).get("api_key")):
            if secret:
                value = value.replace(secret, "[redacted]").replace(quote(secret, safe=""), "[redacted]")
        return " ".join(value.split())[:800]


class _Lookup:
    """One lookup run for one reference; collects notes along the way."""

    def __init__(self, http: HttpClient, meta: ReferenceMetadata, s2_key: str | None,
                 openalex_key: str | None, ref_raw: str) -> None:
        self.http = http
        self.meta = meta
        self.s2_headers = {"x-api-key": s2_key} if s2_key else None
        self.openalex_key = openalex_key
        self.ref_raw = ref_raw
        self.notes: list[str] = []
        self.found_without_abstract = False
        self.had_error = False
        self.scholar_unavailable = False
        self.publication_authors: tuple[str, ...] = ()

    # ---- result helpers ----
    def _result(self, status: SourceStatus, abstract: str = "", source: str = "", url: str = "") -> dict:
        if status is SourceStatus.NOT_FOUND and self.had_error:
            # 沒查成功 ≠ 文獻不存在，評估時這兩種不能混在一起算
            self.notes.append("過程中有連線或限流錯誤，沒有查成功，不代表文獻不存在")
            status = SourceStatus.LOOKUP_FAILED
        return {"source_status": status, "abstract": abstract, "abstract_source": source, "abstract_url": url}

    def _scholar(self, path: str, params: dict) -> HttpResult:
        code, data, error = self.http.get(f"{S2_BASE}/{path}", params, self.s2_headers)
        self.scholar_unavailable = code is None or code in (401, 403, 429) or code >= 500
        return code, data if isinstance(data, dict) else None, error

    def _usable_abstract(self, abstract: object, source: str) -> bool:
        problem = abstract_problem(abstract)
        if not problem:
            return True
        self.notes.append(f"{source} 有相符書目，但{problem}")
        self.found_without_abstract = True
        if problem == "摘要有截斷或展開提示":
            self.had_error = True
        return False

    def _note_indexed_abstract(self, abstract: str, label: str) -> None:
        short = "，且篇幅較短" if len(abstract.split()) < 40 and len(abstract) < 270 else ""
        self.notes.append(f"{label} 索引摘要無明顯截斷標記{short}，完整性未經官方摘要核對")

    # ---- Semantic Scholar ----
    def s2_by_arxiv(self) -> dict | None:
        path = f"paper/{S2_ARXIV_PREFIX}{self.meta.arxiv_id}"
        code, paper, error = self._scholar(path, {"fields": S2_FIELDS})
        if code == 200 and paper:
            ids = _record(paper.get("externalIds"))
            reported_arxiv = str(ids.get("ArXiv") or "")
            if reported_arxiv and reported_arxiv.casefold() != self.meta.arxiv_id.casefold():
                self.notes.append("Semantic Scholar 回傳的 arXiv ID 與書目不符")
                return None
            if self.meta.doi and self.meta.doi not in (
                f"10.48550/arxiv.{self.meta.arxiv_id.lower()}", doi_value(ids.get("DOI")),
            ):
                self.notes.append("Semantic Scholar 的 arXiv 紀錄未核對到書目指定的出版 DOI")
                return None
            if self.meta.title and title_similarity(self.meta.title, paper.get("title") or "") < TITLE_MATCH:
                self.notes.append("Semantic Scholar arXiv 紀錄的篇名與書目不符")
                return None
            if self._usable_abstract(paper.get("abstract"), "Semantic Scholar"):
                self.notes.append("Semantic Scholar 用 arXiv ID 找到")
                return self._result(SourceStatus.GOT_ABSTRACT, paper["abstract"], "semantic_scholar", f"{S2_BASE}/{path}")
        elif code == 404:
            self.notes.append("Semantic Scholar 查無此 arXiv ID")
        else:
            self.had_error = True
            self.notes.append(f"Semantic Scholar arXiv 查詢失敗（{error}）")
        return None

    def s2_by_doi(self) -> dict | None:
        # Semantic Scholar may use the arXiv DOI for a record that also has an ACL ID.
        acl_id = acl_anthology_id(self.meta.doi)
        if acl_id:
            result = self._s2_by_publication_id(f"ACL:{acl_id}", "ACL 編號")
            if result is not None or self.found_without_abstract or self.scholar_unavailable:
                return result
        return self._s2_by_publication_id(f"DOI:{self.meta.doi}", "DOI")

    def _s2_by_publication_id(self, paper_id: str, id_type: str) -> dict | None:
        path = f"paper/{paper_id}"
        code, paper, error = self._scholar(path, {"fields": S2_FIELDS})
        if code == 200 and paper:
            ids = _record(paper.get("externalIds"))
            reported_doi = doi_value(ids.get("DOI"))
            if paper_id.startswith("DOI:") and reported_doi and reported_doi != self.meta.doi:
                self.notes.append("Semantic Scholar 回傳的 DOI 與書目不符")
                return None
            reported_acl = str(ids.get("ACL") or "")
            if paper_id.startswith("ACL:") and reported_acl and reported_acl.casefold() != paper_id[4:].casefold():
                self.notes.append("Semantic Scholar 回傳的 ACL 編號與書目不符")
                return None
            reported_title = paper.get("title") or ""
            if self.meta.title and title_similarity(self.meta.title, reported_title) < TITLE_MATCH:
                self.notes.append(
                    f"{id_type} 對到的篇名缺少或與 ref 不同（「{reported_title}」），"
                    f"{id_type} 可能寫錯，改用其他方式查"
                )
                return None
            if self._usable_abstract(paper.get("abstract"), "Semantic Scholar"):
                self.notes.append(f"Semantic Scholar 用{id_type}找到")
                return self._result(SourceStatus.GOT_ABSTRACT, paper["abstract"], "semantic_scholar", f"{S2_BASE}/{path}")
        elif code == 404:
            self.notes.append(f"Semantic Scholar 查無此 {id_type}")
        else:
            self.had_error = True
            self.notes.append(f"Semantic Scholar {id_type} 查詢失敗（{error}）")
        return None

    def s2_by_title(self) -> dict | None:
        code, data, error = self._scholar("paper/search/match", {"query": self.meta.title, "fields": S2_FIELDS})
        # data 可能是 {"data": []}，不可直接取 [0]，否則整條 pipeline 會 IndexError
        matches = _records((data or {}).get("data"))
        paper = matches[0] if code == 200 and matches else None
        if paper:
            ids = _record(paper.get("externalIds"))
            retrieved_doi = doi_value(ids.get("DOI"))
            if self.meta.doi and self.meta.doi != retrieved_doi:
                self.notes.append("Semantic Scholar 以篇名找到的 DOI 缺少或與書目不符")
                return None
            if self.meta.arxiv_id and str(ids.get("ArXiv") or "").casefold() != self.meta.arxiv_id.casefold():
                self.notes.append("Semantic Scholar 以篇名找到的 arXiv ID 缺少或與書目不符")
                return None
            similarity = title_similarity(self.meta.title, paper.get("title") or "")
            if similarity < (TITLE_MATCH if self.meta.has_identifier else 1.0):
                self.notes.append(
                    f"Semantic Scholar 最接近的篇名不符（相似度 {similarity:.2f}：「{paper.get('title')}」）"
                )
                return None
            if self._usable_abstract(paper.get("abstract"), "Semantic Scholar"):
                self.notes.append(f"Semantic Scholar 用篇名找到（相似度 {similarity:.2f}）")
                paper_id = paper.get("paperId") or ""
                source_url = f"{S2_BASE}/paper/{quote(paper_id, safe='')}" if paper_id else f"{S2_BASE}/paper/search/match"
                return self._result(SourceStatus.GOT_ABSTRACT, paper["abstract"], "semantic_scholar", source_url)
        elif code in (200, 404):
            self.notes.append("Semantic Scholar 用篇名查無結果")
        else:
            self.had_error = True
            self.notes.append(f"Semantic Scholar 篇名查詢失敗（{error}）")
        return None

    def _verified_abstract(self, title: str, abstract: str, source: str, url: str) -> dict | None:
        if not title or (self.meta.title and title_similarity(self.meta.title, title) < TITLE_MATCH):
            self.notes.append(f"{source} 的篇名與書目不符或缺少篇名")
            return None
        if not self._usable_abstract(abstract, source):
            return None
        self.notes.append(f"{source} 書目核對通過，使用指定摘要欄位")
        return self._result(SourceStatus.GOT_ABSTRACT, abstract, source, url)

    def _fetch_backup(self, source: str, url: str, params: dict | None = None) -> dict:
        code, data, error = self.http.get(url, params)
        if code == 200 and isinstance(data, dict):
            return data
        if code != 404:
            self.had_error = True
        self.notes.append(f"{source} 無法取得紀錄（{error or code}）")
        return {}

    def _w3c_abstract(self, path: str) -> dict | None:
        names = history_names(path, self.meta.title)
        accessed = access_date(self.ref_raw)
        if names and accessed:
            archived = ""
            for name in names:
                url = f"{W3C}/standards/history/{name}/"
                data = self._fetch_backup("W3C 發布歷史", url)
                archived = W3CPage(data.get("html", "")).archived_at(names, accessed)
                if archived:
                    break
            if not archived:
                self.notes.append(f"W3C 無法確認 {accessed.isoformat()} 當時的發布版本")
                return None
            path = archived
            self.notes.append(f"依書目查閱日 {accessed.isoformat()} 選用當時已發布的版本")

        url = W3C + path
        data = self._fetch_backup("W3C", url)
        if not data:
            return None
        page = W3CPage(data.get("html", ""))
        titles = comparable_titles(self.meta.title, page.title, path)
        if not titles or title_similarity(*titles) < TITLE_MATCH:
            self.notes.append("W3C 官方頁的篇名或版本與書目不符")
            return None
        if not page.abstract:
            self.notes.append("W3C 官方頁沒有可辨識的 Abstract 區塊")
            return None
        self.notes.append("W3C 官方頁篇名與版本核對通過，使用 Abstract 區塊")
        return self._result(SourceStatus.GOT_ABSTRACT, page.abstract, "W3C", url)

    def _web_abstract(self, url: str, source: str) -> dict | None:
        data = self._fetch_backup(source, url)
        if not data:
            return None
        html = data.get("html")
        if not isinstance(html, str) or not html:
            self.notes.append(f"{source} 沒有回傳可查核的頁面")
            self.had_error = True
            return None
        page = OfficialAbstractHTML(html)
        if self.meta.doi and self.meta.doi not in {
            doi_value(page.metadata.get("citation_doi")), doi_value(page.metadata.get("dc.identifier")),
        }:
            self.notes.append(f"{source} 頁面的 DOI 缺少或與書目不符")
            self.had_error = True
            return None
        title = max(page.titles, key=lambda value: title_similarity(self.meta.title, value))
        return self._verified_abstract(title, page.explicit_abstract, source, url)

    def _official_abstract(self) -> dict | None:
        acl_id = acl_anthology_id(self.meta.doi)
        if acl_id:
            url = f"https://aclanthology.org/{acl_id}/"
            data = self._fetch_backup("ACL Anthology", url)
            if data:
                page = AbstractHTML(data.get("html", ""))
                if doi_value(page.metadata.get("citation_doi")) == self.meta.doi:
                    return self._verified_abstract(page.metadata.get("citation_title", ""), page.publisher_abstract,
                                                   "ACL Anthology", url)
                self.notes.append("ACL Anthology 的 DOI 缺少或與書目不符")

        arxiv_id = self.meta.arxiv_id or self.meta.doi.removeprefix(ARXIV_DOI_PREFIX)
        if not ARXIV_ID.fullmatch(arxiv_id):
            return None
        if self.meta.doi and self.meta.doi != ARXIV_DOI_PREFIX + arxiv_id.lower():
            self.notes.append("書目指定正式出版 DOI，arXiv 預印本須先核對出版書目")
            return None
        return self._arxiv_abstract(arxiv_id)

    def _arxiv_abstract(self, arxiv_id: str, *, verify_publication: bool = False) -> dict | None:
        url = f"https://arxiv.org/abs/{arxiv_id}"
        data = self._fetch_backup("arXiv", url)
        if not data:
            return None
        page = AbstractHTML(data.get("html", ""))
        published_id = page.metadata.get("citation_arxiv_id", "").lower()
        if published_id != arxiv_id.lower():
            self.notes.append("arXiv 的紀錄 ID 或版本與書目不符")
            return None
        title = page.metadata.get("citation_title", "")
        source = "arXiv"
        if verify_publication:
            if (title_similarity(self.meta.title, title) != 1.0
                    or _author_names(page.authors) != self.publication_authors):
                self.notes.append("arXiv 候選的篇名或完整作者順序與正式出版書目不符")
                return None
            source = "arXiv 預印本"
            self.notes.append("正式 DOI 書目與 arXiv 預印本的篇名及完整作者順序一致")
        return self._verified_abstract(title, clean_text(page.metadata.get("citation_abstract", "")), source, url)

    def _deposited_abstract(self) -> dict | None:
        if not self.meta.doi:
            return None
        url = f"{CROSSREF_BASE}/{quote(self.meta.doi, safe='/')}"
        data = self._fetch_backup("Crossref", url)
        record = _record(data.get("message"))
        if record and doi_value(record.get("DOI")) == self.meta.doi:
            titles = record.get("title")
            title = titles[0] if isinstance(titles, list) and titles else ""
            if title_similarity(self.meta.title, title) == 1.0:
                self.publication_authors = _author_names([
                    f"{author.get('given') or ''} {author.get('family') or ''}".strip()
                    if author.get("given") and author.get("family") else ""
                    for author in _records(record.get("author"))
                ])
            markup = record.get("abstract")
            abstract = AbstractMarkup(markup).text if isinstance(markup, str) else ""
            result = self._verified_abstract(title, abstract, "Crossref", url)
            if result:
                return result
            primary = _record(_record(record.get("resource")).get("primary"))
            publisher_url = primary.get("URL") or ""
            if (isinstance(publisher_url, str) and official_web_source(publisher_url) == "IEEE Xplore"
                    and publisher_url != self.meta.url):
                result = self._web_abstract(publisher_url, "IEEE Xplore")
                if result:
                    return result
        elif record:
            self.notes.append("Crossref 的 DOI 缺少或與書目不符")
        elif data:
            self.notes.append("Crossref 未提供可解析的書目紀錄")
            self.had_error = True

        data = self._fetch_backup("Europe PMC", EUROPE_PMC_SEARCH, {
            "query": f'DOI:"{self.meta.doi}"', "format": "json", "resultType": "core", "pageSize": 5,
        })
        results = _records(_record(data.get("resultList")).get("result"))
        for record in results:
            if doi_value(record.get("doi")) != self.meta.doi:
                continue
            source, accession = record.get("source"), record.get("id")
            if source not in ("MED", "PMC") or not isinstance(accession, str) or not accession.isalnum():
                continue
            url = f"https://europepmc.org/article/{source}/{accession}"
            markup = record.get("abstractText")
            abstract = AbstractMarkup(markup).text if isinstance(markup, str) else ""
            result = self._verified_abstract(record.get("title") or "", abstract, "Europe PMC", url)
            if result:
                return result
        if data:
            self.notes.append("Europe PMC 無可核對且有摘要的 DOI 紀錄")
        return None

    # ---- OpenAlex ----
    def _verify_openalex_title_variant(self, work: dict, name: str) -> bool:
        if _title_versions(self.meta.title) != _title_versions(name):
            self.notes.append(f"OpenAlex 近似篇名的版本與書目不符或缺少版本（「{name}」）")
            return False
        year = self.meta.publication_year
        if year is not None and work.get("publication_year") != year:
            self.notes.append(f"OpenAlex 近似篇名的出版年與書目不符或缺少年份（「{name}」）")
            return False
        authorships = _records(work.get("authorships"))
        first = authorships[0] if authorships else {}
        raw_name = first.get("raw_author_name")
        indexed_name = _record(first.get("author")).get("display_name")
        if first.get("author_position") in (None, "first") and (
            _first_author_matches(self.meta.first_author, raw_name)
            or _first_author_matches(self.meta.first_author, indexed_name)
        ):
            return True
        self.notes.append(f"OpenAlex 近似篇名無法核對第一作者（「{name}」）")
        return False

    def openalex(self) -> dict | None:
        if not self.openalex_key:
            self.notes.append("沒有 OpenAlex 金鑰，仍嘗試查詢（速率與每日預算都只有免費金鑰的一小部分）")
        doi = self.meta.doi
        if not doi and self.meta.arxiv_id:
            doi = ARXIV_DOI_PREFIX + self.meta.arxiv_id.lower()
        publication_doi = bool(doi and not doi.startswith(ARXIV_DOI_PREFIX))
        arxiv_candidates: set[str] = set()
        key = {"api_key": self.openalex_key} if self.openalex_key else {}
        attempts = []
        if doi:
            attempts.append(("DOI", f"{OPENALEX_BASE}/works/doi:{quote(doi, safe='/')}",
                             {**key, "select": OPENALEX_SELECT}))
        if self.meta.title:
            attempts.append(("篇名", f"{OPENALEX_BASE}/works",
                             {**key, "search": self.meta.title,
                              "per_page": 5, "select": OPENALEX_SELECT}))
        for how, url, params in attempts:
            code, data, error = self.http.get(url, params)
            candidates = []
            if code == 200 and isinstance(data, dict) and data:
                candidates = [data] if how == "DOI" else _records(data.get("results"))
            matches = []
            variants = []
            for work in candidates:
                name = work.get("display_name") or ""
                similarity = title_similarity(self.meta.title, name)
                if publication_doi and similarity == 1.0:
                    arxiv_candidates.update(_openalex_arxiv_ids(work))
                if doi and doi_value(work.get("doi")) != doi:
                    self.notes.append(f"OpenAlex 用{how}找到的 DOI 缺少或與書目不符")
                    continue
                threshold = TITLE_MATCH if doi else 1.0
                if self.meta.title and similarity < threshold:
                    if not doi and similarity >= OPENALEX_TITLE_VARIANT_MATCH:
                        if self._verify_openalex_title_variant(work, name):
                            variants.append(work)
                        continue
                    self.notes.append(f"OpenAlex 用{how}找到的篇名不符（「{name}」）")
                    continue
                matches.append(work)
            using_variant = not matches and bool(variants)
            matches = matches or variants
            if len(matches) > 1:
                self.notes.append(f"OpenAlex 用{how}找到多筆相符紀錄，無法唯一確認來源")
                self.had_error = True
                continue
            if matches:
                work = matches[0]
                if (publication_doi and not self.publication_authors
                        and title_similarity(self.meta.title, work.get("display_name")) == 1.0):
                    self.publication_authors = _author_names([
                        _record(entry.get("author")).get("display_name") or ""
                        for entry in _records(work.get("authorships"))
                    ])
                inverted = work.get("abstract_inverted_index")
                abstract = rebuild_openalex_abstract(inverted)
                if inverted and not abstract:
                    self.notes.append("OpenAlex 摘要索引缺少詞元或位置衝突，未採用")
                    self.had_error = True
                    continue
                if not self._usable_abstract(abstract, "OpenAlex"):
                    continue
                if using_variant:
                    similarity = title_similarity(self.meta.title, work.get("display_name") or "")
                    self.notes.append(f"OpenAlex 採用近似篇名（{similarity:.3f}），已核對第一作者及可用的出版年和版本")
                else:
                    self.notes.append(f"OpenAlex 用{how}找到相符紀錄")
                self._note_indexed_abstract(abstract, "OpenAlex")
                return self._result(SourceStatus.GOT_ABSTRACT, abstract, "openalex", work.get("id") or url)
            if code in (200, 404) and not candidates:
                self.notes.append(f"OpenAlex 用{how}查無結果")
            elif code not in (200, 404):
                self.had_error = True
                self.notes.append(f"OpenAlex {how}查詢失敗（{error}）")
        if publication_doi and self.meta.arxiv_id:
            arxiv_candidates = {self.meta.arxiv_id.lower()}
        if arxiv_candidates:
            if not self.publication_authors:
                self.notes.append("缺少已核對正式 DOI 的完整作者資料，未採用 arXiv 候選")
            elif len(arxiv_candidates) != 1:
                self.notes.append("找到多個相符篇名的 arXiv 候選，無法唯一確認預印本")
            else:
                arxiv_id = next(iter(arxiv_candidates))
                self.notes.append(f"找到 arXiv 候選 {arxiv_id}，查核官方頁")
                return self._arxiv_abstract(arxiv_id, verify_publication=True)
        return None

    def _scholar_abstract(self) -> dict | None:
        steps = []
        if self.meta.doi:
            steps.append(self.s2_by_doi)
        if self.meta.arxiv_id:
            steps.append(self.s2_by_arxiv)
        if self.meta.title:
            steps.append(self.s2_by_title)
        for step in steps:
            result = step()
            if result:
                return result
            if self.scholar_unavailable:
                self.notes.append("Semantic Scholar 暫不可用，改查其他來源")
                break
            if self.found_without_abstract:
                break
        return None

    def run(self) -> dict:
        standard_path = report_path(self.meta.url)
        if standard_path and self.meta.title and not self.meta.doi:
            # An explicit standards URL identifies the source more precisely than an academic title search.
            return self._w3c_abstract(standard_path) or self._result(SourceStatus.LOOKUP_FAILED)
        if not self.meta.has_identifier and not self.meta.title:
            self.notes.append("ref 裡沒有 DOI、arXiv ID，也抓不到篇名，無法查詢")
            return self._result(SourceStatus.NOT_FOUND)

        web_source = official_web_source(self.meta.url)
        if web_source:
            result = self._web_abstract(self.meta.url, web_source)
            if result:
                return result
            if not self.meta.has_identifier:
                status = SourceStatus.NO_ABSTRACT if self.found_without_abstract else SourceStatus.NOT_FOUND
                return self._result(status)

        scholar = self._scholar_abstract()
        official = self._official_abstract()
        if scholar:
            if official:
                if normalize_title(scholar["abstract"]) != normalize_title(official["abstract"]):
                    self.notes.append("Semantic Scholar 摘要與同版本官方摘要文字不同，採用官方摘要")
                    return official
                self.notes.append("Semantic Scholar 摘要與同版本官方摘要文字核對一致")
            else:
                self._note_indexed_abstract(scholar["abstract"], "Semantic Scholar")
            return scholar

        result = official or self._deposited_abstract() or self.openalex()
        if result:
            return result
        if self.had_error:
            self.notes.append("沒有取得可採用的備援摘要")
            return self._result(SourceStatus.LOOKUP_FAILED)
        if self.found_without_abstract:
            return self._result(SourceStatus.NO_ABSTRACT)
        return self._result(SourceStatus.NOT_FOUND)


def _memo_key(meta: ReferenceMetadata, ref_raw: str) -> str:
    """同一次執行內辨識「同一筆被引文獻」用的鍵；書目抽不出識別碼時退用原始字串。"""

    return reference_cache_key(meta) or "raw:" + " ".join(ref_raw.split())


class ReferenceLookupAdapter:
    """Second-person implementation of ``src.adapters.ReferenceLookupAdapter``.

    Results are cached by ref number, so a reference cited by several claims is
    looked up only once per adapter instance.
    A session cache can also retain successful Semantic Scholar abstracts by
    bibliographic metadata across separate runs.
    """

    def __init__(self, s2_key: str | None = None, openalex_key: str | None = None,
                 http: HttpClient | None = None, *,
                 scholar_cache: dict[ReferenceMetadata, ReferenceLookupResult] | None = None) -> None:
        self.s2_key = s2_key or None
        self.openalex_key = openalex_key or None
        self.http = http or RequestsClient()
        self._cache: dict[str, ReferenceLookupResult] = {}
        self._scholar_cache = scholar_cache if scholar_cache is not None else {}

    def lookup(self, pair: CitationPair) -> ReferenceLookupResult:
        meta = extract_reference_metadata(pair.ref_raw)
        # 用書目本身當鍵，不可以用 ref 編號：同一個 adapter 跑第二篇論文時，
        # 第二篇的 [1] 會拿到第一篇 [1] 的摘要
        memo = _memo_key(meta, pair.ref_raw)
        if memo in self._cache:
            cached = self._cache[memo]
            return replace(cached, ref_number=pair.ref_number, attempts=(),
                           source_note=f"{cached.source_note}；沿用本次查詢結果")

        cached = self._scholar_cache.get(meta)
        if cached is not None:
            result = replace(
                cached,
                ref_number=pair.ref_number,
                attempts=(),
                source_note=f"{cached.source_note}；沿用此工作階段先前取得的摘要",
            )
            self._cache[memo] = result
            return result

        run = _Lookup(self.http, meta, self.s2_key, self.openalex_key, pair.ref_raw)
        trace = getattr(self.http, "attempts", None)
        trace_start = len(trace) if isinstance(trace, list) else 0
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
            abstract_url=found["abstract_url"],
            attempts=tuple(trace[trace_start:]) if isinstance(trace, list) else (),
        )
        self._cache[memo] = result
        if (
            status is SourceStatus.GOT_ABSTRACT
            and result.abstract_source == "semantic_scholar"
            and result.abstract.strip()
        ):
            self._scholar_cache[meta] = result
        return result
