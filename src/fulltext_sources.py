"""第二人：被引文獻的開放存取全文取得與轉 Markdown（汪老師 11/24 第 2 點）。

取全文分三條路，依被引文獻的識別碼決定走哪一條：

1. arXiv ID → arXiv 官方 HTML 版（``arxiv.org/html/{id}``），沒有 HTML 版才退回 PDF
2. PubMed Central → Europe PMC 的 JATS XML（``/{PMCID}/fullTextXML``）
3. 其他 → 用 OpenAlex 查 OA 位置，**先試 landing page 的 HTML，再試 PDF**（分工要求
   優先使用 HTML、XML；HTML 不經過版面還原，比從 PDF 轉準確）

轉檔優先序是 XML > HTML > PDF，因為 XML 和 HTML 不經過版面還原，誤差最小：

- JATS XML：本模組自己解析（只用標準函式庫，沒有額外相依）
- HTML：有裝 trafilatura 就用，沒有就用內建的簡化解析
- PDF：有裝 docling 就用，沒有就回報「缺少轉檔工具」

**狀態一定要分開，不可以混成一種失敗。** 這是本專案反覆出過的錯：

- ``NO_OPEN_ACCESS``：查得到這篇，但沒有開放存取全文
- ``FETCH_FAILED``：連線、限流（429）或 HTTP 錯誤，**不代表沒有全文**
- ``CONVERT_FAILED``：檔案拿到了但轉不出文字
- ``NO_CONVERTER``：本機沒裝對應的轉檔工具，**不是文獻的問題**
- ``INTERNAL_ERROR``：我們自己的程式出錯，不可以和限流、連線失敗算在同一桶
- ``TOO_SHORT``：轉出來的內容短到不像全文（擋爬蟲頁、同意書頁），不可以當成拿到全文

拿不到全文時由呼叫端退回查摘要（``reference_lookup``），兩邊都沒有才標「無法查核」。

OpenAlex 額度（2026-11-26 實測免費金鑰）：每日 US$1 ＝ 10,000 credits。
用 DOI 直查 0 credits，用篇名查 10 credits，所以 ``FullTextCache`` 預設開著，
重跑評估時不要重查。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from typing import Protocol
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

import requests

from src.reference_lookup import TITLE_MATCH, title_similarity
from src.reference_metadata import ReferenceMetadata, reference_cache_key

OPENALEX_BASE = "https://api.openalex.org"
EUROPE_PMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
ARXIV_HTML = "https://arxiv.org/html"
ARXIV_PDF = "https://arxiv.org/pdf"
OPENALEX_FULLTEXT_SELECT = "id,doi,display_name,best_oa_location,locations"
ARXIV_API = "https://export.arxiv.org/api/query"
# 用篇名查 arXiv 時最多看前幾筆。arXiv 的 ti: 是詞組比對，相符的幾乎都在第一筆，
# 多看幾筆是為了副標題寫法不同的情況。
ARXIV_SEARCH_RESULTS = 5

# 單篇全文的大小上限；超過這個值多半是掃描檔或資料附錄，不是論文本文。
# 2026-11-26 調高：原本 25 MB，實測 112 筆被引文獻有 3 筆（2.7%）被它擋掉，
# 三筆都是 arXiv 上的視覺／圖學論文（33.7、34.2、36.4 MB），圖多所以檔案大，不是異常檔。
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DEFAULT_MIN_INTERVAL = 1.0
# arXiv 條款只寫「須遵守速率限制」，沒有公開數字；論壇與二手來源都說每 3 秒 1 次，取保守值
HOST_MIN_INTERVAL = {"arxiv.org": 3.1, "export.arxiv.org": 3.1}
DEFAULT_RETRY_AFTER = 8.0
MAX_TRACE_RECORDS = 500
DEFAULT_CACHE_FOLDER = Path(".cache/fulltext")
# 轉出來比這還短就不是全文，是擋爬蟲頁、同意書頁或 JS 版面殼。
# 這種東西長得像成功，但送去比對只會得到「找不到相關段落」，而且來源欄會寫「拿到全文」。
MIN_FULL_TEXT_CHARS = 1000
# 用篇名查 OpenAlex 時，回來的篇名要像到這個程度才算同一篇。與 reference_lookup 同一個門檻，
# 兩條路（摘要、全文）的證據標準必須一致，否則 Jev 和一般 LLM 拿到的東西會不一樣。
_PMCID = re.compile(r"\bPMC\d+\b", re.IGNORECASE)


class FullTextStatus(StrEnum):
    """全文取得的結果；失敗原因一定要分開，評估時不能混在一起算。"""

    GOT_FULL_TEXT = "拿到全文"
    NO_OPEN_ACCESS = "沒有開放存取全文"
    FETCH_FAILED = "全文下載失敗"
    CONVERT_FAILED = "全文轉檔失敗"
    NO_CONVERTER = "缺少轉檔工具"
    NO_IDENTIFIER = "書目沒有可查的識別碼或篇名"
    INTERNAL_ERROR = "程式內部錯誤"
    TOO_SHORT = "轉出來的內容不像全文"
    PENDING = "待查詢"


class _Unset:
    """區分「沒給 cache」（用預設快取）和「明寫 cache=None」（不要快取）。"""


_UNSET = _Unset()


@dataclass(frozen=True, slots=True)
class FullTextResult:
    """一筆被引文獻的全文取得結果。

    ``source`` 與 ``converter`` 是給評估用的：第三人要靠它們分辨
    「判斷錯」和「證據根本沒拿到」，不要省略。
    """

    status: FullTextStatus = FullTextStatus.PENDING
    markdown: str = ""
    source: str = ""        # arxiv_html / arxiv_pdf / europe_pmc_xml / oa_pdf / oa_html / manual_upload
    url: str = ""
    converter: str = ""     # jats / trafilatura / builtin_html / docling / passthrough
    note: str = ""
    attempts: tuple[dict, ...] = ()
    cache_hit: bool = False

    @property
    def ok(self) -> bool:
        return self.status is FullTextStatus.GOT_FULL_TEXT and bool(self.markdown.strip())


# --------------------------------------------------------------------------
# 下載層
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Download:
    """一次下載的結果；``status`` 為 None 表示請求從頭到尾沒成功。"""

    status: int | None = None
    content_type: str = ""
    body: bytes = b""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300 and bool(self.body)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def looks_like_pdf(self) -> bool:
        return self.body[:1024].lstrip()[:5].startswith(b"%PDF")

    @property
    def looks_like_html(self) -> bool:
        head = self.body[:1024].lstrip().lower()
        return head.startswith(b"<!doctype html") or head.startswith(b"<html")

    def json(self) -> dict:
        try:
            data = json.loads(self.text)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}


class Downloader(Protocol):
    def get(self, url: str, params: dict | None = None, *, accept: str = "") -> Download:
        ...


class RequestsDownloader:
    """照每個主機的速率限制下載；429 之後該主機進入冷卻，不再硬送。"""

    def __init__(self, *, timeout: float = 30.0, retries: int = 2,
                 min_interval: float = DEFAULT_MIN_INTERVAL,
                 max_bytes: int = MAX_DOWNLOAD_BYTES,
                 session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.retries = max(1, retries)
        self.min_interval = min_interval
        self.max_bytes = max_bytes
        self.session = session or requests.Session()
        self.attempts: list[dict] = []
        self._last_call: dict[str, float] = {}
        self._cooldown: dict[str, float] = {}

    def get(self, url: str, params: dict | None = None, *, accept: str = "") -> Download:
        host = urlsplit(url).netloc
        now = time.monotonic()
        if self._cooldown.get(host, 0.0) > now:
            self._trace(url, sent=False, reason="cooldown_after_429")
            return Download(error="上一筆收到 HTTP 429，暫緩下載")

        headers = {"Accept": accept} if accept else {}
        interval = HOST_MIN_INTERVAL.get(host, self.min_interval)
        error = ""
        for attempt in range(self.retries):
            wait = interval - (time.monotonic() - self._last_call.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last_call[host] = time.monotonic()
            event = self._trace(url, sent=True)
            try:
                response = self.session.get(url, params=params, headers=headers,
                                            timeout=self.timeout, allow_redirects=True, stream=True)
            except requests.RequestException as exc:
                error = f"連線失敗：{type(exc).__name__}"
                event.update(reason=type(exc).__name__)
                continue
            with response:
                event.update(status=response.status_code)
                if response.status_code == 429:
                    delay = max(interval, _retry_after_seconds(response.headers.get("Retry-After", "")))
                    self._cooldown[host] = time.monotonic() + delay
                    event["cooldown_seconds"] = delay
                    return Download(status=429, error="HTTP 429，資料庫要求暫停下載")
                if response.status_code >= 500:
                    error = f"HTTP {response.status_code}"
                    continue
                if not response.ok:
                    return Download(status=response.status_code, error=f"HTTP {response.status_code}")
                try:
                    body = self._read(response)
                except requests.RequestException as exc:
                    # 大檔讀到一半斷線是常態，不可以讓例外逃到呼叫端
                    error = f"讀取中斷：{type(exc).__name__}"
                    event.update(reason=type(exc).__name__)
                    continue
                if body is None:
                    event["reason"] = "too_large"
                    return Download(status=response.status_code,
                                    error=f"檔案超過 {self.max_bytes // (1024 * 1024)} MB 上限")
                event["bytes"] = len(body)
                return Download(status=response.status_code,
                                content_type=str(response.headers.get("Content-Type", "")),
                                body=body)
        return Download(error=error or "下載失敗")

    def _read(self, response: requests.Response) -> bytes | None:
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > self.max_bytes:
                return None
            chunks.append(chunk)
        return b"".join(chunks)

    def _trace(self, url: str, **details) -> dict:
        target = urlsplit(url)
        record = {"endpoint": f"{target.scheme}://{target.netloc}{target.path}", **details}
        self.attempts.append(record)
        if len(self.attempts) > MAX_TRACE_RECORDS:  # 整批跑幾百篇時不要無上限長大
            del self.attempts[:-MAX_TRACE_RECORDS]
        return record


def _retry_after_seconds(value: str) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER


# --------------------------------------------------------------------------
# 轉檔層
# --------------------------------------------------------------------------

def jats_to_markdown(xml_text: str) -> str:
    """把 JATS XML（Europe PMC fullTextXML）轉成 Markdown。

    只用標準函式庫，不經過版面還原，所以沒有 PDF 轉檔的斷行與雙欄問題。
    圖表內容不輸出，只留圖說，避免表格被攤平成雜訊。
    """

    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return ""
    for element in root.iter():
        if isinstance(element.tag, str) and "}" in element.tag:
            element.tag = element.tag.rsplit("}", 1)[1]

    lines: list[str] = []
    title = root.find(".//front//article-title")
    if title is not None:
        text = _element_text(title)
        if text:
            lines.append(f"# {text}")

    for abstract in root.findall(".//front//abstract"):
        lines.append("## Abstract")
        _emit_section(abstract, lines, level=3)

    body = root.find(".//body")
    if body is not None:
        _emit_section(body, lines, level=2)

    return "\n\n".join(line for line in lines if line.strip())


def _emit_section(node: ElementTree.Element, lines: list[str], *, level: int) -> None:
    for child in node:
        tag = child.tag
        if tag == "sec":
            heading = child.find("title")
            if heading is not None:
                text = _element_text(heading)
                if text:
                    lines.append(f"{'#' * min(level, 6)} {text}")
            _emit_section(child, lines, level=level + 1)
        elif tag == "p":
            text = _element_text(child)
            if text:
                lines.append(text)
        elif tag in ("list", "boxed-text"):
            _emit_section(child, lines, level=level)
        elif tag == "list-item":
            text = _element_text(child)
            if text:
                lines.append(f"- {text}")
        elif tag in ("fig", "table-wrap"):
            caption = child.find("caption")
            if caption is not None:
                text = _element_text(caption)
                if text:
                    lines.append(f"> {text}")
        elif tag == "title":
            continue


def _element_text(node: ElementTree.Element) -> str:
    return " ".join("".join(node.itertext()).split())


class _TextHTML(HTMLParser):
    """沒有 trafilatura 時的後備：丟掉腳本與版面元素，保留標題與段落。"""

    _DROP = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript"}
    _BLOCK = {"p", "div", "section", "article", "li", "br", "tr", "blockquote"}
    _HEADING = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._heading = 0
        self._parts: list[str] = []
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._DROP:
            self._depth += 1
        elif tag in self._HEADING:
            self._flush()
            self._heading = self._HEADING[tag]
        elif tag in self._BLOCK:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in self._DROP:
            self._depth = max(0, self._depth - 1)
        elif tag in self._HEADING or tag in self._BLOCK:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._depth == 0 and data.strip():
            self._buffer.append(data)

    def _flush(self) -> None:
        text = " ".join("".join(self._buffer).split())
        self._buffer.clear()
        if not text:
            self._heading = 0
            return
        self._parts.append(f"{'#' * self._heading} {text}" if self._heading else text)
        self._heading = 0

    @property
    def markdown(self) -> str:
        self._flush()
        return "\n\n".join(self._parts)


def html_to_markdown(html: str) -> tuple[str, str]:
    """回傳 (markdown, 用了哪個轉檔器)。有裝 trafilatura 就優先用。"""

    try:
        import trafilatura  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        extracted = trafilatura.extract(html, output_format="markdown", include_tables=True)
        if extracted and extracted.strip():
            return extracted.strip(), "trafilatura"
    parser = _TextHTML()
    parser.feed(html)
    return parser.markdown, "builtin_html"


def pdf_to_markdown(data: bytes, *, name: str = "source.pdf") -> tuple[str, str, str]:
    """用 docling 把 PDF 轉成 Markdown，回傳 (markdown, 轉檔器, 錯誤)。

    沒裝 docling 回 ("", "", "")；docling 自己炸掉（壞檔、加密 PDF、其實是登入頁）
    回 ("", "docling", 錯誤型別)，不可以讓例外逃到呼叫端。
    """

    try:
        from docling.document_converter import DocumentConverter  # type: ignore[import-not-found]
    except ImportError:
        return "", "", ""
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / name
            path.write_bytes(data)
            document = DocumentConverter().convert(str(path)).document
            return (document.export_to_markdown() or "").strip(), "docling", ""
    except Exception as exc:  # docling 的例外型別沒有公開保證，只能全攔
        return "", "docling", type(exc).__name__


# --------------------------------------------------------------------------
# 快取
# --------------------------------------------------------------------------

class FullTextCache:
    """把轉好的 Markdown 存成檔案，重跑評估時不要重新下載也不要重查 OpenAlex。"""

    def __init__(self, folder: Path | str | None = None) -> None:
        self.folder = Path(folder if folder is not None else DEFAULT_CACHE_FOLDER)

    def _path(self, key: str) -> Path:
        return self.folder / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()}.json"

    def get(self, key: str) -> FullTextResult | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or "status" not in record:
            return None
        try:
            status = FullTextStatus(record["status"])
        except ValueError:
            return None
        return FullTextResult(
            status=status,
            markdown=str(record.get("markdown", "")),
            source=str(record.get("source", "")),
            url=str(record.get("url", "")),
            converter=str(record.get("converter", "")),
            note=str(record.get("note", "")),
            cache_hit=True,
        )

    def put(self, key: str, result: FullTextResult) -> None:
        # 只快取成功的結果：失敗多半是限流或暫時性錯誤，存下來會讓下次永遠失敗
        if not result.ok:
            return
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self._path(key).write_text(json.dumps({
                "key": key,
                "status": result.status.value,
                "markdown": result.markdown,
                "source": result.source,
                "url": result.url,
                "converter": result.converter,
                "note": result.note,
            }, ensure_ascii=False), encoding="utf-8")
        except OSError:
            return


# --------------------------------------------------------------------------
# 取全文
# --------------------------------------------------------------------------

@dataclass(slots=True)
class _Run:
    """一次取全文的過程；沿路把做了什麼記下來，失敗時要看得出原因。"""

    notes: list[str] = field(default_factory=list)
    had_error: bool = False
    # 拿到了檔案但轉不出文字（缺工具或轉檔失敗）。這不是「沒有開放存取全文」，
    # 收尾時要優先用它當結論，否則本機沒裝 docling 會被誤報成文獻沒有全文。
    fallback: FullTextResult | None = None

    def note(self, text: str) -> None:
        self.notes.append(text)

    def keep(self, result: FullTextResult) -> None:
        if self.fallback is None:
            self.fallback = result

    @property
    def text(self) -> str:
        return "；".join(self.notes)


class FullTextFetcher:
    """依識別碼分流取得開放存取全文，轉成 Markdown。

    用法::

        fetcher = FullTextFetcher(openalex_key=st.secrets.get("OPENALEX_API_KEY"))
        result = fetcher.fetch(extract_reference_metadata(pair.ref_raw))
        if not result.ok:
            ...  # 退回 reference_lookup 查摘要
    """

    def __init__(self, openalex_key: str | None = None, *,
                 downloader: Downloader | None = None,
                 cache: FullTextCache | None | _Unset = _UNSET) -> None:
        self.openalex_key = openalex_key or None
        self.downloader = downloader or RequestsDownloader()
        # 預設就開快取：同一份全文要給方法 A 和方法 B 共用，重跑評估也不該重新下載，
        # 而且 OpenAlex 的篇名查每次 10 credits，每天只有 1,000 次。
        # 真的不要快取（例如單元測試）就明寫 cache=None。
        self.cache: FullTextCache | None = FullTextCache() if cache is _UNSET else cache

    # ---- 對外 ----
    def fetch(self, meta: ReferenceMetadata) -> FullTextResult:
        key = reference_cache_key(meta)
        if not key:
            return FullTextResult(status=FullTextStatus.NO_IDENTIFIER,
                                  note="書目沒有 DOI、arXiv ID，也沒有篇名，無法定位全文")
        if self.cache is not None:
            cached = self.cache.get(key)
            if cached is not None:
                return cached

        trace = getattr(self.downloader, "attempts", None)
        start = len(trace) if isinstance(trace, list) else 0
        run = _Run()
        result = self._route(meta, run)
        result = _with_note(result, run)
        if isinstance(trace, list):
            result = _replace_attempts(result, tuple(trace[start:]))
        if self.cache is not None:
            self.cache.put(key, result)
        return result

    def fetch_file(self, path: Path | str) -> FullTextResult:
        """手動上傳的被引文獻走同一條轉檔路（分工：手動上傳備援）。"""

        path = Path(path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            return FullTextResult(status=FullTextStatus.FETCH_FAILED,
                                  source="manual_upload", url=str(path),
                                  note=f"讀不到檔案：{type(exc).__name__}")
        suffix = path.suffix.casefold()
        if suffix in (".md", ".markdown", ".txt"):
            text = data.decode("utf-8", errors="replace").strip()
            return _converted(text, "manual_upload", str(path), "passthrough",
                              "手動上傳，原檔已是純文字", min_chars=0)
        if suffix in (".xml", ".nxml", ".jats"):
            return _converted(jats_to_markdown(data.decode("utf-8", errors="replace")),
                              "manual_upload", str(path), "jats", "手動上傳 JATS XML", min_chars=0)
        if suffix in (".html", ".htm"):
            markdown, converter = html_to_markdown(data.decode("utf-8", errors="replace"))
            return _converted(markdown, "manual_upload", str(path), converter, "手動上傳 HTML",
                              min_chars=0)
        if suffix == ".pdf":
            run = _Run()
            return _with_note(self._from_pdf(data, "manual_upload", str(path), run), run)
        return FullTextResult(status=FullTextStatus.CONVERT_FAILED,
                              source="manual_upload", url=str(path),
                              note=f"不支援的副檔名「{suffix or '（無）'}」")

    # ---- 分流 ----
    def _route(self, meta: ReferenceMetadata, run: _Run) -> FullTextResult:
        if meta.arxiv_id:
            result = self._from_arxiv(meta.arxiv_id, run)
            if result.ok:
                return result
            run.note("arXiv 沒拿到全文，改用 OpenAlex 找其他開放存取位置")

        locations = self._openalex_locations(meta, run)
        pmcid = _first_pmcid(locations)
        if pmcid:
            result = self._from_europe_pmc(pmcid, run)
            if result.ok:
                return result

        # HTML 先於 PDF（分工：優先使用 HTML、XML）。HTML 不經過版面還原，比從 PDF 轉準確：
        # 實測 Nature 那篇的 landing page 轉出 33,967 字乾淨內容，PDF 那條則要靠 docling。
        # 取捨：這樣可能用到次佳位置的 HTML 而不是最佳位置的 PDF，但格式的差距比位置大。
        # landing page 是只有書目的空殼時，TOO_SHORT 門檻會擋下來，再往下試 PDF。
        for location in locations:
            landing = _clean_url(location.get("landing_page_url"))
            if landing and str(location.get("version") or "") != "submittedVersion":
                result = self._from_html_url(landing, run)
                if result.ok:
                    return result
        for location in locations:
            pdf_url = _clean_url(location.get("pdf_url"))
            if pdf_url:
                result = self._from_pdf_url(pdf_url, run)
                if result.ok:
                    return result

        # 最後一條路：用篇名查 arXiv 的預印本。
        # 實測動機（2026-11-26，112 筆被引文獻）：7 筆被 OpenAlex 判為「沒有開放存取位置」，
        # 幾乎都是 IEEE／ACM 的會議論文（ICRA、ICCV、IEEE Micro）。會議版 DOI 沒有 OA 位置，
        # 但同一篇的 arXiv 預印本通常存在，只是掛在另一筆 OpenAlex 紀錄上。
        # 放在最後而不是前面：它多花一次 arXiv 查詢（每 3 秒 1 次），前面任何一條路成功就不必跑。
        # 四個條件都成立才跑，否則是把額度花在必然查不到的地方：
        #   1. 有篇名可查
        #   2. 書目本身沒有 arXiv ID——有的話那條路剛才就試過同一篇了
        #   3. OpenAlex 給的位置裡沒有 arxiv.org——有的話上面兩個迴圈已經要過同一組網址
        #   4. 過程中沒有出過錯。OpenAlex 被限流時，我們連「它認不認得這個篇名」都不知道，
        #      這時查到的東西不可信；而且會把一次限流放大成每一筆 ref 都多打一次 arXiv，
        #      反而更容易觸發 arXiv 自己的 429（冷卻是 per-host，會波及後面的 ref）
        if (meta.title and not meta.arxiv_id and not run.had_error
                and not _has_arxiv_location(locations)):
            result = self._from_arxiv_title_search(meta, run)
            if result is not None and result.ok:
                return result

        # 收尾優先序：拿到檔案卻轉不出來 > 過程中出過錯 > 真的沒有 OA 全文。
        # 前兩種都不是「這篇沒有開放存取全文」，不可以當成實質結論。
        if run.fallback is not None:
            return run.fallback
        if run.had_error:
            return FullTextResult(
                status=FullTextStatus.FETCH_FAILED,
                note="過程中有下載或查詢失敗，沒有查成功，不代表這篇沒有開放存取全文")
        return FullTextResult(
            status=FullTextStatus.NO_OPEN_ACCESS,
            note="沒有可用的開放存取位置" if not locations else "開放存取位置都試過了，沒有一個拿得到全文")

    # ---- 各條路 ----
    def _from_arxiv(self, arxiv_id: str, run: _Run) -> FullTextResult:
        clean = quote(arxiv_id.strip(), safe="./")
        url = f"{ARXIV_HTML}/{clean}"
        download = self.downloader.get(url, accept="text/html")
        if download.ok:
            result = self._convert_html(download, url, run, source="arxiv_html")
            if result.ok:
                run.note("用 arXiv 官方 HTML 版取得全文")
                return result
        elif _is_absence(download):
            run.note("這篇沒有 arXiv HTML 版")
        else:
            run.had_error = True
            run.note(f"arXiv HTML 下載失敗（{_reason(download)}）")

        result = self._from_pdf_url(f"{ARXIV_PDF}/{clean}", run, source="arxiv_pdf")
        if result.ok:
            run.note("改用 arXiv PDF 取得全文")
        return result

    def _from_arxiv_title_search(self, meta: ReferenceMetadata, run: _Run) -> FullTextResult | None:
        """用篇名查 arXiv 的預印本，查不到回 ``None``（代表這條路沒結論，不是失敗）。

        篇名不夠像就不採用。arXiv 的 ``ti:`` 名目上是詞組比對，但實測畸形輸入（例如書目裡
        有未閉合的引號）會讓它退化成模糊搜尋，回五筆毫不相關的論文；**拿錯論文的全文去比對
        會被判成「引用與來源不符」，那是假的不支持**，所以篇名這一關不能省。

        門檻跟著書目有沒有識別碼走，與 ``reference_lookup`` 查摘要那條路一致：沒有 DOI、
        arXiv ID 時要求完全相符。兩條路標準不同的話，同一筆 ref 的全文與摘要會來自不同論文。
        """

        query = meta.title.replace('"', " ").strip()
        if not query:
            return None
        download = self.downloader.get(
            ARXIV_API,
            {"search_query": f'ti:"{query}"', "start": 0, "max_results": ARXIV_SEARCH_RESULTS},
            accept="application/atom+xml")
        if not download.ok:
            # 這是查詢端點，不是某一篇的網址，所以不套 _is_absence：這裡的 404 代表端點或
            # 參數有問題，不代表這篇沒有預印本。端點壞掉是全批一致的，若記成「沒有開放存取
            # 全文」會讓整批 ref 都被誤判。
            run.had_error = True
            run.note(f"arXiv 用篇名查預印本失敗（{_reason(download)}）")
            return None
        threshold = TITLE_MATCH if meta.has_identifier else 1.0
        arxiv_id, problem = _best_arxiv_match(download.text, meta.title, threshold)
        if problem:
            # 讀不出來不是「查無」。arXiv 過載時會回 HTTP 200 的 HTML 頁，
            # 當成查無的話收尾會寫成「這篇沒有開放存取全文」，那是假結論。
            run.had_error = True
            run.note(f"arXiv 的查詢回應讀不出來（{problem}）")
            return None
        if not arxiv_id:
            run.note("arXiv 用篇名查不到篇名相符的預印本")
            return None
        run.note(f"OpenAlex 沒有可用位置，改用 arXiv 篇名查到預印本 {arxiv_id}")
        return self._from_arxiv(arxiv_id, run)

    def _from_europe_pmc(self, pmcid: str, run: _Run) -> FullTextResult:
        url = f"{EUROPE_PMC_BASE}/{pmcid}/fullTextXML"
        download = self.downloader.get(url, accept="application/xml")
        if not download.ok:
            if _is_absence(download):
                run.note("Europe PMC 沒有這篇的開放存取全文 XML")
                return FullTextResult(status=FullTextStatus.NO_OPEN_ACCESS,
                                      source="europe_pmc_xml", url=url)
            run.had_error = True
            run.note(f"Europe PMC 全文下載失敗（{_reason(download)}）")
            return FullTextResult(status=FullTextStatus.FETCH_FAILED,
                                  source="europe_pmc_xml", url=url)
        markdown = jats_to_markdown(download.text)
        if not markdown.strip():
            run.note("Europe PMC 的 JATS XML 解析不出內文")
            result = FullTextResult(status=FullTextStatus.CONVERT_FAILED,
                                    source="europe_pmc_xml", url=url, converter="jats")
            run.keep(result)
            return result
        result = _converted(markdown, "europe_pmc_xml", url, "jats", "")
        if result.status is FullTextStatus.TOO_SHORT:
            run.note(f"Europe PMC 的 XML {result.note}")
            run.keep(result)
            return result
        run.note("用 Europe PMC 的 JATS XML 取得全文")
        return result

    def _from_pdf_url(self, url: str, run: _Run, *, source: str = "oa_pdf") -> FullTextResult:
        download = self.downloader.get(url, accept="application/pdf")
        if not download.ok:
            # 只有明確的 404／410 才算「沒有這個檔」；403（擋爬蟲）、429、連線中斷
            # 都是沒查成功，必須記成錯誤，否則最後會被寫成「沒有開放存取全文」
            run.had_error |= not _is_absence(download)
            run.note(f"PDF 下載失敗（{_reason(download)}）")
            return FullTextResult(status=FullTextStatus.FETCH_FAILED, source=source, url=url)
        if download.looks_like_html and not download.looks_like_pdf:
            run.note("標示為 PDF 的連結實際回傳 HTML，改用 HTML 解析")
            return self._convert_html(download, url, run, source="oa_html")
        return self._from_pdf(download.body, source, url, run)

    def _from_pdf(self, data: bytes, source: str, url: str, run: _Run) -> FullTextResult:
        markdown, converter, error = pdf_to_markdown(data)
        if not converter:
            run.note("本機沒有安裝 docling，PDF 轉不了（不是文獻的問題）")
            result = FullTextResult(status=FullTextStatus.NO_CONVERTER, source=source, url=url)
            run.keep(result)
            return result
        if error:
            run.note(f"docling 轉檔失敗（{error}）")
            result = FullTextResult(status=FullTextStatus.CONVERT_FAILED, source=source, url=url,
                                    converter=converter)
            run.keep(result)
            return result
        if not markdown.strip():
            run.note("docling 轉出來是空的，可能是掃描檔")
            result = FullTextResult(status=FullTextStatus.CONVERT_FAILED, source=source, url=url,
                                    converter=converter)
            run.keep(result)
            return result
        result = _converted(markdown, source, url, converter, "")
        if result.status is FullTextStatus.TOO_SHORT:
            run.note(f"{url}：{result.note}")
            run.keep(result)
        return result

    def _from_html_url(self, url: str, run: _Run) -> FullTextResult:
        download = self.downloader.get(url, accept="text/html")
        if not download.ok:
            run.had_error |= not _is_absence(download)
            run.note(f"出版社頁面下載失敗（{_reason(download)}）")
            return FullTextResult(status=FullTextStatus.FETCH_FAILED, source="oa_html", url=url)
        if download.looks_like_pdf:
            run.note("出版社頁面直接回傳 PDF")
            return self._from_pdf(download.body, "oa_pdf", url, run)
        result = self._convert_html(download, url, run, source="oa_html")
        if result.ok:
            run.note("用出版社的 HTML 頁面取得全文")
        return result

    def _convert_html(self, download: Download, url: str, run: _Run, *, source: str) -> FullTextResult:
        markdown, converter = html_to_markdown(download.text)
        if not markdown.strip():
            run.note(f"{url} 解析不出內文")
            result = FullTextResult(status=FullTextStatus.CONVERT_FAILED, source=source,
                                    url=url, converter=converter)
            run.keep(result)
            return result
        result = _converted(markdown, source, url, converter, "")
        if result.status is FullTextStatus.TOO_SHORT:
            run.note(f"{url}：{result.note}")
            run.keep(result)
        return result

    # ---- OpenAlex 路由 ----
    def _openalex_locations(self, meta: ReferenceMetadata, run: _Run) -> list[dict]:
        key = {"api_key": self.openalex_key} if self.openalex_key else {}
        if meta.doi:
            # DOI 直查是 singleton，0 credits
            url = f"{OPENALEX_BASE}/works/doi:{quote(meta.doi, safe='/')}"
            download = self.downloader.get(url, {**key, "select": OPENALEX_FULLTEXT_SELECT})
            if download.ok:
                work = download.json()
                if not work:
                    run.had_error = True
                    run.note("OpenAlex 用 DOI 查到的回應不是 JSON，無法判讀")
                    return []
                locations = _open_locations(work)
                if not locations:
                    run.note("OpenAlex 用 DOI 查到這篇，但沒有開放存取位置")
                return locations
            if not _is_absence(download):
                run.had_error = True
                run.note(f"OpenAlex 用 DOI 查 OA 位置失敗（{_reason(download)}）")
                return []
            run.note("OpenAlex 查無此 DOI，改用篇名查")

        if not meta.title:
            run.note("書目沒有篇名，沒有再向 OpenAlex 查詢")
            return []

        # 篇名查屬 search，每次 10 credits，額度有限，所以要靠 FullTextCache
        url = f"{OPENALEX_BASE}/works"
        download = self.downloader.get(url, {**key, "search": meta.title, "per_page": 3,
                                             "select": OPENALEX_FULLTEXT_SELECT})
        if not download.ok:
            run.had_error |= not _is_absence(download)
            run.note(f"OpenAlex 用篇名查 OA 位置失敗（{_reason(download)}）")
            return []
        results = download.json().get("results")
        candidates = [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []
        if not candidates:
            run.note("OpenAlex 用篇名查無相符文獻")
            return []
        # 門檻跟著書目有沒有識別碼走，與 reference_lookup 查摘要那條路一致：沒有 DOI、
        # arXiv ID 時要求完全相符。原本這裡一律用 TITLE_MATCH，比查摘要那條路鬆，
        # 同一筆 ref 的全文與摘要有可能來自不同論文。
        threshold = TITLE_MATCH if meta.has_identifier else 1.0
        matched_without_locations = False
        for work in candidates:
            if title_similarity(meta.title, str(work.get("display_name") or "")) >= threshold:
                locations = _open_locations(work)
                if locations:
                    return locations
                # 篇名對上但這一筆沒有 OA 位置。同一篇常常有「預印本紀錄」和「期刊紀錄」
                # 兩筆，先命中的那筆沒有位置不代表這篇沒有，要繼續看下一個候選。
                matched_without_locations = True
        if matched_without_locations:
            run.note("OpenAlex 用篇名核對到這篇，但沒有開放存取位置")
            return []
        # search 是模糊比對，一定會回「最接近的」而不是「相符的」。篇名不夠像就不能用：
        # 拿錯論文的全文去比對，會被判成「引用與來源不符」，那是假的不支持。
        closest = str(candidates[0].get("display_name") or "")[:60]
        run.note(f"OpenAlex 用篇名查到的結果篇名不符（最接近的是「{closest}」），不採用")
        return []


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _is_absence(download: Download) -> bool:
    """只有 404／410 算「這個東西不存在」；其餘失敗一律是沒查成功。"""

    return download.status in (404, 410)


def _reason(download: Download) -> str:
    return download.error or f"HTTP {download.status}"


def _has_arxiv_location(locations: list[dict]) -> bool:
    """OpenAlex 給的位置裡有沒有 arxiv.org；有的話前面的迴圈已經要過同一組網址了。"""

    for location in locations:
        for field in ("pdf_url", "landing_page_url"):
            if "arxiv.org" in _clean_url(location.get(field)).casefold():
                return True
    return False


# 試過並退回的做法（2026-11-26）：曾經放寬成「資料庫篇名以書目篇名開頭也算同一篇」，
# 動機是 OpenAlex 對 IEEE 紀錄的 display_name 會接會議資訊，例如
# `...RT-X Models : Open X-Embodiment Collaboration 0`，相似度只有 0.78 而被判為查不到。
# 退回原因：「書目篇名是另一篇論文篇名的前綴」在學術文獻裡是結構性常見，不是邊角案例——
# `Generative Adversarial Networks` → `...: An Overview`（0.84）、
# `Attention is all you need` → `... in speech separation`（0.70）、
# `A Survey of Large Language Models` → `... for Healthcare`（0.82）等六個反例全部會誤中，
# 抓到的是別篇論文的全文，而且 status 是「拿到全文」、note 裡沒有任何警告，下游看不出來。
# 加上 reference_lookup 在沒有 DOI 時要求篇名完全相符，放寬全文這一條會讓兩條路標準不一致。
# IEEE 的 display_name 污染因此仍是已知限制：沒有 DOI 的 IEEE 會議論文可能查不回來。

_ARXIV_ABS_RE = re.compile(r"arxiv\.org/abs/(?P<id>.+?)(?:v\d+)?\s*$", re.IGNORECASE)


def _best_arxiv_match(atom_xml: str, title: str,
                      threshold: float = TITLE_MATCH) -> tuple[str, str]:
    """從 arXiv 的 Atom 回應挑出篇名相符的那一筆，回傳 ``(arxiv_id, 讀不出來的原因)``。

    三種結果必須分開，不可以壓成「回空字串」一種：

    - ``("2310.08864", "")``：找到了
    - ``("", "")``：回應讀得懂，但沒有篇名相符的預印本
    - ``("", "<原因>")``：回應讀不出來（arXiv 過載時會回 HTTP 200 的 HTML 頁，
      或回它自己的錯誤 feed）

    把第三種當成第二種，收尾會寫成「這篇沒有開放存取全文」，下游就變成假的「沒有來源支持」。
    """

    namespace = "{http://www.w3.org/2005/Atom}"
    try:
        feed = ElementTree.fromstring(atom_xml)
    except ElementTree.ParseError:
        return "", "回應不是 XML"
    # 光是解析成功不夠：HTML 頁面也可能是合法的 XML（實測 `<html><body>…</body></html>`
    # 解得開，root tag 是 html），findall 找不到 entry 就會被誤認成「查無預印本」。
    if feed.tag != f"{namespace}feed":
        return "", f"回應不是 Atom feed（root 是 {feed.tag}）"
    for entry in feed.findall(f"{namespace}entry"):
        identifier = (entry.findtext(f"{namespace}id") or "").strip()
        if "arxiv.org/api/errors" in identifier.casefold():
            return "", "arXiv 回報查詢本身有問題"
        # arXiv 的 <title> 常常帶換行與多餘空白，不正規化會讓相似度偏低
        found = " ".join((entry.findtext(f"{namespace}title") or "").split())
        if not found or title_similarity(title, found) < threshold:
            continue
        match = _ARXIV_ABS_RE.search(identifier)
        if match:
            return match.group("id"), ""
    return "", ""


def _open_locations(work: dict) -> list[dict]:
    """把 best_oa_location 排在最前面，其餘只留標示為 OA 的位置。"""

    locations: list[dict] = []
    best = work.get("best_oa_location")
    if isinstance(best, dict):
        locations.append(best)
    others = work.get("locations")
    if isinstance(others, list):
        for item in others:
            if isinstance(item, dict) and item.get("is_oa") and item not in locations:
                locations.append(item)
    return locations


def _first_pmcid(locations: list[dict]) -> str:
    for location in locations:
        for value in (location.get("pdf_url"), location.get("landing_page_url")):
            if isinstance(value, str):
                found = _PMCID.search(value)
                if found:
                    return found.group(0).upper()
    return ""


def _clean_url(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip().startswith("http") else ""


def _converted(markdown: str, source: str, url: str, converter: str, note: str,
               *, min_chars: int = MIN_FULL_TEXT_CHARS) -> FullTextResult:
    body = markdown.strip()
    if not body:
        return FullTextResult(status=FullTextStatus.CONVERT_FAILED, source=source, url=url,
                              converter=converter, note=note)
    if len(body) < min_chars:
        # 擋爬蟲頁、同意書頁、JS 版面殼都會轉出幾十到幾百字。放行的話來源欄會寫
        # 「拿到全文」，實際上判斷端拿到的是垃圾，看起來卻像有憑有據。
        short = f"轉出來只有 {len(body)} 字，短到不像全文（門檻 {min_chars}）"
        return FullTextResult(status=FullTextStatus.TOO_SHORT, markdown=body, source=source,
                              url=url, converter=converter,
                              note="；".join(part for part in (note, short) if part))
    return FullTextResult(status=FullTextStatus.GOT_FULL_TEXT, markdown=body,
                          source=source, url=url, converter=converter, note=note)


def _with_note(result: FullTextResult, run: _Run) -> FullTextResult:
    note = "；".join(part for part in (run.text, result.note) if part)
    return FullTextResult(status=result.status, markdown=result.markdown, source=result.source,
                          url=result.url, converter=result.converter, note=note,
                          attempts=result.attempts, cache_hit=result.cache_hit)


def _replace_attempts(result: FullTextResult, attempts: tuple[dict, ...]) -> FullTextResult:
    return FullTextResult(status=result.status, markdown=result.markdown, source=result.source,
                          url=result.url, converter=result.converter, note=result.note,
                          attempts=attempts, cache_hit=result.cache_hit)
