"""Refresh the local Phantom PDF handoff using the current PDF/DOCX contract."""

from pathlib import Path
import sys
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.document_conversion import convert_document  # noqa: E402
from src.handoff_bundle import build_handoff_bundle, pairing_preview_csv_bytes  # noqa: E402
from src.whole_paper import extract_whole_paper  # noqa: E402


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    workspace = repo.parent
    source = next((workspace / "5篇").glob("Phantom citations*.pdf"))
    converted = convert_document(source.read_bytes(), source.name)
    bundle = build_handoff_bundle(converted, extract_whole_paper(converted))
    output = workspace / "output" / "handoff"
    output.mkdir(parents=True, exist_ok=True)
    csv_data = pairing_preview_csv_bytes(bundle)
    (output / "phantom-pairing-review.csv").write_bytes(csv_data)
    with ZipFile(output / "phantom-team-handoff.zip", "w") as archive:
        archive.writestr("phantom-citation-batch.v1.json", bundle.to_json_bytes())
        archive.writestr("phantom-pairing-review.csv", csv_data)
        archive.write(repo / "docs" / "document-handoff-v1.md", "document-handoff-v1.md")
    print(f"references={len(bundle.references)} ready={len(bundle.ready_pairs)} review={len(bundle.review_pairs)}")


if __name__ == "__main__":
    main()
