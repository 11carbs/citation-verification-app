"""Parse a pasted IEEE reference list into numbered raw entries."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class ReferenceEntry:
    ref_number: int
    raw_text: str


_LINE_START_MARKER_RE = re.compile(r"(?m)^[ \t]*\[(?P<number>\d+)\][ \t]*")
_INLINE_MARKER_RE = re.compile(r"(?<!\S)\[(?P<number>\d+)\](?=\s)")


def parse_reference_list(text: str) -> dict[int, ReferenceEntry]:
    """Return numbered entries; continuation lines stay attached to the entry.

    The first occurrence is kept when a duplicate reference number is pasted.
    Numeric entries that are never cited are parsed but later omitted naturally
    by the pairing stage.
    """

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return {}

    matches = list(_LINE_START_MARKER_RE.finditer(normalized))
    if not matches:
        matches = list(_INLINE_MARKER_RE.finditer(normalized))

    entries: dict[int, ReferenceEntry] = {}
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        raw_text = normalized[start:end].strip()
        ref_number = int(match.group("number"))
        entries.setdefault(
            ref_number,
            ReferenceEntry(ref_number=ref_number, raw_text=raw_text),
        )
    return entries

