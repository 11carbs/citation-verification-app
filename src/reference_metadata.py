"""Extract title, DOI and URL from one raw IEEE reference entry (第二人).

The entry text comes from ``CitationPair.ref_raw`` produced by
``reference_parser``; it may still contain the line breaks of a PDF copy.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

_LEADING_NUMBER_RE = re.compile(r"^\s*\[\d+\]\s*")
# IEEE 的篇名引號在不同排版下長得不一樣：
#   “…”（彎引號）、"…"（直引號）、''…''（兩個單引號，IEEE Access 的 PDF 常見）、
#   ‘‘…’’（兩個彎單引號，pdftotext 從 IEEEtran 抽出來的樣子）、``…''（LaTeX 原始寫法）
_OPEN_QUOTE = r"(?:\u201c|\u2018\u2018|``|''|\")"
_CLOSE_QUOTE = r"(?:\u201d|\u2019\u2019|''|\")"
_TITLE_RE = re.compile(_OPEN_QUOTE + r"(.+?)" + _CLOSE_QUOTE, re.DOTALL)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"“”<>]+)", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s\"“”<>]+", re.IGNORECASE)
# arXiv:2602.23452 / arXiv:2602.23452v2 / arxiv.org/abs/2602.23452 / 舊式 cs.CL/0112017
_ARXIV_RE = re.compile(
    r"(?:arxiv[:\s/]+|arxiv\.org/(?:abs|pdf)/)"
    r"(?P<id>\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)",
    re.IGNORECASE,
)
_URLISH_START_RE = re.compile(
    r"^(https?://|www\.|doi\.org|dx\.doi\.org|10\.\d{4,9}/)", re.IGNORECASE
)
# The next line looks like the rest of a URL: URL characters only, no colon.
_URLISH_CONT_RE = re.compile(r"^[A-Za-z0-9\-._~/?#=&%+]+(\s|$)")
# The PDF can break a URL inside its scheme: "... Available: http" + "s://host/…".
# Joining those with a space loses the whole URL, so rejoin them with nothing.
_SCHEME_TAIL_RE = re.compile(r"(?:^|\s)(?:h|ht|htt|http|https|https?:|https?:/)$", re.IGNORECASE)
_SCHEME_HEAD_RE = re.compile(r"^(?:s?://|//|ttps?://|tps?://|ps?://)", re.IGNORECASE)
_TRAILING = ".,;"
_CLOSERS = {")": "(", "]": "["}


@dataclass(frozen=True, slots=True)
class ReferenceMetadata:
    title: str = ""
    doi: str = ""
    url: str = ""
    arxiv_id: str = ""
    parse_note: str = ""

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


def extract_reference_metadata(ref_raw: str) -> ReferenceMetadata:
    """Return the quoted IEEE title, first DOI and first URL of one entry."""

    body = _LEADING_NUMBER_RE.sub("", join_reference_lines(ref_raw))
    title_match = _TITLE_RE.search(body)
    doi_match = _DOI_RE.search(body)
    url_match = _URL_RE.search(body)
    arxiv_match = _ARXIV_RE.search(body)
    title = _clean(title_match.group(1)) if title_match else ""
    arxiv_id = _clean(arxiv_match.group("id")) if arxiv_match else ""
    notes = []
    if not title:
        notes.append("抓不到引號內的篇名（可能是書籍、標準或格式不符）")
    if arxiv_id:
        notes.append(f"抓到 arXiv ID {arxiv_id}")
    return ReferenceMetadata(
        title=title,
        doi=_clean(doi_match.group(1)).lower() if doi_match else "",
        url=_clean(url_match.group(0)) if url_match else "",
        arxiv_id=arxiv_id,
        parse_note="；".join(notes),
    )
