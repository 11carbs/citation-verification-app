"""First-pass whole-paper citation extraction from a converted document.

Only explicit numeric and author-year citations are matched. Unclear matches stay
unresolved; this module never searches for substitute sources or judges support.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from src.citation_parser import CitationParseError, extract_citation_groups, split_claim_segments
from src.document_conversion import ConvertedDocument
from src.document_handoff import CitationHandoff, ReferenceCandidate


@dataclass(frozen=True)
class ExtractionIssue:
    code: str
    message: str


@dataclass(frozen=True)
class WholePaperOutcome:
    document_id: str
    section_found: bool
    references: tuple[ReferenceCandidate, ...]
    pairs: tuple[CitationHandoff, ...]
    issues: tuple[ExtractionIssue, ...]


@dataclass(frozen=True)
class _Line:
    text: str
    block_id: str


@dataclass(frozen=True)
class _Paragraph:
    text: str
    block_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Mention:
    marker: str
    number: int | None = None
    first_author: str = ""
    second_author: str = ""
    year: str = ""


_REF_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:(?:\d+|[IVX]+)[.)]\s+)?"
    r"(?:references|bibliography|works cited|參考文獻|参考文献)(?:\*\*)?\s*:?\s*$",
    re.I,
)
_NUMERIC_REF = re.compile(r"^\s*(?:\[(?P<bracket>\d+)\]|(?P<plain>\d+)[.)])\s+(?P<entry>\S.*)$")
_AUTHOR_REF = re.compile(
    r"^\s*(?P<authors>[A-ZÀ-ÖØ-Þ][^()\n]{1,120}?)\s*"
    r"\((?P<year>(?:19|20)\d{2}[a-z]?)\)\s*[.,]?\s+\S",
    re.I,
)
_PAREN_CITATION = re.compile(r"\((?P<body>[^()]{1,180})\)")
_PAREN_ITEM = re.compile(
    r"^\s*(?P<first>[A-ZÀ-ÖØ-Þ][\w'’\-]+)"
    r"(?:\s*(?:&|and)\s*(?P<second>[A-ZÀ-ÖØ-Þ][\w'’\-]+)|\s+et\s+al\.)?"
    r"\s*,\s*(?P<year>(?:19|20)\d{2}[a-z]?)(?:\s*,.*)?$",
    re.I,
)
_NARRATIVE = re.compile(
    r"\b(?P<first>[A-ZÀ-ÖØ-Þ][\w'’\-]+)"
    r"(?:\s+(?:&|and)\s+(?P<second>[A-ZÀ-ÖØ-Þ][\w'’\-]+)|\s+et\s+al\.)?"
    r"\s*\((?P<year>(?:19|20)\d{2}[a-z]?)\)",
    re.I,
)
_DOI = re.compile(r"\b10\.\d{4,9}/[^\s,;]+", re.I)
_URL = re.compile(r"https?://[^\s<>]+", re.I)


def _lines(document: ConvertedDocument) -> list[_Line]:
    return [_Line(line, block.block_id) for block in document.blocks
            for line in block.markdown.splitlines()]


def _paragraphs(lines: list[_Line]) -> list[_Paragraph]:
    paragraphs: list[_Paragraph] = []
    parts: list[str] = []
    ids: list[str] = []
    for line in [*lines, _Line("", "")]:
        if not line.text.strip() or (ids and line.block_id != ids[-1]):
            if parts:
                paragraphs.append(_Paragraph(" ".join(parts), tuple(dict.fromkeys(ids))))
                parts, ids = [], []
        if line.text.strip():
            parts.append(line.text.strip())
            ids.append(line.block_id)
    return paragraphs


def _authors(prefix: str) -> tuple[str, ...]:
    # APA entries usually have "Surname, I." for every author.
    names = re.findall(r"\b([A-ZÀ-ÖØ-Þ][\w'’\-]+),\s*[A-Z]\.", prefix, re.I)
    if not names:
        names = re.findall(r"\b([A-ZÀ-ÖØ-Þ][\w'’\-]+)\b", prefix, re.I)
        names = [name for name in names if name.casefold() not in {"and", "et", "al"}]
    return tuple(names)


def _reference(raw: str, ref_id: str, author_prefix: str = "", year: str = "") -> ReferenceCandidate:
    doi_match = _DOI.search(raw)
    url_match = _URL.search(raw)
    return ReferenceCandidate(
        ref_id=ref_id, raw_text=raw, authors=_authors(author_prefix) if author_prefix else (),
        year=year, doi=doi_match.group().rstrip(".,)") if doi_match else "",
        url=url_match.group().rstrip(".,)") if url_match else "",
    )


def _parse_references(lines: list[_Line]) -> tuple[ReferenceCandidate, ...]:
    entries: list[ReferenceCandidate] = []
    current: list[str] = []
    current_id = ""
    authors = ""
    year = ""

    def flush() -> None:
        if current:
            entries.append(_reference(" ".join(current), current_id, authors, year))

    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        number = _NUMERIC_REF.match(text)
        author = None if number else _AUTHOR_REF.match(text)
        if number or author:
            flush()
            current = [text]
            if number:
                base_id = "ref_n_" + (number["bracket"] or number["plain"])
                duplicates = sum(ref.ref_id == base_id or ref.ref_id.startswith(base_id + "_dup")
                                 for ref in entries)
                current_id = base_id if duplicates == 0 else f"{base_id}_dup{duplicates + 1}"
                authors, year = "", ""
            else:
                current_id = f"ref_a_{len(entries) + 1:03d}"
                authors, year = author["authors"], author["year"]
        elif current:
            current.append(text)
    flush()
    return tuple(entries)


def _mentions(segment: str) -> list[_Mention]:
    found: list[_Mention] = []
    seen: set[tuple] = set()

    def add(mention: _Mention) -> None:
        key = (mention.number, mention.first_author.casefold(),
               mention.second_author.casefold(), mention.year)
        if key not in seen:
            found.append(mention)
            seen.add(key)

    for group in extract_citation_groups(segment):
        for number in group.numbers:
            add(_Mention(group.marker, number=number))
    for match in _PAREN_CITATION.finditer(segment):
        for item in match["body"].split(";"):
            if parsed := _PAREN_ITEM.fullmatch(item.strip()):
                add(_Mention(match.group(), first_author=parsed["first"],
                             second_author=parsed["second"] or "", year=parsed["year"]))
    for match in _NARRATIVE.finditer(segment):
        if any(parent.start() <= match.start() < parent.end()
               for parent in _PAREN_CITATION.finditer(segment)):
            continue
        add(_Mention(match.group(), first_author=match["first"],
                     second_author=match["second"] or "", year=match["year"]))
    return found


def _candidates(mention: _Mention, refs: tuple[ReferenceCandidate, ...]) -> list[ReferenceCandidate]:
    if mention.number is not None:
        base_id = f"ref_n_{mention.number}"
        return [ref for ref in refs if ref.ref_id == base_id
                or ref.ref_id.startswith(base_id + "_dup")]
    first = mention.first_author.casefold()
    second = mention.second_author.casefold()
    return [ref for ref in refs if ref.year.casefold() == mention.year.casefold()
            and ref.authors and ref.authors[0].casefold() == first
            and (not second or len(ref.authors) > 1 and ref.authors[1].casefold() == second)]


def extract_whole_paper(document: ConvertedDocument) -> WholePaperOutcome:
    """Extract explicit cited claims and their own bibliography entries."""
    lines = _lines(document)
    headings = [index for index, line in enumerate(lines) if _REF_HEADING.fullmatch(line.text)]
    section_found = bool(headings)
    boundary = headings[-1] if headings else len(lines)
    body = _paragraphs(lines[:boundary])
    references = _parse_references(lines[boundary + 1:]) if section_found else ()
    issues: list[ExtractionIssue] = []
    pairs: list[CitationHandoff] = []
    if not section_found:
        issues.append(ExtractionIssue("NO_REFERENCE_SECTION", "找不到獨立的參考文獻標題；引用會標示為缺少 ref。"))
    elif not references:
        issues.append(ExtractionIssue("NO_PARSED_REFERENCES", "找到參考文獻區，但未辨識出可配對的條目。"))

    claim_number = 0
    for paragraph in body:
        for segment in split_claim_segments(paragraph.text):
            try:
                mentions = _mentions(segment)
            except CitationParseError as error:
                issues.append(ExtractionIssue("INVALID_NUMERIC_CITATION", str(error)))
                continue
            if not mentions:
                continue
            claim_number += 1
            claim_id = f"claim_{claim_number:04d}"
            for mention in mentions:
                candidates = _candidates(mention, references)
                if len(candidates) == 1:
                    status, reference, candidate_ids, message = "matched", candidates[0], (), ""
                elif candidates:
                    status, reference = "ambiguous", None
                    candidate_ids = tuple(ref.ref_id for ref in candidates)
                    message = f"{mention.marker} 對應多筆 ref，需人工確認。"
                else:
                    status, reference, candidate_ids = "missing_reference", None, ()
                    message = f"ref 清單找不到 {mention.marker}。"
                pairs.append(CitationHandoff(
                    document_id=document.document_id, claim_id=claim_id,
                    pair_id=f"pair_{len(pairs) + 1:04d}", citation_group_id=claim_id,
                    claim_text=segment, context=paragraph.text,
                    citation_marker=mention.marker, block_ids=paragraph.block_ids,
                    match_status=status, reference=reference,
                    candidate_ref_ids=candidate_ids, error_message=message,
                ))
    if not pairs:
        issues.append(ExtractionIssue("NO_SUPPORTED_CITATIONS", "正文中未偵測到可解析的數字型或作者年份型引用。"))
    return WholePaperOutcome(document.document_id, section_found, references,
                             tuple(pairs), tuple(issues))
