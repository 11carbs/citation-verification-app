"""File bytes -> Markdown plus source locations; no citation inference or API calls."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
from zipfile import ZipFile

SUPPORTED_EXTENSIONS = ("pdf", "docx", "html", "xml", "md", "txt")
MAX_BYTES = 30 * 1024 * 1024
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _word_tag(name: str) -> str:
    return f"{{{_WORD_NS}}}{name}"


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


def _decode(data: bytes) -> str:
    # BOM first; do not silently replace unreadable citation characters.
    encodings = ("utf-16",) if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "cp950")
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeError:
            pass
    raise ConversionError("TEXT_ENCODING", "文字編碼無法辨識，請另存 UTF-8。")


def convert_pasted_paper(text: str) -> ConvertedDocument:
    """Treat a pasted full paper as Markdown text through the common converter."""
    data = text.encode("utf-8") if text.strip() else b""
    return convert_document(data, "pasted-paper.md")


def convert_document(data: bytes, filename: str) -> ConvertedDocument:
    """Common entry point for both the input paper and cited source documents.

    This baseline preserves available textual structure, not PDF visual fidelity.
    Unsupported or image-only inputs raise explicit errors rather than empty success.
    """
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in SUPPORTED_EXTENSIONS:
        raise ConversionError("UNSUPPORTED_FORMAT", "支援 PDF、DOCX、HTML、XML、MD、TXT。")
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
            converter = "pymupdf-text-blocks-v1"
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
                            text = block[4].strip()
                            box = tuple(block[:4])
                            for raised_text, raised_box in superscripts.get(block[5], []):
                                text += "^{" + raised_text + "}"
                                box = (min(box[0], raised_box[0]), min(box[1], raised_box[1]),
                                       max(box[2], raised_box[2]), max(box[3], raised_box[3]))
                            add(text, f"page:{page_number}/block:{block[5]}", page_number, box)
                    if len(blocks) == before:
                        warnings.append(f"第 {page_number} 頁無可擷取文字，可能需要 OCR。")
            warnings.append("PDF 採文字區塊抽取，僅合併明顯的數字上標；多欄順序、註腳、表格與公式需人工核對，未執行 OCR。")
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
        elif extension == "html":
            from bs4 import BeautifulSoup
            from markdownify import markdownify
            converter = "html-markdownify-v1"
            soup = BeautifulSoup(_decode(data), "html.parser")
            for node in soup(["script", "style", "iframe", "object"]):
                node.decompose()
            html_notes: dict[str, str] = {}
            for sup in list(soup.find_all("sup")):
                label = sup.get_text("", strip=True)
                if not re.fullmatch(r"\d+(?:\s*[,;–-]\s*\d+)*", label):
                    continue
                link = sup.find("a", href=True)
                if link is None and sup.parent.name == "a" and sup.parent.has_attr("href"):
                    link = sup.parent
                target_id = link["href"][1:] if link and link["href"].startswith("#") else ""
                target = soup.find(id=target_id) if target_id else None
                note_key = re.sub(r"[^A-Za-z0-9_-]", "", target_id)
                is_note = bool(target and (
                    target_id.lower().startswith(("fn", "en", "note"))
                    or target.find_parent(class_=re.compile(r"footnotes|endnotes", re.I))
                ))
                if is_note and note_key:
                    note_text = target.get_text(" ", strip=True)
                    note_text = re.sub(r"^\s*\d+[.)]?\s*", "", note_text).strip()
                    if note_text:
                        html_notes[note_key] = note_text
                        sup.replace_with(f"[^{note_key}]")
                        target.decompose()
                        continue
                sup.replace_with("^{" + label + "}")
            # Use a document-local Markdown block; do not invent PDF page numbers.
            add(markdownify(str(soup), heading_style="ATX"), "html:document")
            for note_key, note_text in html_notes.items():
                add(f"[^{note_key}]: {note_text}", f"html:note:{note_key}")
        elif extension == "xml":
            from defusedxml import ElementTree
            converter = "xml-structured-text-v1"
            root = ElementTree.fromstring(data)
            xml_notes: list[tuple[str, str, str]] = []

            def inline_text(element):
                if element.tag.rsplit("}", 1)[-1] == "xref":
                    label = "".join(element.itertext()).strip()
                    kind = element.attrib.get("ref-type", "").lower()
                    target = element.attrib.get("rid", "")
                    if kind in {"fn", "footnote", "endnote"} and target:
                        return f"[^{target}]"
                    if kind == "bibr":
                        numeric_label = label.strip("[] .")
                        if numeric_label.isdigit():
                            return f"[{numeric_label}]"
                        if target:
                            return f"[@{target}]"
                pieces = [element.text or ""]
                for child in element:
                    pieces.extend((inline_text(child), child.tail or ""))
                return "".join(pieces)

            def walk(element, locator):
                tag = element.tag.rsplit("}", 1)[-1]
                children = list(element)
                if tag in {"fn", "endnote"}:
                    note_id = element.attrib.get("id", "")
                    if note_id:
                        xml_notes.append((note_id, inline_text(element).strip(), locator))
                    return
                if tag in {"p", "title", "article-title", "ref", "mixed-citation", "element-citation"} or not children:
                    text = inline_text(element).strip()
                    if tag == "ref":
                        label = next(("".join(child.itertext()).strip(" .[]") for child in children
                                      if child.tag.rsplit("}", 1)[-1] == "label"), "")
                        if label.isdigit():
                            text = "[" + label + "] " + re.sub(r"^\s*" + re.escape(label) + r"[.)]?\s*", "", text)
                        elif element.attrib.get("id") and not re.match(r"^\s*\[\d+\]", text):
                            text = "[@" + element.attrib["id"] + "] " + text
                    add(("## " if tag in {"title", "article-title"} else "") + text, locator)
                else:
                    if element.text and element.text.strip():
                        add(element.text, locator + "/text")
                    for index, child in enumerate(children, 1):
                        child_tag = child.tag.rsplit("}", 1)[-1]
                        walk(child, f"{locator}/{child_tag}[{index}]")
                        if child.tail and child.tail.strip():
                            add(child.tail, f"{locator}/tail[{index}]")
            walk(root, "/" + root.tag.rsplit("}", 1)[-1])
            for note_id, note_text, locator in xml_notes:
                if note_text:
                    add(f"[^{note_id}]: {note_text}", locator)
            warnings.append("XML 本版保留文字與元素路徑；僅處理常見 JATS xref/ref/註腳結構，其他 schema 需核對。")
        else:
            converter = "markdown-pass-through-v1" if extension == "md" else "plain-text-v1"
            text = _decode(data).replace("\r\n", "\n").replace("\r", "\n")
            add(text, "text:document")
    except ConversionError:
        raise
    except Exception as error:
        raise ConversionError("CONVERSION_FAILED", "無法解析檔案，請確認內容、格式與檔案是否完整。") from error
    if not blocks:
        raise ConversionError("NO_EXTRACTABLE_TEXT", "未取得文字；掃描文件可能需要 OCR，不能視為沒有引用。")
    markdown = "\n\n".join(f"<!-- {block.block_id} -->\n{block.markdown}" for block in blocks)
    return ConvertedDocument("doc_" + sha256(data).hexdigest()[:16], Path(filename).name,
                             extension, markdown, tuple(blocks), tuple(warnings), converter)
