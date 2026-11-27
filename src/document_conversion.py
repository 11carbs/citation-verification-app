"""File bytes -> Markdown plus source locations; no citation inference or API calls."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
from zipfile import ZipFile

SUPPORTED_EXTENSIONS = ("pdf", "docx")
MAX_BYTES = 30 * 1024 * 1024
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_PDF_LINE_HYPHEN = re.compile(
    r"(?P<left>[A-Za-zÀ-ÖØ-öø-ÿ0-9]{1,30})-[ \t]*\n[ \t]*"
    r"(?P<right>[A-Za-zÀ-ÖØ-öø-ÿ0-9]{1,30})"
)
_PDF_BLOCK_HYPHEN = re.compile(r"(?P<left>[A-Za-zÀ-ÖØ-öø-ÿ0-9]{1,30})-$")
_PDF_BLOCK_CONTINUATION = re.compile(r"^(?P<right>[A-Za-zÀ-ÖØ-öø-ÿ0-9]{1,30})")
# These are lexical compounds, not printer-added word breaks. Unknown pairs
# are joined without the hyphen so rare names such as Ser-hii are recovered.
_PDF_TRUE_COMPOUNDS = {
    "four-label", "sub-category", "sub-categories", "state-of",
    "few-shot", "real-world", "highly-cited", "mistral-small",
    "cross-domain", "open-source", "large-scale", "full-text",
    "long-context", "low-resource", "high-quality", "self-hosted",
    "peer-reviewed", "multi-domain", "zero-shot", "one-shot",
    "end-to-end", "state-of-the-art", "human-annotated",
    "co-cited", "inter-annotator", "cloud-based", "cross-database",
    "multi-source", "venue-specific", "cosine-similarity", "fine-tuning",
    "off-topic", "high-throughput", "label-prediction", "sci-fact",
    "zero-assumption",
}
_PDF_LEFT_ACRONYMS = {
    "AI", "API", "BERT", "BM25", "DOI", "FAISS", "GPT", "IEEE", "LLM",
    "NLI", "PDF", "RAG", "TF", "TPR", "W3C", "XML",
}
_PDF_RIGHT_ACRONYMS = {"AI", "API", "FPR", "IDF", "LLM", "NLI", "PDF", "RAG", "TPR"}


def _pdf_keep_hyphen(left: str, right: str, known: set[str]) -> bool:
    pair = f"{left}-{right}".casefold()
    return (pair in _PDF_TRUE_COMPOUNDS or pair in known
            or left in _PDF_LEFT_ACRONYMS or right in _PDF_RIGHT_ACRONYMS
            or any(character.isdigit() for character in left + right))


def _pdf_join_line_hyphens(text: str, known: set[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        left, right = match["left"], match["right"]
        line_start = text.rfind("\n", 0, match.start()) + 1
        if re.search(r"https?://\S*$", text[line_start:match.start()], re.I):
            return f"{left}-{right}"
        return left + ("-" if _pdf_keep_hyphen(left, right, known) else "") + right

    return _PDF_LINE_HYPHEN.sub(replace, text)


def _pdf_normalize_hyphens(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    raw = "\n".join(block.markdown for block in blocks)
    known = {f"{left}-{right}".casefold() for left, right in re.findall(
        r"\b([A-Za-zÀ-ÖØ-öø-ÿ]{2,})-([A-Za-zÀ-ÖØ-öø-ÿ]{2,})\b(?![ \t]*\n)", raw
    )}
    normalized = [DocumentBlock(block.block_id,
                                _pdf_join_line_hyphens(block.markdown, known),
                                block.locator, block.page, block.bbox)
                  for block in blocks]
    merged: list[DocumentBlock] = []
    for block in normalized:
        previous = merged[-1] if merged else None
        ending = _PDF_BLOCK_HYPHEN.search(previous.markdown) if previous else None
        beginning = _PDF_BLOCK_CONTINUATION.match(block.markdown)
        if (previous and ending and beginning and previous.page == block.page
                and previous.bbox and block.bbox
                and -2 <= block.bbox[1] - previous.bbox[3] <= 8
                and previous.bbox[0] + 5 <= block.bbox[0] <= previous.bbox[0] + 25):
            left, right = ending["left"], beginning["right"]
            url = re.search(r"https?://\S*$", previous.markdown[:ending.start()], re.I)
            separator = "-" if url or _pdf_keep_hyphen(left, right, known) else ""
            text = previous.markdown[:-1] + separator + block.markdown
            box = (min(previous.bbox[0], block.bbox[0]),
                   min(previous.bbox[1], block.bbox[1]),
                   max(previous.bbox[2], block.bbox[2]),
                   max(previous.bbox[3], block.bbox[3]))
            merged[-1] = DocumentBlock(previous.block_id, text, previous.locator,
                                       previous.page, box)
        else:
            merged.append(block)
    return merged


def _word_tag(name: str) -> str:
    return f"{{{_WORD_NS}}}{name}"


def _pdf_reference_label_first(text: str, detail: dict) -> str:
    """Fix PDF text blocks whose left-hand reference label is emitted last."""
    lines = text.splitlines()
    if len(lines) < 2 or not re.fullmatch(r"\[\d{1,3}\]", lines[-1].strip()):
        return text
    detailed_lines = detail.get("lines", [])
    if len(detailed_lines) != len(lines):
        return text
    label = detailed_lines[-1]
    first = detailed_lines[0]
    if (label["bbox"][0] < first["bbox"][0]
            and abs(label["bbox"][1] - first["bbox"][1]) < 3):
        return lines[-1].strip() + "\n" + "\n".join(lines[:-1])
    return text


def _docx_paragraph_text(paragraph) -> str:
    """Read Word runs in order, retaining superscripts and note references."""
    parts: list[str] = []
    for run in paragraph._p.iter(_word_tag("r")):
        fragments: list[str] = []
        for child in run:
            if child.tag == _word_tag("t"):
                fragments.append(child.text or "")
            elif child.tag == _word_tag("tab"):
                fragments.append("\t")
            elif child.tag == _word_tag("br"):
                fragments.append("\n")
            elif child.tag in {_word_tag("footnoteReference"), _word_tag("endnoteReference")}:
                kind = "fn" if child.tag == _word_tag("footnoteReference") else "en"
                note_id = child.get(_word_tag("id"))
                if note_id and note_id.isdigit():
                    fragments.append(f"[^{kind}{note_id}]")
        text = "".join(fragments)
        align = run.find(f"{_word_tag('rPr')}/{_word_tag('vertAlign')}")
        if (align is not None and align.get(_word_tag("val")) == "superscript"
                and re.fullmatch(r"\d+(?:\s*[,;–-]\s*\d+)*", text)):
            text = "^{" + text + "}"
        parts.append(text)
    return "".join(parts)


def _docx_notes(data: bytes, kind: str) -> list[tuple[str, str]]:
    from defusedxml import ElementTree

    part = f"word/{'footnotes' if kind == 'fn' else 'endnotes'}.xml"
    with ZipFile(BytesIO(data)) as archive:
        if part not in archive.namelist():
            return []
        root = ElementTree.fromstring(archive.read(part))
    notes: list[tuple[str, str]] = []
    for note in root:
        note_id = note.get(_word_tag("id"), "")
        if not note_id.isdigit() or note.get(_word_tag("type")):
            continue
        paragraphs = ["".join(node.text or "" for node in paragraph.iter(_word_tag("t"))).strip()
                      for paragraph in note.iter(_word_tag("p"))]
        text = " ".join(part for part in paragraphs if part)
        if text:
            notes.append((note_id, text))
    return notes


class ConversionError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class DocumentBlock:
    block_id: str
    markdown: str
    locator: str
    page: int | None = None
    bbox: tuple[float, ...] | None = None


@dataclass(frozen=True)
class ConvertedDocument:
    document_id: str
    filename: str
    source_format: str
    markdown: str
    blocks: tuple[DocumentBlock, ...]
    warnings: tuple[str, ...]
    converter: str
    schema_version: str = "document.v1"

    def to_dict(self) -> dict:
        return asdict(self)


def conversion_capabilities() -> dict:
    return {"extensions": list(SUPPORTED_EXTENSIONS), "max_bytes": MAX_BYTES,
            "ocr_available": False}


def convert_pasted_paper(text: str) -> ConvertedDocument:
    """Create an internal text fixture for parser tests, not a user upload path."""
    if not text.strip():
        raise ConversionError("EMPTY_FILE", "內容是空的。")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    data = normalized.encode("utf-8")
    block = DocumentBlock("block_0001", normalized.strip(), "text:document")
    markdown = f"<!-- {block.block_id} -->\n{block.markdown}"
    return ConvertedDocument("doc_" + sha256(data).hexdigest()[:16],
                             "internal-text-fixture", "internal_text", markdown,
                             (block,), (), "internal-test-text-v1")


def _order_pdf_page(blocks: list[DocumentBlock], page_width: float) -> list[DocumentBlock]:
    """Read two substantial text columns vertically before crossing the gutter."""
    def by_reading_line(items: list[DocumentBlock]) -> list[DocumentBlock]:
        # PDF baselines often differ by a fraction of a point between a label
        # in the margin and the reference text on the same visual line.
        positioned = sorted(items, key=lambda item: (item.bbox[1], item.bbox[0]))
        ordered: list[DocumentBlock] = []
        group: list[DocumentBlock] = []
        for item in positioned:
            if group and item.bbox[1] - group[0].bbox[1] > 2:
                ordered.extend(sorted(group, key=lambda member: member.bbox[0]))
                group = []
            group.append(item)
        ordered.extend(sorted(group, key=lambda member: member.bbox[0]))
        return ordered

    middle = page_width / 2
    left = [block for block in blocks if block.bbox and block.bbox[2] < middle + 15]
    right = [block for block in blocks if block not in left and block.bbox
             and block.bbox[0] > middle - 15]
    if len(left) < 3 or len(right) < 3:
        return by_reading_line(blocks)
    first_y = min(block.bbox[1] for block in left + right)
    leading = [block for block in blocks if block not in left + right and block.bbox
               and block.bbox[3] <= first_y + 10]
    trailing = [block for block in blocks if block not in left + right and block not in leading]
    return by_reading_line(leading) + by_reading_line(left) + \
        by_reading_line(right) + by_reading_line(trailing)


def convert_document(data: bytes, filename: str) -> ConvertedDocument:
    """Common entry point for both the input paper and cited source documents.

    This baseline preserves available textual structure, not PDF visual fidelity.
    Unsupported or image-only inputs raise explicit errors rather than empty success.
    """
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in SUPPORTED_EXTENSIONS:
        raise ConversionError("UNSUPPORTED_FORMAT", "僅支援 PDF、DOCX。")
    if not data:
        raise ConversionError("EMPTY_FILE", "檔案是空的。")
    if len(data) > MAX_BYTES:
        raise ConversionError("FILE_TOO_LARGE", "檔案超過 30 MiB。")
    blocks: list[DocumentBlock] = []
    warnings: list[str] = []

    def add(text: str, locator: str, page: int | None = None, bbox=None):
        if text.strip():
            blocks.append(DocumentBlock(f"block_{len(blocks)+1:04d}", text.strip(), locator, page, bbox))

    try:
        if extension == "pdf":
            import pymupdf
            converter = "pymupdf-text-blocks-v2"
            with pymupdf.open(stream=data, filetype="pdf") as document:
                if document.needs_pass:
                    raise ConversionError("ENCRYPTED_PDF", "PDF 需要密碼，請上傳可讀取版本。")
                for page_number, page in enumerate(document, 1):
                    text_blocks = page.get_text("blocks", sort=True)
                    detailed = {block["number"]: block for block in page.get_text("dict")["blocks"]}
                    before = len(blocks)
                    superscripts: dict[int, list[tuple[str, tuple[float, ...]]]] = {}
                    skip_numbers: set[int] = set()
                    for index, raised in enumerate(text_blocks):
                        if raised[6] != 0 or not re.fullmatch(
                            r"\d+(?:\s*[,;–-]\s*\d+)*", raised[4].strip()
                        ):
                            continue
                        raised_spans = [span for line in detailed.get(raised[5], {}).get("lines", [])
                                        for span in line.get("spans", [])]
                        if not raised_spans:
                            continue
                        for neighbor in (index - 1, index + 1):
                            if not 0 <= neighbor < len(text_blocks):
                                continue
                            parent = text_blocks[neighbor]
                            parent_spans = [span for line in detailed.get(parent[5], {}).get("lines", [])
                                            for span in line.get("spans", [])]
                            if (parent[6] == 0 and parent_spans
                                    and raised_spans[0]["size"] <= parent_spans[-1]["size"] * 0.75
                                    and 0 <= raised[0] - parent[2] <= max(8, parent_spans[-1]["size"])
                                    and 0.15 * parent_spans[-1]["size"] <= (
                                        parent_spans[-1]["origin"][1] - raised_spans[0]["origin"][1]
                                    ) <= 0.85 * parent_spans[-1]["size"]):
                                superscripts.setdefault(parent[5], []).append((raised[4].strip(), tuple(raised[:4])))
                                skip_numbers.add(raised[5])
                                break
                    for block in text_blocks:
                        if block[6] == 0 and block[5] not in skip_numbers:
                            text = _pdf_reference_label_first(
                                block[4].strip(), detailed.get(block[5], {}),
                            )
                            box = tuple(block[:4])
                            for raised_text, raised_box in superscripts.get(block[5], []):
                                text += "^{" + raised_text + "}"
                                box = (min(box[0], raised_box[0]), min(box[1], raised_box[1]),
                                       max(box[2], raised_box[2]), max(box[3], raised_box[3]))
                            add(text, f"page:{page_number}/block:{block[5]}", page_number, box)
                    blocks[before:] = _order_pdf_page(blocks[before:], page.rect.width)
                    if len(blocks) == before:
                        warnings.append(f"第 {page_number} 頁無可擷取文字，可能需要 OCR。")
            # Repeated top-margin running heads are not author-year citations.
            def header_key(text: str) -> str:
                return " ".join(re.sub(r"\bpage\s+\d+\s+of\s+\d+\b", "", text,
                                       flags=re.I).split()).casefold()

            headers: dict[str, set[int]] = {}
            for block in blocks:
                if block.page and block.bbox and block.bbox[1] < 60:
                    key = header_key(block.markdown)
                    headers.setdefault(key, set()).add(block.page)
            repeated = {key for key, pages in headers.items() if key and len(pages) >= 2}
            blocks = [block for block in blocks if not (block.page and block.bbox
                      and block.bbox[1] < 60 and any(
                          header_key(block.markdown).startswith(key) for key in repeated))]
            blocks = _pdf_normalize_hyphens(blocks)
            warnings.append("PDF 已擷取可選取的文字，並嘗試依雙欄閱讀順序排列及修復跨行斷字；少數真正連字號仍可能誤判。註腳、表格與公式可能漏掉或排錯，請人工核對；圖片掃描頁尚不能辨識文字。")
        elif extension == "docx":
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph
            converter = "python-docx-v1"
            document = Document(BytesIO(data))
            for number, item in enumerate(document.iter_inner_content(), 1):
                if isinstance(item, Paragraph):
                    text = _docx_paragraph_text(item)
                    style = item.style.name if item.style else ""
                    if match := re.fullmatch(r"Heading ([1-6])", style):
                        text = "#" * int(match[1]) + " " + text
                    add(text, f"body:{number}")
                elif isinstance(item, Table):
                    rows = [["<br>".join(_docx_paragraph_text(paragraph) for paragraph in cell.paragraphs)
                             .replace("|", "\\|") for cell in row.cells] for row in item.rows]
                    if rows:
                        lines = ["| " + " | ".join(row) + " |" for row in rows]
                        lines.insert(1, "| " + " | ".join(["---"] * len(rows[0])) + " |")
                        add("\n".join(lines), f"body:{number}/table")
            for kind, label in (("fn", "footnote"), ("en", "endnote")):
                for note_id, note_text in _docx_notes(data, kind):
                    add(f"[^{kind}{note_id}]: {note_text}", f"word:{label}:{note_id}")
            warnings.append("DOCX 已讀取本文、表格及可用的註腳／尾註文字；頁首頁尾、文字方塊及複雜欄位需人工核對。")
    except ConversionError:
        raise
    except Exception as error:
        raise ConversionError("CONVERSION_FAILED", "無法解析檔案，請確認內容、格式與檔案是否完整。") from error
    if not blocks:
        raise ConversionError("NO_EXTRACTABLE_TEXT", "未取得文字；掃描文件可能需要 OCR，不能視為沒有引用。")
    markdown = "\n\n".join(f"<!-- {block.block_id} -->\n{block.markdown}" for block in blocks)
    return ConvertedDocument("doc_" + sha256(data).hexdigest()[:16], Path(filename).name,
                             extension, markdown, tuple(blocks), tuple(warnings), converter)
