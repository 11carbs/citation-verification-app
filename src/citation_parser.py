"""Parse IEEE-style numeric in-text citations from user-provided text."""

from __future__ import annotations

from dataclasses import dataclass
import re


class CitationParseError(ValueError):
    """Raised when a numeric citation marker is structurally invalid."""


@dataclass(frozen=True, slots=True)
class CitationGroup:
    """One visible marker or range and the reference numbers it denotes."""

    marker: str
    numbers: tuple[int, ...]
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class CitationMention:
    """One cited reference number and the original marker containing it."""

    ref_number: int
    marker: str


@dataclass(frozen=True, slots=True)
class ClaimCitations:
    """A sentence-like claim segment and all citations attached to it."""

    claim_text: str
    mentions: tuple[CitationMention, ...]
    duplicate_numbers: tuple[int, ...] = ()
    sentence_is_compound: bool = False
    subclaims: tuple[str, ...] = ()
    original_claim_text: str = ""

    @property
    def is_compound(self) -> bool:
        return self.sentence_is_compound or len(self.mentions) > 1


_CROSS_BRACKET_RANGE_RE = re.compile(
    r"\[\s*(?P<start>\d+)\s*\]\s*[-–—]\s*\[\s*(?P<end>\d+)\s*\]"
)
_BRACKET_GROUP_RE = re.compile(
    r"\[\s*(?P<body>"
    r"\d+(?:\s*[-–—]\s*\d+)?"
    r"(?:\s*[,;]\s*\d+(?:\s*[-–—]\s*\d+)?)*"
    r")\s*\]"
)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?。！？])\s+")
_CITED_CLAUSE_BOUNDARY_RE = re.compile(
    r"(?<=\])\s*(?:,\s+(?:while|whereas)\s+|,?\s+(?P<and>and)\s+|;\s+)", re.I,
)
# Limit shared predicates to simple type assertions about two named subjects.
# Relations, comparisons, and qualifications stay in the original sentence.
_NAMED_SUBJECT = r"[A-Z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*"
_TYPE_PHRASE = (
    r"(?:(?:pre-?trained|large|scientific|deep|neural|convolutional|"
    r"bidirectional|multilingual|generative|autoregressive|open-source|"
    r"(?:un|self-)?supervised|transformer-based) )*"
    r"(?:(?:language|vision|machine learning|deep learning) )?"
    r"(?:model|network|algorithm|dataset|benchmark|method|system|architecture|tool|"
    r"framework|protocol|standard|classifier|encoder|decoder)"
)
_SHARED_TYPE_RE = re.compile(
    rf"(?P<first>{_NAMED_SUBJECT}) and (?P<second>{_NAMED_SUBJECT}) are (?P<kind>{_TYPE_PHRASE})s"
)
_SINGLE_TYPE_RE = re.compile(
    rf"{_NAMED_SUBJECT} is (?:a|an) {_TYPE_PHRASE}"
)
_TYPE_CLAUSE_SEPARATOR_RE = re.compile(r";\s+|,\s+and\s+|\s+and\s+")
_DEPENDENT_CLAUSE_START_RE = re.compile(
    r"^(?:[a-z]+ing|only|not|also|even|to|for|from|during|under|at|by|with|without|in|on|"
    r"it|they|this|these|those|such|that|which|whose)\b", re.I,
)
_PARAGRAPH_BREAK_RE = re.compile(r"\n[ \t]*\n")
_LINE_HYPHEN_RE = re.compile(r"-[ \t]*\n[ \t]*(?=[a-z])")
# A period after these words does not end a sentence.
_ABBREVIATIONS = {
    "al", "e.g", "i.e", "etc", "cf", "vs", "fig", "figs", "eq", "eqs", "ref", "refs",
    "sec", "ch", "no", "vol", "pp", "approx", "resp", "dr", "prof",
}
_PART_RE = re.compile(r"^(?P<start>\d+)(?:\s*[-–—]\s*(?P<end>\d+))?$")
_MAX_EXPANDED_RANGE = 500


def extract_citation_groups(text: str) -> list[CitationGroup]:
    """Extract IEEE citation groups while preserving their original markers.

    Supported examples include ``[79]``, ``[47], [107]``, ``[1, 3, 5]``,
    ``[47]-[50]`` and ``[47-50]``. Non-numeric brackets such as ``[Online]``
    are ignored.
    """

    groups: list[CitationGroup] = []
    occupied: list[tuple[int, int]] = []

    for match in _CROSS_BRACKET_RANGE_RE.finditer(text):
        start_number = int(match.group("start"))
        end_number = int(match.group("end"))
        groups.append(
            CitationGroup(
                marker=match.group(0),
                numbers=_expand_range(start_number, end_number),
                start=match.start(),
                end=match.end(),
            )
        )
        occupied.append((match.start(), match.end()))

    for match in _BRACKET_GROUP_RE.finditer(text):
        if _overlaps(match.start(), match.end(), occupied):
            continue
        groups.append(
            CitationGroup(
                marker=match.group(0),
                numbers=_parse_bracket_body(match.group("body")),
                start=match.start(),
                end=match.end(),
            )
        )

    return sorted(groups, key=lambda group: group.start)


def extract_citation_numbers(text: str) -> list[int]:
    """Return cited numbers once each, preserving first-appearance order."""

    numbers: list[int] = []
    seen: set[int] = set()
    for group in extract_citation_groups(text):
        for number in group.numbers:
            if number not in seen:
                seen.add(number)
                numbers.append(number)
    return numbers


def extract_claim_citations(text: str) -> list[ClaimCitations]:
    """Return cited sentences, narrowed to clauses when their citation scopes are clear."""

    claims: list[ClaimCitations] = []
    for segment in _split_claim_segments(text):
        groups = extract_citation_groups(segment)
        if not groups:
            continue
        sentence_is_compound = len({number for group in groups for number in group.numbers}) > 1
        for claim, claim_groups in _scope_cited_clauses(segment, groups):
            subclaims = _shared_subclaims(segment, groups) if sentence_is_compound and claim == segment else ()
            claims.append(
                _claim_citations(
                    claim, claim_groups, sentence_is_compound, subclaims,
                    original_claim_text=segment if subclaims or claim != segment else "",
                )
            )
    return claims


def _shared_subclaims(segment: str, groups: list[CitationGroup]) -> tuple[str, ...]:
    if segment[groups[-1].end :].strip(" \t."):
        return ()
    if any(
        segment[left.end : right.start].strip(" \t,;")
        for left, right in zip(groups, groups[1:])
    ):
        return ()
    statement = segment[: groups[0].start].rstrip()
    match = _SHARED_TYPE_RE.fullmatch(statement)
    if match is not None and match["first"].casefold() != match["second"].casefold():
        kind = match["kind"]
        article = "an" if kind.startswith(("a", "e", "i", "o", "u")) else "a"
        return (f"{match['first']} is {article} {kind}.", f"{match['second']} is {article} {kind}.")
    clauses = _TYPE_CLAUSE_SEPARATOR_RE.split(statement)
    if len(set(clauses)) > 1 and all(_SINGLE_TYPE_RE.fullmatch(clause) for clause in clauses):
        return tuple(f"{clause}." for clause in dict.fromkeys(clauses))
    return ()


def _scope_cited_clauses(segment: str, groups: list[CitationGroup]) -> list[tuple[str, list[CitationGroup]]]:
    # Only split at an explicit boundary directly after a numeric citation and
    # when the final clause also ends in a citation. Keep shared/ambiguous scopes.
    if len(groups) < 2 or segment[groups[-1].end :].strip(" \t.!?。！？"):
        return [(segment, groups)]

    boundaries = {match.start(): match for match in _CITED_CLAUSE_BOUNDARY_RE.finditer(segment)}
    scoped: list[tuple[str, list[CitationGroup]]] = []
    text_start = group_start = 0
    for index, group in enumerate(groups[:-1]):
        boundary = boundaries.get(group.end)
        if boundary is None:
            continue
        before = segment[text_start : group.start]
        after = segment[boundary.end() : groups[index + 1].start].strip()
        if not any(char.isalpha() for char in before) or not any(char.isalpha() for char in after):
            continue
        if _DEPENDENT_CLAUSE_START_RE.match(after):
            continue
        if boundary["and"] and not (
            _SINGLE_TYPE_RE.fullmatch(before.partition("[")[0].strip())
            and _SINGLE_TYPE_RE.fullmatch(after.strip())
        ):
            continue
        scoped.append((segment[text_start : group.end], groups[group_start : index + 1]))
        text_start, group_start = boundary.end(), index + 1
    scoped.append((segment[text_start:], groups[group_start:]))
    return scoped


def _claim_citations(
    claim: str,
    groups: list[CitationGroup],
    sentence_is_compound: bool,
    subclaims: tuple[str, ...],
    *,
    original_claim_text: str,
) -> ClaimCitations:
    mentions: list[CitationMention] = []
    seen: set[int] = set()
    duplicates: list[int] = []
    for group in groups:
        for number in group.numbers:
            if number in seen:
                if number not in duplicates:
                    duplicates.append(number)
                continue
            seen.add(number)
            mentions.append(CitationMention(ref_number=number, marker=group.marker))
    return ClaimCitations(
        claim_text=claim.strip(),
        mentions=tuple(mentions),
        duplicate_numbers=tuple(duplicates),
        sentence_is_compound=sentence_is_compound,
        subclaims=subclaims,
        original_claim_text=original_claim_text,
    )


def split_claim_segments(text: str) -> list[str]:
    """Return sentence-like claim segments for numeric and author-year parsers."""
    return _split_claim_segments(text)


def _split_claim_segments(text: str) -> list[str]:
    """Split into sentence-like segments.

    Single line breaks (from copying a PDF) are joined back into one line;
    only blank lines are paragraph breaks. A period after an abbreviation
    (et al., e.g., Fig.), an initial, or before a lowercase word or a
    citation bracket does not end a sentence.
    """

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _LINE_HYPHEN_RE.sub("-", normalized)
    segments: list[str] = []
    for paragraph in _PARAGRAPH_BREAK_RE.split(normalized):
        paragraph = " ".join(paragraph.split())
        start = 0
        for boundary in _SENTENCE_BOUNDARY_RE.finditer(paragraph):
            before = paragraph[start : boundary.start()]
            after = paragraph[boundary.end() : boundary.end() + 1]
            last_word = before.rsplit(" ", 1)[-1].rstrip(".").lower()
            if (
                last_word in _ABBREVIATIONS
                or (len(last_word) == 1 and last_word.isalpha())
                or after.islower()
                or after == "["
            ):
                continue
            segment = paragraph[start : boundary.start()].strip()
            if segment:
                segments.append(segment)
            start = boundary.end()
        final_segment = paragraph[start:].strip()
        if final_segment:
            segments.append(final_segment)
    return segments


def _parse_bracket_body(body: str) -> tuple[int, ...]:
    numbers: list[int] = []
    for raw_part in re.split(r"\s*[,;]\s*", body):
        part = raw_part.strip()
        match = _PART_RE.fullmatch(part)
        if match is None:
            raise CitationParseError(f"無法解析引用標記內容：{body}")
        start_number = int(match.group("start"))
        end_text = match.group("end")
        if end_text is None:
            numbers.append(start_number)
        else:
            numbers.extend(_expand_range(start_number, int(end_text)))
    return tuple(dict.fromkeys(numbers))


def _expand_range(start_number: int, end_number: int) -> tuple[int, ...]:
    if end_number < start_number:
        raise CitationParseError(
            f"引用範圍起點不得大於終點：{start_number}-{end_number}"
        )
    if end_number - start_number + 1 > _MAX_EXPANDED_RANGE:
        raise CitationParseError(
            f"引用範圍過大：{start_number}-{end_number}"
        )
    return tuple(range(start_number, end_number + 1))


def _overlaps(start: int, end: int, occupied: list[tuple[int, int]]) -> bool:
    return any(start < used_end and end > used_start for used_start, used_end in occupied)

