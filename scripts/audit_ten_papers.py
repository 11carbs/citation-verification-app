"""Reproducible local-only audit of ten distinct research papers and drafts."""

from pathlib import Path
import json
import sys

from audit_real_papers import audit


def one(folder: Path, pattern: str) -> Path:
    found = sorted(folder.glob(pattern))
    if len(found) != 1:
        raise SystemExit(f"Expected one file for {pattern!r} in {folder}; found {len(found)}")
    return found[0]


def corpus() -> list[tuple[str, Path]]:
    workspace = Path(__file__).resolve().parents[2]
    downloads = Path.home() / "Downloads"
    research = sorted((workspace / "5篇").glob("*.pdf"))
    if len(research) != 5:
        raise SystemExit("Expected five research papers in 5篇")
    acquired = workspace / "tmp" / "corpus10"
    return [*( ("published_or_preprint", path) for path in research),
            ("conference_paper", one(workspace, "L3CV_*.pdf")),
            ("preprint", one(downloads, "2510.03662v1.pdf")),
            ("conference_paper", one(acquired, "SciFact_2020.pdf")),
            ("conference_paper", one(downloads, "OpenClaw_in_the_Wild_Security_Analysis_of_Autonomous_Agents.pdf")),
            ("preprint", one(downloads, "ScaleCall_Agentic_Tool_Calling_at_Scale_for_Fintech_Challenges_Methods_and_Deployment_Insights.pdf"))]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for kind, path in corpus():
        result = audit(path)
        if "--summary" in sys.argv:
            print("\t".join(str(value) for value in (
                kind, result["file"], result.get("reference_section", "error"),
                result.get("references", 0), result.get("pairs", 0),
                result.get("ready", 0), result.get("review", 0),
                result.get("statuses", {}), result.get("error", ""))))
        else:
            print(json.dumps({"kind": kind, **result}, ensure_ascii=False))
