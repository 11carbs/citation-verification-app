"""Extract title, DOI and URL from one raw IEEE reference entry (第二人).

The entry text comes from ``CitationPair.ref_raw`` produced by
``reference_parser``; it may still contain the line breaks of a PDF copy.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

_LEADING_NUMBER_RE = re.compile(r"^\s*\[\d+\]\s*")
_TITLE_RE = re.compile(r"[“\"](.+?)[”\"]")
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"“”<>]+)", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s\"“”<>]+", re.IGNORECASE)
_URLISH_START_RE = re.compile(
    r"^(https?://|www\.|doi\.org|dx\.doi\.org|10\.\d{4,9}/)", re.IGNORECASE
)
# The next line looks like the rest of a URL: URL characters only, no colon.
_URLISH_CONT_RE = re.compile(r"^[A-Za-z0-9\-._~/?#=&%+]+(\s|$)")
_TRAILING = ".,;)]"


@dataclass(frozen=True, slots=True)
class ReferenceMetadata:
    title: str = ""
    doi: str = ""
    url: str = ""
    parse_note: str = ""


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
        if mid_url or (joined.endswith("-") and line[:1].islower()):
            joined += line
        else:
            joined += " " + line
    return joined


def _clean(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().rstrip(_TRAILING).strip()


def extract_reference_metadata(ref_raw: str) -> ReferenceMetadata:
    """Return the quoted IEEE title, first DOI and first URL of one entry."""

    body = _LEADING_NUMBER_RE.sub("", join_reference_lines(ref_raw))
    title_match = _TITLE_RE.search(body)
    doi_match = _DOI_RE.search(body)
    url_match = _URL_RE.search(body)
    title = _clean(title_match.group(1)) if title_match else ""
    return ReferenceMetadata(
        title=title,
        doi=_clean(doi_match.group(1)).lower() if doi_match else "",
        url=_clean(url_match.group(0)) if url_match else "",
        parse_note="" if title else "抓不到引號內的篇名（可能是書籍、標準或格式不符）",
    )
