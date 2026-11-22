"""Read abstracts explicitly published in authoritative record fields."""

from html import unescape
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import urlsplit


_ACL_DOI = re.compile(
    r"(?:10\.18653/(?:v1/)?|10\.3115/v1/)"
    r"(?:(?P<legacy>[a-z][0-9]{2}-[0-9]{4})|(?P<current>[0-9]{4}\.[a-z0-9]+-[a-z0-9-]+\.[0-9]+))"
)
ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?", re.I)

_OFFICIAL_WEB_HOSTS = {
    "fidoalliance.org": "FIDO Alliance",
    "www.fidoalliance.org": "FIDO Alliance",
    "www.anthropic.com": "Anthropic",
    "anthropic.com": "Anthropic",
    "google.github.io": "Google A2A",
    "a2a-protocol.org": "A2A 官方網站",
    "agentnetworkprotocol.com": "ANP 官方網站",
    "agoraprotocol.org": "Agora 官方網站",
    "ieeexplore.ieee.org": "IEEE Xplore",
    "scholars.ncu.edu.tw": "中央大學學術典藏",
}
_PUBLISHER_ABSTRACT_MARKERS = {"abstract", "abstract-content", "abstract-text"}


def official_web_source(url: str) -> str:
    """Recognize supported public official origins without fetching arbitrary URLs."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme != "https" or parsed.query or parsed.fragment:
        return ""
    if parsed.netloc.lower() == "google.github.io" and parsed.path.split("/")[1:2] not in (["A2A"], ["a2a"]):
        return ""
    return _OFFICIAL_WEB_HOSTS.get(parsed.netloc.lower(), "")


def clean_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", unescape(text)).split())


def doi_value(value: object) -> str:
    return str(value or "").strip().lower().removeprefix("https://doi.org/").removeprefix("http://doi.org/")


def abstract_problem(value: object) -> str:
    """Find explicit signs of missing or incomplete abstract text."""
    if not isinstance(value, str) or not any(char.isalpha() for char in value):
        return "未提供可用摘要"
    text = clean_text(value).strip().casefold()
    if text.rstrip(".") in {"no abstract available", "abstract unavailable", "abstract not available", "no abstract"}:
        return "摘要欄僅有狀態提示"
    if text.endswith(("...", "…", "[truncated]", "[摘要截斷]", "read more", "show more")):
        return "摘要有截斷或展開提示"
    return ""


def acl_anthology_id(doi: str) -> str:
    """Return the case-sensitive Anthology ID encoded in an ACL DOI."""
    match = _ACL_DOI.fullmatch(doi_value(doi))
    if match is None:
        return ""
    legacy = match["legacy"]
    return legacy.upper() if legacy else match["current"]


class AbstractHTML(HTMLParser):
    """Extract bibliographic meta tags and the ACL publisher's abstract block."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.metadata: dict[str, str] = {}
        self.authors: list[str] = []
        self._divs = 0
        self._parts: list[str] = []
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "meta" and values.get("content"):
            name = values.get("name") or values.get("property") or ""
            self.metadata.setdefault(name, values["content"])
            if name == "citation_author":
                self.authors.append(values["content"])
        if tag == "div":
            if self._divs:
                self._divs += 1
            elif "acl-abstract" in (values.get("class") or "").split():
                self._divs = 1
        if self._divs and tag in ("div", "p", "br"):
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._divs and tag == "div":
            self._divs -= 1
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._divs:
            if data.strip() == "Abstract" and not "".join(self._parts).strip():
                return
            self._parts.append(data)

    @property
    def publisher_abstract(self) -> str:
        return clean_text("".join(self._parts)).removeprefix("Abstract ")


class OfficialAbstractHTML(AbstractHTML):
    """Read only explicit abstract fields from a publisher or project page."""

    def __init__(self, html: str) -> None:
        self._heading: list[str] = []
        self._reading_heading = False
        self._window_title: list[str] = []
        self._reading_title = False
        self._containers: list[str] = []
        self._abstract_parts: list[str] = []
        super().__init__(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        super().handle_starttag(tag, attrs)
        if tag == "h1" and not self._heading:
            self._reading_heading = True
        if tag == "title":
            self._reading_title = True
        if tag in ("div", "section", "article", "p"):
            values = dict(attrs)
            markers = set((values.get("class") or "").split()) | {values.get("id")}
            if self._containers or markers & _PUBLISHER_ABSTRACT_MARKERS:
                self._containers.append(tag)
                self._abstract_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        super().handle_endtag(tag)
        if tag == "h1":
            self._reading_heading = False
        if tag == "title":
            self._reading_title = False
        if self._containers and tag == self._containers[-1]:
            self._containers.pop()
            self._abstract_parts.append(" ")

    def handle_data(self, data: str) -> None:
        super().handle_data(data)
        if self._reading_heading:
            self._heading.append(data)
        if self._reading_title:
            self._window_title.append(data)
        if self._containers:
            self._abstract_parts.append(data)

    @property
    def titles(self) -> tuple[str, ...]:
        return (
            self.metadata.get("citation_title", ""),
            self.metadata.get("dc.title", ""),
            clean_text("".join(self._heading)),
            self.metadata.get("og:title", ""),
            *clean_text("".join(self._window_title)).split(" | "),
        )

    @property
    def explicit_abstract(self) -> str:
        value = (self.metadata.get("citation_abstract") or self.metadata.get("dc.description.abstract")
                 or "".join(self._abstract_parts))
        return clean_text(value).removeprefix("Abstract ")


class AbstractMarkup(HTMLParser):
    """Convert deposited JATS or HTML abstract fields into plain evidence text."""

    def __init__(self, markup: str) -> None:
        super().__init__()
        self._parts: list[str] = []
        self.feed(markup)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.rsplit(":", 1)[-1] in ("p", "sec", "title", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        self.handle_starttag(tag, [])

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    @property
    def text(self) -> str:
        return clean_text("".join(self._parts))
