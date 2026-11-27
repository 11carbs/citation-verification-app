"""Read-only citation extraction audit for locally held research PDFs."""

from collections import Counter
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.document_conversion import ConversionError, convert_document  # noqa: E402
from src.handoff_bundle import HandoffValidationError, build_handoff_bundle  # noqa: E402
from src.whole_paper import extract_whole_paper  # noqa: E402


def audit(path: Path) -> dict:
    result: dict = {"file": path.name}
    try:
        document = convert_document(path.read_bytes(), path.name)
        extracted = extract_whole_paper(document)
        bundle = build_handoff_bundle(document, extracted)
    except (ConversionError, HandoffValidationError) as error:
        return {**result, "error": str(error)}
    result.update({
        "blocks": len(document.blocks),
        "reference_section": extracted.section_found,
        "references": len(extracted.references),
        "pairs": len(extracted.pairs),
        "ready": len(bundle.ready_pairs),
        "review": len(bundle.review_pairs),
        "statuses": dict(Counter(pair.match_status for pair in extracted.pairs)),
        "issues": [issue.code for issue in extracted.issues[:12]],
        "reference_head": [(ref.ref_id, ref.raw_text[:65]) for ref in extracted.references[:8]],
        "reference_tail": [(ref.ref_id, ref.raw_text[:65]) for ref in extracted.references[-8:]],
        "missing_markers": Counter(pair.citation_marker for pair in extracted.pairs
                                   if pair.match_status == "missing_reference").most_common(8),
        "heading_context": [
            {"page": block.page, "locator": block.locator,
             "next": [next_block.markdown[:90] for next_block in document.blocks[index + 1:index + 4]]}
            for index, block in enumerate(document.blocks)
            if any(re.fullmatch(r"(?:#+\s*)?(?:references|bibliography|works cited|參考文獻)",
                                line.strip(), re.I) for line in block.markdown.splitlines())
        ][:12],
        "samples": [
            {"marker": pair.citation_marker, "status": pair.match_status,
             "ref_id": pair.reference.ref_id if pair.reference else "",
             "claim": pair.claim_text[:90]}
            for pair in extracted.pairs[:8]
        ],
    })
    return result


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for name in sys.argv[1:]:
        path = Path(name)
        files = sorted(path.glob("*.pdf")) if path.is_dir() else [path]
        for file in files:
            print(json.dumps(audit(file), ensure_ascii=False))
