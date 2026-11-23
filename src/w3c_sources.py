"""Identify a W3C technical report and extract its explicitly marked abstract."""

from datetime import date, datetime
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from src.abstract_sources import clean_text


W3C = "https://www.w3.org"
_REPORT = re.compile(r"/TR/(?P<name>[a-z0-9][a-z0-9.-]*)/?")
_ARCHIVE = re.compile(r"/TR/\d{4}/[A-Z]+-(?P<name>[a-z0-9.-]+)-(?P<date>\d{8})/?")
_VERSION = re.compile(r"\bv(?P<version>\d+\.\d+)(?:\.\d+)?\b", re.I)
_ACCESS_DATE = re.compile(r"\bAccessed:\s*([A-Za-z]+)\.?\s+(\d{1,2}),\s+(\d{4})\b", re.I)
_HISTORY_NAMES = {"vc-overview": "vc-overview-1.0"}


def report_path(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme != "https" or parsed.netloc.lower() not in ("www.w3.org", "w3.org"):
        return ""
    if parsed.query or not (_REPORT.fullmatch(parsed.path) or _ARCHIVE.fullmatch(parsed.path)):
        return ""
    return parsed.path.rstrip("/") + "/"


def access_date(reference: str) -> date | None:
    match = _ACCESS_DATE.search(reference)
    if not match:
        return None
    month, day, year = match.groups()
    try:
        return datetime.strptime(f"{month[:3]} {day} {year}", "%b %d %Y").date()
    except ValueError:
        return None


def history_names(path: str, title: str) -> tuple[str, ...]:
    report = _REPORT.fullmatch(path)
    if report is None:
        return ()
    name = report["name"]
    version = _VERSION.search(title)
    if version and not name.endswith(f"-{version['version']}"):
        return (f"{name}-{version['version']}", name)
    if name in _HISTORY_NAMES:
        return (_HISTORY_NAMES[name], name)
    return (name,)


def comparable_titles(cited: str, published: str, path: str) -> tuple[str, str] | None:
    cited_version = _VERSION.search(cited)
    page_version = _VERSION.search(published)
    if cited_version and (not page_version or cited_version["version"] != page_version["version"]):
        return None
    source, target = (_VERSION.sub("", title).strip() for title in (cited, published))
    if "vc-bitstring-status-list" in path:
        source = re.sub(r"^Verifiable Credentials\s+", "", source, flags=re.I)
    return source, target


class W3CPage(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self._first_h1_seen = False
        self._in_title = False
        self._abstract_depth = 0
        self._abstract_heading_level: int | None = None
        self._title: list[str] = []
        self._abstract: list[str] = []
        self.archive_links: list[tuple[date, str, str]] = []
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "h1" and not self._first_h1_seen:
            self._first_h1_seen = self._in_title = True
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            if self._abstract_heading_level is not None and level <= self._abstract_heading_level:
                self._abstract_heading_level = None
            if values.get("id") == "abstract" and not self._abstract_depth:
                self._abstract_heading_level = level
        if tag == "section":
            if self._abstract_depth or values.get("id") == "abstract":
                self._abstract_depth += 1
        if (self._abstract_depth or self._abstract_heading_level is not None) and tag in (
            "p", "br", "li", "div", "h2", "h3",
        ):
            self._abstract.append(" ")
        if tag == "a":
            href = values.get("href") or ""
            path = report_path(href if not href.startswith("/TR/") else W3C + href)
            match = _ARCHIVE.fullmatch(path)
            if match:
                try:
                    published = datetime.strptime(match["date"], "%Y%m%d").date()
                except ValueError:
                    return
                self.archive_links.append((published, match["name"], path))

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1":
            self._in_title = False
        if self._abstract_depth and tag == "section":
            self._abstract_depth -= 1
        if tag in ("main", "body"):
            self._abstract_heading_level = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title.append(data)
        if self._abstract_depth or self._abstract_heading_level is not None:
            self._abstract.append(data)

    @property
    def title(self) -> str:
        return clean_text("".join(self._title))

    @property
    def abstract(self) -> str:
        text = clean_text("".join(self._abstract))
        return "" if text.casefold() == "abstract" else text.removeprefix("Abstract ")

    def archived_at(self, names: tuple[str, ...], accessed: date) -> str:
        options = [(published, path) for published, name, path in self.archive_links
                   if name in names and published <= accessed]
        return max(options)[1] if options else ""
