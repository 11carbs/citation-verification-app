"""Extract title, DOI and URL from one raw reference entry (第二人).

The entry text comes from ``CitationPair.ref_raw`` produced by
``reference_parser``; it may still contain the line breaks of a PDF copy.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

# 開頭的編號：`[12]`、`(12)`、`12.`，以及 trafilatura 從 HTML 轉出來時
# 會把方括號轉義成 `\[12\]` 的樣子。不剝掉的話作者會抽成「\[37\] M」。
_LEADING_NUMBER_RE = re.compile(r"^\s*(?:\\?\[\d{1,3}\\?\]|\(\d{1,3}\)|\d{1,3}\.)\s*")
# IEEE 的篇名引號在不同排版下長得不一樣：
#   “…”（彎引號）、"…"（直引號）、''…''（兩個單引號，IEEE Access 的 PDF 常見）、
#   ‘‘…’’（兩個彎單引號，pdftotext 從 IEEEtran 抽出來的樣子）、``…''（LaTeX 原始寫法）
_TITLE_RE = re.compile(
    r'"(?P<straight>.+?)"|\u201c(?P<curly>.+?)\u201d|'
    r"\u2018\u2018(?P<single_curly>.+?)\u2019\u2019|(?:``|'')(?P<latex>.+?)''",
    re.DOTALL,
)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"“”<>]+)", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s\"“”<>]+", re.IGNORECASE)
# arXiv:2602.23452 / arXiv:2602.23452v2 / arxiv.org/abs/2602.23452 / 舊式 cs.CL/0112017
_ARXIV_RE = re.compile(
    r"(?:arxiv[:\s/]+|arxiv\.org/(?:abs|pdf)/)"
    r"(?P<id>\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)",
    re.IGNORECASE,
)
_REFERENCE_TAIL_RE = re.compile(r"\b(?:accessed\s*:|available\s*:|doi\s*:|https?://)", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<![\w./–—-])(?:19|20)\d{2}(?=\s*(?:[,.;)\]]|$))")
_URLISH_START_RE = re.compile(
    r"^(https?://|www\.|doi\.org|dx\.doi\.org|10\.\d{4,9}/)", re.IGNORECASE
)
# The next line looks like the rest of a URL: URL characters only, no colon.
_URLISH_CONT_RE = re.compile(r"^[A-Za-z0-9\-._~/?#=&%+]+(\s|$)")
# The PDF can break a URL inside its scheme: "... Available: http" + "s://host/…".
# Joining those with a space loses the whole URL, so rejoin them with nothing.
_SCHEME_TAIL_RE = re.compile(r"(?:^|\s)(?:h|ht|htt|http|https|https?:|https?:/)$", re.IGNORECASE)
_SCHEME_HEAD_RE = re.compile(r"^(?:s?://|//|ttps?://|tps?://|ps?://)", re.IGNORECASE)
# 作者年份制（APA／ACL／Chicago 等）：「作者. 年份. 篇名. 出處」。
# 篇名沒有引號，所以引號那條抓不到，改用句點切。年份後面允許 a/b 區分同年同作者。
_AUTHOR_YEAR_RE = re.compile(
    r"^(?P<authors>.{3,400}?)\.\s*(?P<year>(?:19|20)\d{2})(?P<suffix>[a-z])?\.\s*"
    r"(?P<title>.{5,400}?)[.?!](?:\s|$)",
    re.DOTALL,
)
# PDF 換行斷字：「af- fecting」要還原成「affecting」。
# 只處理「字母 + 連字號 + 空格 + 小寫字母」，所以 Mistral- AI、Qwen3- 8B3 這類
# 本來就有連字號的不會被動到（右邊是大寫或數字）。
#
# ⚠️ 這條規則會誤合併 highly- cited、Cite- screener 這種真的有連字號的。
# 仍然這樣做，是因為實測比對層的結果：沒有 DOI 時篇名要求完全相符，
# 不處理的話 10 筆帶斷字的篇名相似度全是 0.993、全部查不到；
# 全部合併之後 8 筆變成 1.000 查得到，誤合併的 2 筆是 0.994、本來就查不到。
# 要真正修好必須在 PDF 轉檔當下看連字號在不在行尾，那是第一人那一層的事。
_SOFT_HYPHEN_RE = re.compile(r"([A-Za-z\u00c0-\u00ff])- ([a-z\u00e0-\u00ff])")
# APA／Harvard：「作者 (年份). 篇名. 出處」。年份在括號裡，是真實語料裡最常見的
# 作者年份制寫法（7,294 筆實測佔 32%，ACL 那種裸年份只有 1%）。
_PAREN_YEAR_RE = re.compile(
    r"^(?P<authors>.{3,250}?)\(\s*(?P<year>(?:19|20)\d{2})[a-z]?\s*\)\s*[.,:]?\s*"
    r"(?P<title>.{5,400}?)[.?!](?:\s|$)",
    re.DOTALL,
)
# arXiv 的 HTML 書目會在每筆前面加一個引用鍵：
#   「Müller et al. (2022) Müller, T., Evans, A., ...: Instant neural graphics primitives...」
# 不剝掉的話，括號年份那條會把後面整串作者當成篇名。實測 7,294 筆裡有 9% 長這樣。
_CITATION_KEY_RE = re.compile(
    r"^\s*\\?\[?\d{0,3}\\?\]?\s*[^()]{1,60}?\(\s*(?:19|20)\d{2}[a-z]?\s*\)\s+(?=\S)"
)
# 抽出來的東西不像篇名時寧可不給。錯的篇名會害系統去抓別篇論文的全文，
# 產生假的「引用與來源不符」，比沒有篇名更糟。
_NOT_A_TITLE_RE = re.compile(
    r"^(?:in\b|proceedings\b|to appear\b|technical report\b|preprint\b|available\b"
    r"|accessed\b|url\b|https?://|doi\b|pp?\.\s|vol\.|retrieved\b|arxiv\b"
    r"|manuscript\b|submitted\b|forthcoming\b)",
    re.IGNORECASE,
)
_TRAILING = ".,;"
_CLOSERS = {")": "(", "]": "["}


@dataclass(frozen=True, slots=True)
class ReferenceMetadata:
    title: str = ""
    doi: str = ""
    url: str = ""
    arxiv_id: str = ""
    parse_note: str = ""
    first_author: str = ""
    publication_year: int | None = None

    @property
    def has_identifier(self) -> bool:
        return bool(self.doi or self.arxiv_id)


def join_reference_lines(raw: str) -> str:
    """Join a multi-line entry; rejoin URLs/DOIs and hyphenated words split by line breaks."""

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    joined = ""
    for line in lines:
        if not joined:
            joined = line
            continue
        last_token = joined.rsplit(" ", 1)[-1]
        mid_url = (
            _URLISH_START_RE.match(last_token)
            and last_token[-1] in "-/._"
            and _URLISH_CONT_RE.match(line)
            and not line[0].isupper()
        )
        split_scheme = bool(_SCHEME_TAIL_RE.search(joined) and _SCHEME_HEAD_RE.match(line))
        if mid_url or split_scheme or (joined.endswith("-") and line[:1].islower()):
            joined += line
        else:
            joined += " " + line
    return joined


def _clean(value: str | None) -> str:
    """Strip trailing punctuation, but keep a bracket that closes one inside.

    ``"Model context protocol (MCP),"`` must keep its ``)``; only the comma goes.
    """

    if not value:
        return ""
    value = value.strip()
    while value:
        last = value[-1]
        if last in _TRAILING:
            value = value[:-1].rstrip()
            continue
        opener = _CLOSERS.get(last)
        if opener and value.count(opener) < value.count(last):
            value = value[:-1].rstrip()
            continue
        break
    return value


def _looks_like_title(value: str) -> bool:
    """擋掉明顯不是篇名的東西：頁碼、編號、出處註記。

    寧可回報抓不到，也不要給一個錯的篇名。
    """

    # 不設字數下限：`Deep learning`、`Attention` 這種真實的短篇名要留著。
    # 擋掉的是頁碼與編號（字母太少）以及出處註記。
    if len(value) < 4:
        return False
    if sum(ch.isalpha() for ch in value) / len(value) < 0.6:
        return False
    return not _NOT_A_TITLE_RE.match(value.lstrip("\u201c\"'"))


def _strip_citation_key(body: str) -> str:
    """剝掉 arXiv HTML 書目前面的引用鍵，剝完要還是一筆完整書目才算數。"""

    match = _CITATION_KEY_RE.match(body)
    if not match:
        return body
    rest = body[match.end():]
    if len(rest) > 60 and (re.search(r"(?:19|20)\d{2}", rest) or re.search(r"[A-Z]\.[:,]", rest)):
        return rest
    return body


# 書目開頭到第一個年份（或第一個句點欄位）之間就是作者區。這條**不依賴篇名**，
# 因為抽不到篇名的書目正是最需要用作者去驗證比對結果的那些。
_BYLINE_RE = re.compile(
    r"^(?P<byline>.{3,250}?)(?:\(\s*(?:19|20)\d{2}|\.\s*(?:19|20)\d{2}\.|\.\s+[A-Z\u201c\"])"
)
_ANY_YEAR_RE = re.compile(r"(?<![\w.])((?:19|20)\d{2})(?![\w.])")


def _authors_and_year(body: str) -> tuple[str, int | None]:
    """篇名抽不到時仍要給出作者與年份，否則比對結果就沒有東西可以驗證。"""

    byline = _BYLINE_RE.match(body)
    author = _first_author(byline.group("byline")) if byline else ""
    years = [int(y) for y in _ANY_YEAR_RE.findall(body[:400])]
    # 書目裡常有卷期、頁碼、版次，只在剛好有一個年份時才採用，避免猜錯
    year = years[0] if len(set(years)) == 1 else None
    return author, year


def _first_author(byline: str) -> str:
    return re.split(
        r",|\s+and\s+|\s+et\s+al\.?", byline, maxsplit=1, flags=re.IGNORECASE,
    )[0].strip()


def extract_reference_metadata(ref_raw: str) -> ReferenceMetadata:
    """Return the title, first DOI and first URL of one reference entry.

    篇名有兩條路，依序試：

    1. 引號裡的篇名（IEEE 等編號制）。這是原本就有的行為，一行沒動
    2. 「作者. 年份. 篇名. 出處」（APA／ACL 等作者年份制）

    兩條都沒中就不給篇名，並在 ``parse_note`` 寫明。**不會為了湊數硬給一個**：
    錯的篇名會害系統去抓到別篇論文的全文，比沒有篇名更糟。
    """

    body = _strip_citation_key(
        _SOFT_HYPHEN_RE.sub(r"\1\2", _LEADING_NUMBER_RE.sub("", join_reference_lines(ref_raw)))
    )
    doi_match = _DOI_RE.search(body)
    url_match = _URL_RE.search(body)
    arxiv_match = _ARXIV_RE.search(body)
    title_match = _TITLE_RE.search(body)
    bare_year = _AUTHOR_YEAR_RE.match(body)
    paren_year = _PAREN_YEAR_RE.match(body)

    def _quote_is_inside(match: re.Match | None) -> bool:
        # 引號落在作者年份制的篇名「裡面」，那是內文引號不是篇名。
        # 例如 `When "normal" becomes normative: ...` 會被引號那條抓成 `normal`。
        return bool(
            match and title_match
            and match.start("title") <= title_match.start()
            and title_match.end() <= match.end("title")
        )

    if _quote_is_inside(bare_year) or _quote_is_inside(paren_year):
        title_match = None

    title = ""
    title_style = ""
    first_author = ""
    publication_year = None
    quoted = _clean(title_match.group(title_match.lastgroup)) if title_match else ""
    if quoted and _looks_like_title(quoted):
        title, title_style = quoted, "quoted"
        first_author = _first_author(body[:title_match.start()])
        publication_text = _REFERENCE_TAIL_RE.split(body[title_match.end():], maxsplit=1)[0]
        years = set(_YEAR_RE.findall(publication_text))
        if len(years) == 1:
            publication_year = int(years.pop())
    else:
        # 括號年份（APA／Harvard）在真實語料裡比裸年份（ACL）常見三十倍，先試它
        for match, style in ((paren_year, "paren-year"), (bare_year, "bare-year")):
            if match is None:
                continue
            candidate = _clean(match.group("title"))
            if _looks_like_title(candidate):
                title, title_style = candidate, style
                first_author = _first_author(match.group("authors"))
                publication_year = int(match.group("year"))
                break
    if not first_author or publication_year is None:
        # 篇名有沒有抽到都要給出驗證用的證據
        fallback_author, fallback_year = _authors_and_year(body)
        first_author = first_author or fallback_author
        publication_year = publication_year if publication_year is not None else fallback_year
    arxiv_id = _clean(arxiv_match.group("id")) if arxiv_match else ""
    notes = []
    if not title:
        notes.append("篇名抓不到：引號、括號年份、裸年份三種樣式都不符")
    elif title_style != "quoted":
        notes.append(f"篇名依作者年份制樣式抽出（{title_style}）")
    if arxiv_id:
        notes.append(f"抓到 arXiv ID {arxiv_id}")
    return ReferenceMetadata(
        title=title,
        doi=_clean(doi_match.group(1)).lower() if doi_match else "",
        url=_clean(url_match.group(0)) if url_match else "",
        arxiv_id=arxiv_id,
        parse_note="；".join(notes),
        first_author=first_author,
        publication_year=publication_year,
    )


def reference_cache_key(meta: ReferenceMetadata) -> str:
    """一筆被引文獻的穩定識別鍵。ref 編號不可以當鍵：不同論文的 [1] 是不同文獻。"""

    if meta.arxiv_id:
        return f"arxiv:{meta.arxiv_id.casefold()}"
    if meta.doi:
        return f"doi:{meta.doi.casefold()}"
    if meta.title:
        # 只有篇名時要併上第一作者與年份，否則同名不同篇（preprint 與期刊版、
        # 不同年的同名報告）會共用快取，讀到別篇的全文
        return "title:" + "|".join((
            " ".join(meta.title.casefold().split()),
            " ".join(meta.first_author.casefold().split()),
            str(meta.publication_year or ""),
        ))
    return ""
