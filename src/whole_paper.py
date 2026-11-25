"""First-pass whole-paper citation extraction from a converted document.

Only explicit numeric and author-year citations are matched. Unclear matches stay
unresolved; this module never searches for substitute sources or judges support.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from src.citation_parser import CitationParseError, extract_citation_groups, split_claim_segments
from src.document_conversion import ConvertedDocument
from src.document_handoff import CitationHandoff, ReferenceCandidate, SourceLocation


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
    locator: str
    line_number: int
    page: int | None
    bbox: tuple[float, ...] | None


@dataclass(frozen=True)
class _Paragraph:
    text: str
    block_ids: tuple[str, ...]
    source_locations: tuple[SourceLocation, ...]
    lines: tuple[_Line, ...]


@dataclass(frozen=True)
class _Mention:
    marker: str
    start: int = 0
    number: int | None = None
    first_author: str = ""
    second_author: str = ""
    year: str = ""
    unparsed: bool = False


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
_LIKELY_AUTHOR_YEAR = re.compile(r"^[A-ZÀ-ÖØ-Þ][\w'’\-]+(?:\s+et\s+al\.)?\s+(?:19|20)\d{2}[a-z]?$", re.I)
_NUMERIC_LIKE = re.compile(
    r"\[\s*\d+(?:\s*[-–—]\s*\d+)?"
    r"(?:\s*[,;]\s*\d+(?:\s*[-–—]\s*\d+)?)*\s*\]"
    r"(?:\s*[-–—]\s*\[\s*\d+\s*\])?"
)


def _lines(document: ConvertedDocument) -> list[_Line]:
    return [
        _Line(line, block.block_id, block.locator, number, block.page, block.bbox)
        for block in document.blocks
        for number, line in enumerate(block.markdown.splitlines(), 1)
    ]


def _locations(lines: list[_Line]) -> tuple[SourceLocation, ...]:
    locations: list[SourceLocation] = []
    for line in lines:
        if locations and locations[-1].block_id == line.block_id:
            last = locations[-1]
            locations[-1] = SourceLocation(last.block_id, last.locator, last.line_start,
                                           line.line_number, last.page, last.bbox)
        else:
            locations.append(SourceLocation(line.block_id, line.locator, line.line_number,
                                            line.line_number, line.page, line.bbox))
    return tuple(locations)


def _paragraphs(lines: list[_Line]) -> list[_Paragraph]:
    paragraphs: list[_Paragraph] = []
    current: list[_Line] = []
    for line in [*lines, _Line("", "", "", 0, None, None)]:
        if not line.text.strip() or (current and line.block_id != current[-1].block_id):
            if current:
                paragraphs.append(_Paragraph(
                    " ".join(" ".join(item.text.strip() for item in current).split()),
                    tuple(dict.fromkeys(item.block_id for item in current)), _locations(current),
                    tuple(current),
                ))
                current = []
        if line.text.strip():
            current.append(line)
    return paragraphs


def _segment_locations(paragraph: _Paragraph, start: int | None,
                       end: int | None) -> tuple[SourceLocation, ...]:
    if start is None or end is None:
        return paragraph.source_locations
    selected: list[_Line] = []
    offset = 0
    for line in paragraph.lines:
        normalized = " ".join(line.text.split())
        line_end = offset + len(normalized)
        if start < line_end and end > offset:
            selected.append(line)
        offset = line_end + 1
    return _locations(selected) or paragraph.source_locations


def _authors(prefix: str) -> tuple[str, ...]:
    # APA entries usually have "Surname, I." for every author.
    names = re.findall(r"\b([A-ZÀ-ÖØ-Þ][\w'’\-]+),\s*[A-Z]\.", prefix, re.I)
    if not names:
        names = re.findall(r"\b([A-ZÀ-ÖØ-Þ][\w'’\-]+)\b", prefix, re.I)
        names = [name for name in names if name.casefold() not in {"and", "et", "al"}]
    return tuple(names)


def _reference(raw: str, ref_id: str, locations: tuple[SourceLocation, ...],
               author_prefix: str = "", year: str = "") -> ReferenceCandidate:
    doi_match = _DOI.search(raw)
    url_match = _URL.search(raw)
    return ReferenceCandidate(
        ref_id=ref_id, raw_text=raw, authors=_authors(author_prefix) if author_prefix else (),
        year=year, doi=doi_match.group().rstrip(".,)") if doi_match else "",
        url=url_match.group().rstrip(".,)") if url_match else "",
        source_locations=locations,
    )


def _parse_references(lines: list[_Line]) -> tuple[ReferenceCandidate, ...]:
    entries: list[ReferenceCandidate] = []
    current: list[_Line] = []
    current_id = ""
    authors = ""
    year = ""

    def flush() -> None:
        if current:
            entries.append(_reference(" ".join(line.text.strip() for line in current),
                                      current_id, _locations(current), authors, year))

    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        number = _NUMERIC_REF.match(text)
        author = None if number else _AUTHOR_REF.match(text)
        if number or author:
            flush()
            current = [line]
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
            current.append(line)
    flush()
    return tuple(entries)


def _mentions(segment: str) -> tuple[list[_Mention], list[str], list[str]]:
    found: list[_Mention] = []
    seen: set[tuple] = set()
    duplicates: list[str] = []
    invalid: list[str] = []

    def add(mention: _Mention) -> None:
        key = (mention.unparsed, mention.marker if mention.unparsed else "",
               mention.number, mention.first_author.casefold(),
               mention.second_author.casefold(), mention.year)
        if key not in seen:
            found.append(mention)
            seen.add(key)
        else:
            duplicates.append(mention.marker)

    try:
        numeric_groups = extract_citation_groups(segment)
    except CitationParseError:
        numeric_groups = []
        for marker in _NUMERIC_LIKE.finditer(segment):
            try:
                for group in extract_citation_groups(marker.group()):
                    for number in group.numbers:
                        add(_Mention(group.marker, start=marker.start() + group.start,
                                     number=number))
            except CitationParseError:
                add(_Mention(marker.group(), start=marker.start(), unparsed=True))
                invalid.append(marker.group())
    for group in numeric_groups:
        for number in group.numbers:
            add(_Mention(group.marker, start=group.start, number=number))
    for match in _PAREN_CITATION.finditer(segment):
        for item in match["body"].split(";"):
            if parsed := _PAREN_ITEM.fullmatch(item.strip()):
                add(_Mention(match.group(), start=match.start(), first_author=parsed["first"],
                             second_author=parsed["second"] or "", year=parsed["year"]))
            elif _LIKELY_AUTHOR_YEAR.fullmatch(item.strip()):
                add(_Mention(item.strip(), start=match.start(), unparsed=True))
                invalid.append(item.strip())
    for match in _NARRATIVE.finditer(segment):
        if any(parent.start() <= match.start() < parent.end()
               for parent in _PAREN_CITATION.finditer(segment)):
            continue
        add(_Mention(match.group(), start=match.start(), first_author=match["first"],
                     second_author=match["second"] or "", year=match["year"]))
    return sorted(found, key=lambda mention: mention.start), duplicates, invalid


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
        cursor = 0
        for segment in split_claim_segments(paragraph.text):
            start = paragraph.text.find(segment, cursor)
            end = start + len(segment) if start >= 0 else None
            if end is not None:
                cursor = end
            mentions, duplicates, invalid = _mentions(segment)
            for marker in dict.fromkeys(duplicates):
                issues.append(ExtractionIssue("REPEATED_REFERENCE_IN_CLAIM",
                                              f"同一引用陳述重複出現 {marker}，已合併為一筆配對。"))
            for marker in dict.fromkeys(invalid):
                issues.append(ExtractionIssue("UNPARSED_CITATION",
                                              f"無法安全解析引用標記 {marker}，需人工確認。"))
            if not mentions:
                continue
            claim_number += 1
            claim_id = f"claim_{claim_number:04d}"
            for mention in mentions:
                candidates = [] if mention.unparsed else _candidates(mention, references)
                if mention.unparsed:
                    status, reference, candidate_ids = "unparsed", None, ()
                    message = f"無法判定 {mention.marker} 的引用格式或對應 ref。"
                elif len(candidates) == 1:
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
                    source_locations=_segment_locations(paragraph, start if start >= 0 else None, end),
                    paragraph_char_start=start if start >= 0 else None,
                    paragraph_char_end=end,
                ))
    if not pairs:
        issues.append(ExtractionIssue("NO_SUPPORTED_CITATIONS", "正文中未偵測到可解析的數字型或作者年份型引用。"))
    return WholePaperOutcome(document.document_id, section_found, references,
                             tuple(pairs), tuple(issues))
