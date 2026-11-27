"""First-pass whole-paper citation extraction from a converted document.

Only explicit numeric and author-year citations are matched. Unclear matches stay
unresolved; this module never searches for substitute sources or judges support.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from src.citation_parser import CitationParseError, extract_citation_groups, split_claim_segments
from src.document_conversion import ConvertedDocument, _pdf_keep_hyphen
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
    note_key: str = ""
    target_id: str = ""
    first_author: str = ""
    second_author: str = ""
    year: str = ""
    unparsed: bool = False
    uncertain_numeric: bool = False


_REF_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:(?:\d+|[IVX]+)[.)]\s+)?"
    r"(?:references|bibliography|bibliographical references|works cited|參考文獻|参考文献)(?:\*\*)?\s*:?\s*$",
    re.I,
)
_NUMERIC_REF = re.compile(r"^\s*(?:\[(?P<bracket>\d+)\]|(?P<plain>[1-9]\d{0,2})[.)])(?:\s+(?P<entry>\S.*))?\s*$")
_PDF_LICENSE_FOOTER = re.compile(
    r"^(?:Authorized licensed use limited to:|Downloaded on .+ from IEEE Xplore\.|Restrictions apply\.)",
    re.I,
)
_REFERENCE_END_HEADING = re.compile(
    r"^(?:author biographies|biographies|about the authors|author information|作者簡介|著者簡介)$",
    re.I,
)
_REFERENCE_END_NOTICE = re.compile(r"^How to cite this article\s*:", re.I)
_TABLE_CAPTION = re.compile(r"^\s*Table\s+\d+[.:]", re.I)
_APPENDIX_HEADING = re.compile(r"^[A-Z]\s+[A-Z][A-Za-z][A-Za-z ]{6,80}$")
_AUTHOR_BIO_START = re.compile(
    r"^[A-Z][A-Za-z .'-]{2,80}(?:\s*\([^)]{1,45}\))?\s+received\s+(?:his|her|the|a)\b",
    re.I,
)
_NOTE_REF = re.compile(r"^\s*\[\^(?P<key>[A-Za-z0-9_-]+)\]:\s*(?P<entry>\S.*)$")
_NOTE_MARKER = re.compile(r"\[\^(?P<key>[A-Za-z0-9_-]+)\](?!:)")
_STRUCT_REF = re.compile(r"^\s*\[@(?P<key>[A-Za-z0-9_-]+)\](?:\s+(?P<entry>\S.*))?\s*$")
_STRUCT_MARKER = re.compile(r"\[@(?P<key>[A-Za-z0-9_-]+)\]")
_SUPERSCRIPT = re.compile(r"\^\{(?P<body>\d+(?:\s*[,;–-]\s*\d+)*)\}")
_PAREN_NUMERIC = re.compile(r"\((?P<body>\d{1,3}(?:\s*[-–—]\s*\d{1,3})?"
                            r"(?:\s*[,;]\s*\d{1,3}(?:\s*[-–—]\s*\d{1,3})?)*)\)")
_UNICODE_SUP = re.compile(r"(?<=[.!?。！？])(?P<body>[⁰¹²³⁴⁵⁶⁷⁸⁹]+)(?=[.,;:!?。！？\s]|$)")
_SUP_TRANSLATION = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_AUTHOR_REF = re.compile(
    r"^\s*(?P<authors>[A-ZÀ-ÖØ-Þ][^()\n]{1,450}?)\s*"
    r"\((?P<year>(?:19|20)\d{2}[a-z]?)\)\s*[.,]?\s+\S",
    re.I,
)
_AUTHOR_LIST_START = re.compile(r"^[A-ZÀ-ÖØ-Þ][\w'’\-]+,\s*[A-Z](?:\.|[A-Z]?\.)")
_ACL_REF = re.compile(
    r"^\s*(?P<authors>[A-ZÀ-ÖØ-Þ][^\n]{2,450}?)\.\s+"
    r"(?P<year>(?:19|20)\d{2}[a-z]?)\.\s+\S",
)
_SURNAME = (
    r"(?:(?:van(?:\s+der)?|von|de(?:\s+la)?|del|da|di|le|la)\s+)?"
    r"[A-ZÀ-ÖØ-Þ][\w'’\-]+"
)
_PAREN_CITATION = re.compile(r"(?:\((?P<body>[^()]{1,180})\)|（(?P<wide_body>[^（）]{1,180})）)")
_PAREN_ITEM = re.compile(
    rf"^\s*(?P<first>{_SURNAME})"
    rf"(?:\s*(?:&|and)\s*(?P<second>{_SURNAME})|\s+et\s+al\.|\s+等人)?"
    r"\s*[,，]\s*(?P<year>(?:19|20)\d{2}[a-z]?)(?:\s*[,，].*)?$",
    re.I,
)
_NARRATIVE = re.compile(
    rf"\b(?P<first>{_SURNAME})"
    rf"(?:\s+(?:&|and)\s+(?P<second>{_SURNAME})|\s+et\s+al\.|\s+等人)?"
    r"\s*[（(](?P<year>(?:19|20)\d{2}[a-z]?)[）)]",
    re.I,
)
_QUOTED_EXAMPLE = re.compile(r'(?:“[^”]*”|"[^"]*"|‘[^’]*’)', re.S)
_EXAMPLE_PREFIX = re.compile(r"(?:for example|e\.g\.|such as|like)\s*,?\s*$", re.I)
_DOI = re.compile(r"\b10\.\d{4,9}/[^\s,;]+", re.I)
_URL = re.compile(r"https?://[^\s<>]+", re.I)
_LIKELY_AUTHOR_YEAR = re.compile(r"^[A-ZÀ-ÖØ-Þ][\w'’\-]+(?:\s+et\s+al\.)?\s+(?:19|20)\d{2}[a-z]?$", re.I)
_NUMERIC_LIKE = re.compile(
    r"\[\s*\d+(?:\s*[-–—]\s*\d+)?"
    r"(?:\s*[,;]\s*\d+(?:\s*[-–—]\s*\d+)?)*\s*\]"
    r"(?:\s*[-–—]\s*\[\s*\d+\s*\])?"
)
_SECTION_NAME = (
    r"Abstract|Summary|Background|Introduction|Related Work|Literature Review|"
    r"Methods?|Materials and Methods|Methodology|Results?|Results and Discussion|"
    r"Discussion|Conclusions?|Evaluation|Limitations|Future Work|"
    r"摘要|背景|前言|引言|相關研究|文獻探討|研究方法|方法|結果|討論|結論|限制"
)
_SECTION_HEADING = re.compile(
    rf"^(?:#{{1,6}}\s*)?(?:\d+(?:\.\d+)*[.)]?\s+)?(?:{_SECTION_NAME})\s*[:：]?\s*$",
    re.I,
)
_INLINE_SECTION_LABEL = re.compile(
    rf"^(?:{_SECTION_NAME})\s*[:：]\s+(?=\S)", re.I,
)


def _lines(document: ConvertedDocument) -> list[_Line]:
    return [
        _Line(line, block.block_id, block.locator, number, block.page, block.bbox)
        for block in document.blocks
        for number, line in enumerate(block.markdown.splitlines(), 1)
    ]


def _table_block_ids(document: ConvertedDocument) -> set[str]:
    """Exclude PDF table cells when overlapping text blocks lead to a caption."""
    if document.source_format != "pdf":
        return set()
    excluded: set[str] = set()
    blocks = [block for block in document.blocks if block.page and block.bbox]
    for caption in blocks:
        if not _TABLE_CAPTION.match(caption.markdown):
            continue
        candidates = [
            block for block in blocks if block.page == caption.page
            and block.block_id != caption.block_id
            and caption.bbox[1] - 450 <= block.bbox[1] < caption.bbox[1]
            and block.bbox[3] <= caption.bbox[1] + 4
        ]
        neighbors: dict[str, set[str]] = {block.block_id: set() for block in candidates}
        for index, left in enumerate(candidates):
            for right in candidates[index + 1:]:
                horizontal = min(left.bbox[2], right.bbox[2]) - max(left.bbox[0], right.bbox[0])
                vertical = min(left.bbox[3], right.bbox[3]) - max(left.bbox[1], right.bbox[1])
                if horizontal > 5 and vertical > 5:
                    neighbors[left.block_id].add(right.block_id)
                    neighbors[right.block_id].add(left.block_id)
        by_id = {block.block_id: block for block in candidates}
        seen: set[str] = set()
        for block in candidates:
            if block.block_id in seen or not neighbors[block.block_id]:
                continue
            component = {block.block_id}
            seen.add(block.block_id)
            pending = [block.block_id]
            while pending:
                for item in neighbors[pending.pop()] - seen:
                    seen.add(item)
                    component.add(item)
                    pending.append(item)
            if max(by_id[item].bbox[3] for item in component) >= caption.bbox[1] - 50:
                excluded.update(component)
    return excluded


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
    for original in [*lines, _Line("", "", "", 0, None, None)]:
        heading = bool(_SECTION_HEADING.fullmatch(original.text.strip()))
        if (not original.text.strip() or heading
                or (current and original.block_id != current[-1].block_id)):
            if current:
                paragraphs.append(_Paragraph(
                    " ".join(" ".join(item.text.strip() for item in current).split()),
                    tuple(dict.fromkeys(item.block_id for item in current)), _locations(current),
                    tuple(current),
                ))
                current = []
        if original.text.strip() and not heading:
            line = original
            if not current:
                cleaned = _INLINE_SECTION_LABEL.sub("", line.text.strip())
                if cleaned != line.text.strip():
                    line = _Line(cleaned, line.block_id, line.locator, line.line_number,
                                 line.page, line.bbox)
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
    names = re.findall(rf"\b({_SURNAME}),\s*[A-Z]\.", prefix, re.I)
    if not names:
        names = re.findall(r"\b([A-ZÀ-ÖØ-Þ][\w'’\-]+)\b", prefix, re.I)
        names = [name for name in names if name.casefold() not in {"and", "et", "al"}]
    return tuple(names)


def _acl_authors(prefix: str) -> tuple[str, ...]:
    names = []
    for person in re.split(r"\s*,\s*|\s+and\s+", prefix):
        words = re.findall(r"[A-ZÀ-ÖØ-Þ][\w'’\-]+", person, re.I)
        if words:
            names.append(words[-1])
    return tuple(names)


def _reference(raw: str, ref_id: str, locations: tuple[SourceLocation, ...],
               author_prefix: str = "", year: str = "", acl: bool = False) -> ReferenceCandidate:
    doi_match = _DOI.search(raw)
    url_match = _URL.search(raw)
    return ReferenceCandidate(
        ref_id=ref_id, raw_text=raw,
        authors=(_acl_authors(author_prefix) if acl else _authors(author_prefix)) if author_prefix else (),
        year=year, doi=doi_match.group().rstrip(".,)") if doi_match else "",
        url=url_match.group().rstrip(".,)") if url_match else "",
        source_locations=locations,
    )


def _repair_pdf_reference_url_gaps(raw: str) -> str:
    """Join URL fragments separated only by PDF line breaks."""
    raw = re.sub(r"\bhtt-\s+ps://", "https://", raw, flags=re.I)
    raw = re.sub(r"(https?://\S*[/])\s+(?=[A-Za-z0-9])", r"\1", raw, flags=re.I)
    raw = re.sub(r"(https?://\S*[-])\s+(?=[A-Za-z0-9])", r"\1", raw, flags=re.I)
    raw = re.sub(
        r"(https?://\S+\.)\s+(?=(?:com|org|net|edu|gov|io|ai|app|dev)(?:/|\b)|[A-Za-z0-9-]+\.[A-Za-z]{2,}(?:/|\b))",
        r"\1", raw, flags=re.I,
    )
    return raw


def _join_reference_lines(lines: list[_Line]) -> str:
    """Join PDF reference lines after their entry boundary is known."""
    result = ""
    known = {f"{left}-{right}".casefold() for line in lines
             for left, right in re.findall(r"\b([A-Za-z]{2,})-([A-Za-z]{2,})\b",
                                           line.text)}
    for line in lines:
        part = line.text.strip()
        if not part:
            continue
        ending = re.search(r"(?P<left>[A-Za-zÀ-ÖØ-öø-ÿ0-9]{1,30})-$", result)
        beginning = re.match(r"(?P<right>[A-Za-zÀ-ÖØ-öø-ÿ0-9]{1,30})", part)
        if line.page is not None and ending and beginning:
            left, right = ending["left"], beginning["right"]
            url = re.search(r"https?://\S*$", result[:ending.start()], re.I)
            separator = "-" if url or _pdf_keep_hyphen(left, right, known) else ""
            result = result[:-1] + separator + part
        else:
            result = f"{result} {part}" if result else part
    return result


def _parse_references(lines: list[_Line]) -> tuple[ReferenceCandidate, ...]:
    entries: list[ReferenceCandidate] = []
    current: list[_Line] = []
    current_id = ""
    authors = ""
    year = ""
    acl = False
    layout = False
    numeric_style = sum(bool(_NUMERIC_REF.match(line.text.strip())) for line in lines[:30]) >= 2
    structural_style = sum(bool(_STRUCT_REF.match(line.text.strip())) for line in lines[:30]) >= 2
    acl_style = any(_ACL_REF.match(line.text.strip()) for line in lines[:3])
    first_x = next((line.bbox[0] for line in lines if line.bbox and line.line_number == 1), None)
    layout_style = bool(
        first_x is not None and not (numeric_style or structural_style or acl_style)
        and sum(bool(line.bbox and line.line_number == 1
                     and abs(line.bbox[0] - first_x) < 3) for line in lines[:45]) >= 4
        and sum(bool(line.bbox and line.line_number == 1
                     and line.bbox[0] > first_x + 7) for line in lines[:45]) >= 2
    )
    last_numeric_index = max(
        (index for index, line in enumerate(lines) if _NUMERIC_REF.match(line.text.strip())),
        default=-1,
    )

    def flush() -> None:
        if current:
            raw = _join_reference_lines(current)
            if any(line.page is not None for line in current):
                raw = _repair_pdf_reference_url_gaps(raw)
            entry_year = year
            if layout:
                bibliography = re.split(r"\b(?:URL|doi:)\s+", raw, maxsplit=1, flags=re.I)[0]
                years = re.findall(r"(?<!\d)(?:19|20)\d{2}[a-z]?(?!\d)", bibliography)
                entry_year = years[-1] if years else ""
            entries.append(_reference(raw, current_id, _locations(current), authors, entry_year,
                                      acl or layout))

    for index, line in enumerate(lines):
        text = line.text.strip()
        if not text:
            continue
        if (line.page is not None and line.bbox and re.fullmatch(r"\d{1,5}", text)
                and line.bbox[1] > 740):
            continue  # Printed page number at the bottom margin.
        if _PDF_LICENSE_FOOTER.match(text):
            # A repeated PDF download/license line is never bibliography text.
            # After the final numbered item, it also marks the end of the list.
            if index > last_numeric_index:
                break
            continue
        if _REFERENCE_END_HEADING.fullmatch(text):
            break
        if _REFERENCE_END_NOTICE.match(text):
            break
        next_line = lines[index + 1] if index + 1 < len(lines) else None
        if (entries and line.page is not None and (
                _APPENDIX_HEADING.fullmatch(text)
                or (re.fullmatch(r"[A-Z]", text) and next_line
                    and next_line.block_id == line.block_id
                    and _APPENDIX_HEADING.fullmatch(text + " " + next_line.text.strip())))):
            break
        if (numeric_style and index > last_numeric_index and current and line.page
                and current[-1].page and line.page > current[-1].page
                and _AUTHOR_BIO_START.match(text)):
            break
        secondary_heading = re.match(r"^\d+[.)]\s+Language Resource References\s*$", text, re.I)
        if (not secondary_heading and re.fullmatch(r"\d+[.)]", text)
                and index + 1 < len(lines)
                and re.fullmatch(r"Language Resource References", lines[index + 1].text.strip(), re.I)):
            secondary_heading = True
        if secondary_heading:
            flush()
            current = []
            continue
        number = _NUMERIC_REF.match(text)
        if number and 1900 <= int(number["bracket"] or number["plain"]) <= 2099:
            number = None  # A broken year/DOI line is not a bibliography number.
        if number and number["plain"] and (number["entry"] or "").lower().startswith(("http://", "https://")):
            number = None  # A DOI/URL line fragment is not a new reference.
        structural = None if number else _STRUCT_REF.match(text)
        author = None if numeric_style or structural_style or acl_style or number or structural else _AUTHOR_REF.match(text)
        apa_lookahead = _join_reference_lines(lines[index:index + 7])
        acl_window = [line]
        for next_line in lines[index + 1:index + 40]:
            if (line.page is not None and next_line.bbox and line.bbox
                    and next_line.line_number == 1
                    and next_line.block_id != line.block_id
                    and (next_line.page != line.page
                         or next_line.bbox[0] <= line.bbox[0] + 5)):
                break  # The next outdented PDF block begins another entry.
            acl_window.append(next_line)
        acl_lookahead = _join_reference_lines(acl_window)
        if not author and not (numeric_style or structural_style or acl_style or number or structural):
            if _AUTHOR_LIST_START.match(text):
                author = _AUTHOR_REF.match(apa_lookahead)
        if (author and current and year == author["year"]
                and not re.search(r"\((?:19|20)\d{2}[a-z]?\)",
                                  " ".join(item.text for item in current), re.I)):
            author = None  # The year belongs to an author list spanning lines.
        acl_author = None
        if not (numeric_style or structural_style or number or structural or author
                or text.lower().endswith("references")):
            continuation_indent = bool(
                line.bbox and current and current[-1].bbox
                and line.page == current[-1].page
                and line.bbox[1] >= current[-1].bbox[1] - 5
                and line.bbox[0] > current[-1].bbox[0] + 5
            )
            can_start = not continuation_indent and (line.page is None or line.line_number == 1)
            acl_author = _ACL_REF.match(text) if can_start else None
            # ACL bibliographies often end an author/year block with a period,
            # then put the title in the next PDF block.  A new entry is also
            # commonly outdented from the previous entry's continuation.
            outdented = bool(
                line.bbox and current and current[-1].bbox
                and line.page == current[-1].page
                and line.bbox[0] + 5 < current[-1].bbox[0]
            )
            year_line = bool(re.search(r"\b(?:19|20)\d{2}[a-z]?\.\s*$", text))
            next_year_line = bool(
                index + 1 < len(lines)
                and re.match(r"^(?:19|20)\d{2}[a-z]?\.\s+\S",
                             lines[index + 1].text.strip())
            )
            if not acl_author and can_start and (
                    not text.endswith(".") or year_line or next_year_line
                    or outdented or not current):
                acl_author = _ACL_REF.match(acl_lookahead)
            if (acl_author and current and len(current) <= 2 and year == acl_author["year"]
                    and not _ACL_REF.match(current[0].text)):
                acl_author = None  # Continuation of an author list split by a PDF block.
        layout_start = bool(
            layout_style and line.bbox and line.line_number == 1
            and abs(line.bbox[0] - first_x) < 3
        )
        if number or structural or author or acl_author or layout_start:
            flush()
            current = [line]
            layout = bool(layout_start and not (number or structural or author or acl_author))
            if number:
                base_id = "ref_n_" + (number["bracket"] or number["plain"])
                duplicates = sum(ref.ref_id == base_id or ref.ref_id.startswith(base_id + "_dup")
                                 for ref in entries)
                current_id = base_id if duplicates == 0 else f"{base_id}_dup{duplicates + 1}"
                authors, year, acl = "", "", False
            elif structural:
                base_id = "ref_struct_" + structural["key"]
                duplicates = sum(ref.ref_id == base_id or ref.ref_id.startswith(base_id + "_dup")
                                 for ref in entries)
                current_id = base_id if duplicates == 0 else f"{base_id}_dup{duplicates + 1}"
                authors, year, acl = "", "", False
            else:
                current_id = f"ref_a_{len(entries) + 1:03d}"
                match = author or acl_author
                if match:
                    authors, year, acl = match["authors"], match["year"], bool(acl_author)
                else:
                    first_author = text.split(",", 1)[0].split(".", 1)[0]
                    first_author = re.sub(r"\s+et\s+al$", "", first_author, flags=re.I)
                    first_author = re.sub(r"(?<=[A-Za-z])(?:˘|´|`|˜)(?=[A-Za-z])", "", first_author)
                    authors, year, acl = first_author, "", False
        elif current:
            current.append(line)
    flush()
    return tuple(entries)


def _parse_note_references(lines: list[_Line]) -> tuple[ReferenceCandidate, ...]:
    notes: list[ReferenceCandidate] = []
    for line in lines:
        if match := _NOTE_REF.match(line.text):
            base_id = "ref_note_" + match["key"]
            duplicates = sum(ref.ref_id == base_id or ref.ref_id.startswith(base_id + "_dup")
                             for ref in notes)
            ref_id = base_id if duplicates == 0 else f"{base_id}_dup{duplicates + 1}"
            notes.append(_reference(match["entry"], ref_id, _locations([line])))
    return tuple(notes)


def _mentions(segment: str, *, allow_parenthesized_numeric: bool = True) -> tuple[list[_Mention], list[str], list[str]]:
    found: list[_Mention] = []
    seen: set[tuple] = set()
    duplicates: list[str] = []
    invalid: list[str] = []

    def add(mention: _Mention) -> None:
        key = (mention.unparsed, mention.marker if mention.unparsed else "",
               mention.number, mention.note_key, mention.target_id,
               mention.first_author.casefold(),
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
    for match in _SUPERSCRIPT.finditer(segment):
        before = segment[:match.start()].rstrip()
        if re.search(r"(?:^|\W)[A-Za-z]$", before):
            continue  # Common mathematical exponent, e.g. x^{2}.
        try:
            for group in extract_citation_groups("[" + match["body"] + "]"):
                for number in group.numbers:
                    add(_Mention(match.group(), start=match.start(), number=number,
                                 uncertain_numeric=True))
        except CitationParseError:
            add(_Mention(match.group(), start=match.start(), unparsed=True))
            invalid.append(match.group())
    for match in _UNICODE_SUP.finditer(segment):
        add(_Mention(match.group(), start=match.start(),
                     number=int(match["body"].translate(_SUP_TRANSLATION)),
                     uncertain_numeric=True))
    for match in _PAREN_NUMERIC.finditer(segment) if allow_parenthesized_numeric else ():
        if re.search(r"\b(?:Eq(?:uation)?|Fig(?:ure)?|Table)\.?\s*$",
                     segment[:match.start()], re.I):
            continue
        try:
            for group in extract_citation_groups("[" + match["body"] + "]"):
                for number in group.numbers:
                    add(_Mention(match.group(), start=match.start(), number=number,
                                 uncertain_numeric=True))
        except CitationParseError:
            add(_Mention(match.group(), start=match.start(), unparsed=True))
            invalid.append(match.group())
    for match in _NOTE_MARKER.finditer(segment):
        add(_Mention(match.group(), start=match.start(), note_key=match["key"]))
    for match in _STRUCT_MARKER.finditer(segment):
        add(_Mention(match.group(), start=match.start(), target_id=match["key"]))
    for match in _PAREN_CITATION.finditer(segment):
        for item in (match["body"] or match["wide_body"]).split(";"):
            if parsed := _PAREN_ITEM.fullmatch(item.strip()):
                add(_Mention(match.group(), start=match.start(), first_author=parsed["first"],
                             second_author=parsed["second"] or "", year=parsed["year"]))
            elif (_LIKELY_AUTHOR_YEAR.fullmatch(item.strip())
                  and not item.strip().split()[0].isupper()):
                add(_Mention(item.strip(), start=match.start(), unparsed=True))
                invalid.append(item.strip())
    for match in _NARRATIVE.finditer(segment):
        if any(parent.start() <= match.start() < parent.end()
               for parent in _PAREN_CITATION.finditer(segment)):
            continue
        if any(mention.number is not None and mention.marker.startswith("[")
               and mention.start >= match.end()
               and re.fullmatch(r"[\w\s-]{0,40}", segment[match.end():mention.start])
               and len(segment[match.end():mention.start].split()) <= 3
               for mention in found):
            # "Wadden et al. (2020) [8]" and "Jurgens et al. (2018)
            # taxonomy [13]" each point to one numbered bibliography entry.
            continue
        add(_Mention(match.group(), start=match.start(), first_author=match["first"],
                     second_author=match["second"] or "", year=match["year"]))
    illustrative_spans = [
        (quote.start(), quote.end()) for quote in _QUOTED_EXAMPLE.finditer(segment)
        if _EXAMPLE_PREFIX.search(segment[:quote.start()])
    ]
    found = [mention for mention in found if not any(
        start <= mention.start < end for start, end in illustrative_spans
    )]
    return sorted(found, key=lambda mention: mention.start), duplicates, invalid


def _candidates(mention: _Mention, refs: tuple[ReferenceCandidate, ...]) -> list[ReferenceCandidate]:
    if mention.target_id:
        base_id = "ref_struct_" + mention.target_id
        return [ref for ref in refs if ref.ref_id == base_id
                or ref.ref_id.startswith(base_id + "_dup")]
    if mention.note_key:
        base_id = "ref_note_" + mention.note_key
        return [ref for ref in refs if ref.ref_id == base_id
                or ref.ref_id.startswith(base_id + "_dup")]
    if mention.number is not None:
        base_id = f"ref_n_{mention.number}"
        return [ref for ref in refs if ref.ref_id == base_id
                or ref.ref_id.startswith(base_id + "_dup")]
    # ACL entries may give a full name ("Teixeira da Silva") while the
    # in-text citation uses only the family name ("da Silva").
    first = mention.first_author.casefold().split()[-1]
    second = mention.second_author.casefold().split()[-1] if mention.second_author else ""
    return [ref for ref in refs if ref.year.casefold() == mention.year.casefold()
            and ref.authors and ref.authors[0].casefold().split()[-1] == first
            and (not second or len(ref.authors) > 1
                 and ref.authors[1].casefold().split()[-1] == second)]


def extract_whole_paper(document: ConvertedDocument) -> WholePaperOutcome:
    """Extract explicit cited claims and their own bibliography entries."""
    all_lines = _lines(document)
    note_lines = [line for line in all_lines if _NOTE_REF.match(line.text)]
    lines = [line for line in all_lines if not _NOTE_REF.match(line.text)]
    headings = [index for index, line in enumerate(lines) if _REF_HEADING.fullmatch(line.text)]
    note_references = _parse_note_references(note_lines)
    section_found = bool(headings or note_references)
    boundary = headings[-1] if headings else len(lines)
    table_blocks = _table_block_ids(document)
    body = _paragraphs([line for line in lines[:boundary]
                        if line.block_id not in table_blocks])
    reference_lines = lines[boundary + 1:] if headings else []
    for index, line in enumerate(reference_lines):
        if re.match(r"^\s*(?:APPENDIX\b|APPENDICES\b)", line.text, re.I):
            reference_lines = reference_lines[:index]
            break
    parsed_references = (_parse_references(reference_lines) if headings else ()) + note_references
    incomplete_references = tuple(
        ref for ref in parsed_references
        if re.fullmatch(r"(?:\[\d+\]|\[@[A-Za-z0-9_-]+\])", ref.raw_text.strip())
    )
    references = tuple(ref for ref in parsed_references if ref not in incomplete_references)
    # Parenthesized numerals are valid in Vancouver-style papers, but in a
    # bibliography labelled [1], [2] they are usually list steps or ranges.
    parenthesized_numeric_references = any(
        re.match(r"^[1-9]\d{0,2}[.)]\s+", ref.raw_text)
        for ref in references if ref.ref_id.startswith("ref_n_")
    )
    issues: list[ExtractionIssue] = []
    if table_blocks:
        issues.append(ExtractionIssue(
            "TABLE_BLOCKS_EXCLUDED",
            f"已排除 {len(table_blocks)} 個表格文字區塊，避免將資料集範例當成本文引用。",
        ))
    pairs: list[CitationHandoff] = []
    for ref in incomplete_references:
        issues.append(ExtractionIssue(
            "INCOMPLETE_REFERENCE_ENTRY",
            f"{ref.ref_id} 只有引用標記、沒有書目內容，已排除自動配對；請核對原始文件。",
        ))
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
            mentions, duplicates, invalid = _mentions(
                segment, allow_parenthesized_numeric=parenthesized_numeric_references,
            )
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
                elif mention.uncertain_numeric:
                    status, reference, candidate_ids = "unparsed", None, ()
                    message = f"{mention.marker} 可能是上標、註腳、公式或數字型引用；無可確認的 ref。"
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
