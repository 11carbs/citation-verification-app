"""Exercise DOCX input with complete local paper content."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.document_conversion import convert_document  # noqa: E402
from src.handoff_bundle import build_handoff_bundle  # noqa: E402
from src.whole_paper import extract_whole_paper  # noqa: E402


def report(label: str, data: bytes, filename: str) -> None:
    document = convert_document(data, filename)
    outcome = extract_whole_paper(document)
    bundle = build_handoff_bundle(document, outcome)
    print("\t".join(str(value) for value in (
        label, document.source_format, len(document.blocks), len(outcome.references),
        len(outcome.pairs), len(bundle.ready_pairs), len(bundle.review_pairs))))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    workspace = Path(__file__).resolve().parents[2]
    docx = workspace / "文獻探討.docx"
    report("research_draft", docx.read_bytes(), docx.name)
