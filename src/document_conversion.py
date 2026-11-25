"""File bytes -> Markdown plus source locations; no citation inference or API calls."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re

SUPPORTED_EXTENSIONS = ("pdf", "docx", "html", "xml", "md", "txt")
MAX_BYTES = 30 * 1024 * 1024


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
                    before = len(blocks)
                    for block in text_blocks:
                        if block[6] == 0:
                            add(block[4], f"page:{page_number}/block:{block[5]}", page_number, tuple(block[:4]))
                    if len(blocks) == before:
                        warnings.append(f"第 {page_number} 頁無可擷取文字，可能需要 OCR。")
            warnings.append("PDF 採文字區塊抽取；多欄順序、表格與公式需人工核對，未執行 OCR。")
        elif extension == "docx":
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph
            converter = "python-docx-v1"
            document = Document(BytesIO(data))
            for number, item in enumerate(document.iter_inner_content(), 1):
                if isinstance(item, Paragraph):
                    text = item.text
                    style = item.style.name if item.style else ""
                    if match := re.fullmatch(r"Heading ([1-6])", style):
                        text = "#" * int(match[1]) + " " + text
                    add(text, f"body:{number}")
                elif isinstance(item, Table):
                    rows = [[cell.text.replace("|", "\\|").replace("\n", "<br>") for cell in row.cells] for row in item.rows]
                    if rows:
                        lines = ["| " + " | ".join(row) + " |" for row in rows]
                        lines.insert(1, "| " + " | ".join(["---"] * len(rows[0])) + " |")
                        add("\n".join(lines), f"body:{number}/table")
            warnings.append("DOCX 本版讀取本文段落與表格；腳註、尾註及文字方塊尚未完整處理。")
        elif extension == "html":
            from bs4 import BeautifulSoup
            from markdownify import markdownify
            converter = "html-markdownify-v1"
            soup = BeautifulSoup(_decode(data), "html.parser")
            for node in soup(["script", "style", "iframe", "object"]):
                node.decompose()
            # Use a document-local Markdown block; do not invent PDF page numbers.
            add(markdownify(str(soup), heading_style="ATX"), "html:document")
        elif extension == "xml":
            from defusedxml import ElementTree
            converter = "xml-structured-text-v1"
            root = ElementTree.fromstring(data)
            def walk(element, locator):
                tag = element.tag.rsplit("}", 1)[-1]
                children = list(element)
                if tag in {"p", "title", "article-title", "ref", "mixed-citation", "element-citation"} or not children:
                    text = "".join(element.itertext()).strip()
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
            warnings.append("XML 本版保留文字與元素路徑；引用目標屬性尚未正規化。")
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
