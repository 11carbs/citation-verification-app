"""Exercise the production fallback with simulated Scholar 429 and real official records."""

import argparse
from collections import Counter
import csv
from difflib import SequenceMatcher
from hashlib import sha256
import json
from pathlib import Path
import re
import statistics
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import requests

from src.abstract_sources import AbstractMarkup, doi_value
from src.reference_lookup import ReferenceLookupAdapter, RequestsClient, normalize_title, title_similarity
from src.schemas import CitationPair


ACL_IDS = (
    "2020.emnlp-main.609", "2020.emnlp-main.295", "2020.emnlp-main.600", "2020.emnlp-main.601",
    "2020.acl-main.746", "2020.acl-main.740", "2020.acl-main.703", "2020.acl-main.1",
    "2022.findings-emnlp.347", "2024.acl-long.1",
)
ARXIV_CASES = (
    ("2005.11401", "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"),
    ("1706.03762", "Attention Is All You Need"),
    ("1810.04805", "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"),
    ("2203.02155", "Training language models to follow instructions with human feedback"),
    ("2010.11929", "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale"),
    ("1512.03385", "Deep Residual Learning for Image Recognition"),
    ("2006.11239", "Denoising Diffusion Probabilistic Models"),
    ("2103.00020", "Learning Transferable Visual Models From Natural Language Supervision"),
    ("2106.09685", "LoRA: Low-Rank Adaptation of Large Language Models"),
    ("2305.10601", "Tree of Thoughts: Deliberate Problem Solving with Large Language Models"),
)
PUBMED_CASES = (("29466592", "10.1001/jama.2018.0245"), ("18303940", "10.1371/journal.pmed.0050045"))
FIELDS = (
    "id", "title", "doi", "official_record", "status", "source", "source_url", "comparison",
    "official_words", "retrieved_words", "official_sha256", "retrieved_sha256", "elapsed_ms",
    "http_requests", "failure_reason", "lookup_note",
)


class ControlledScholar429(RequestsClient):
    """Inject only the upstream failure. All fallback HTTP calls reach their real hosts."""

    def __init__(self) -> None:
        super().__init__(retries=1, timeout=10, request_source="live-fallback-acceptance")
        self.arxiv_document = ""

    def get(self, url: str, params: dict | None = None, headers: dict | None = None):
        host = urlsplit(url).hostname
        if host == "api.semanticscholar.org":
            self._trace(url, headers, params, sent=False, status=429, reason="injected_429_no_network")
            return 429, None, "HTTP 429 (controlled acceptance condition)"
        code, data, error = super().get(url, params, headers)
        if host == "arxiv.org" and data:
            self.arxiv_document = data.get("html", "")
        return code, data, error


def xml_element_text(element: ET.Element | None) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def acl_reference(paper_id: str, session: requests.Session, collections: dict) -> tuple[str, str, str, str]:
    volume_id, number = paper_id.rsplit(".", 1)
    collection, volume = volume_id.rsplit("-", 1)
    url = f"https://raw.githubusercontent.com/acl-org/acl-anthology/master/data/xml/{collection}.xml"
    if collection not in collections:
        response = session.get(url, timeout=18)
        response.raise_for_status()
        collections[collection] = ET.fromstring(response.content)
    paper = collections[collection].find(f"./volume[@id='{volume}']/paper[@id='{number}']")
    if paper is None:
        raise ValueError("ACL reference missing in official source XML")
    title = xml_element_text(paper.find("title"))
    doi = doi_value(paper.findtext("doi"))
    abstract = xml_element_text(paper.find("abstract"))
    return title, doi, abstract, url


def pubmed_reference(pmid: str, session: requests.Session) -> tuple[str, str, str, str]:
    endpoint = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {"db": "pubmed", "id": pmid, "retmode": "xml"}
    response = session.get(endpoint, params=params, timeout=15)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    article = root.find(".//MedlineCitation/Article")
    if article is None or root.findtext(".//MedlineCitation/PMID") != pmid:
        raise ValueError("PubMed returned a different accession")
    title = xml_element_text(article.find("ArticleTitle"))
    doi = doi_value(root.findtext(".//ArticleId[@IdType='doi']"))
    sections = [
        " ".join(filter(None, (element.get("Label"), xml_element_text(element))))
        for element in article.findall("Abstract/AbstractText")
    ]
    return title, doi, " ".join(sections), f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"


def arxiv_visible_abstract(html: str) -> str:
    block = re.search(r'<blockquote\b[^>]*class="[^"]*\babstract\b[^"]*"[^>]*>(.*?)</blockquote>', html, re.S)
    if not block:
        return ""
    # arXiv displays some literal links as "this URL" in the visible block.
    # Compare the linked target to the URL written out in citation_abstract.
    markup = re.sub(r'<a\b[^>]*href="(https?://[^\"]+)"[^>]*>.*?</a>', r' \1', block[1], flags=re.S)
    return re.sub(r"^Abstract\s*:\s*", "", AbstractMarkup(markup).text)


def compare_abstract(expected: str, actual: str) -> dict:
    # ACL XML represents the multiplication sign as LaTeX, while its page renders ×.
    canonical = normalize_title(expected.replace(r"\times", " times ").replace("×", " times "))
    captured = normalize_title(actual.replace(r"\times", " times ").replace("×", " times "))
    expected_words, captured_words = canonical.split(), captured.split()
    ratio = SequenceMatcher(None, expected_words, captured_words, autojunk=False).ratio()
    exact = bool(canonical) and canonical == captured
    same_boundaries = (expected_words[:8] == captured_words[:8] and expected_words[-8:] == captured_words[-8:])
    comparable = bool(expected_words and captured_words)
    match = exact or (comparable and same_boundaries and ratio >= 0.98)
    return {
        "comparison": "exact" if exact else "near-exact" if match else "mismatch" if comparable else "unavailable",
        "official_words": len(expected_words),
        "retrieved_words": len(captured_words),
        "official_sha256": sha256(canonical.encode()).hexdigest() if canonical else "",
        "retrieved_sha256": sha256(captured.encode()).hexdigest() if captured else "",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cases = [("ACL", paper_id, "") for paper_id in ACL_IDS]
    cases += [("arXiv", paper_id, title) for paper_id, title in ARXIV_CASES]
    cases += [("PubMed", pmid, doi) for pmid, doi in PUBMED_CASES]
    client = ControlledScholar429()
    adapter = ReferenceLookupAdapter(http=client)
    collections: dict = {}
    results: list[dict] = []
    wall_start = time.monotonic()
    with requests.Session() as gold_session, client.session, (
        args.output_dir / "acceptance.csv"
    ).open("w", encoding="utf-8-sig", newline="") as csv_output, (
        args.output_dir / "acceptance_trace.jsonl"
    ).open("w", encoding="utf-8") as trace_output:
        writer = csv.DictWriter(csv_output, FIELDS)
        writer.writeheader()
        for number, (kind, paper_id, expected) in enumerate(cases, 1):
            row = {"id": f"{kind}:{paper_id}"}
            try:
                if kind == "ACL":
                    title, doi, gold, url = acl_reference(paper_id, gold_session, collections)
                elif kind == "PubMed":
                    title, doi, gold, url = pubmed_reference(paper_id, gold_session)
                    if doi != expected:
                        raise ValueError("PubMed reference DOI disagrees with the fixed case")
                    time.sleep(0.4)
                else:
                    title = expected
                    doi = f"10.48550/arxiv.{paper_id}"
                    gold = ""
                    url = f"https://arxiv.org/abs/{paper_id}"
                row.update(title=title, doi=doi, official_record=url)
                if not title or not doi or (kind != "arXiv" and not gold):
                    raise ValueError("official reference is missing DOI, title or abstract")
                reference = f'[{number}] "{title}," doi: {doi}.'
                pair = CitationPair(f"sample_{number:02d}", "Evidence retrieval check", f"[{number}]", number, reference)
                client.arxiv_document = ""
                start = time.monotonic()
                result = adapter.lookup(pair)
                row["elapsed_ms"] = round((time.monotonic() - start) * 1000)
                if kind == "arXiv":
                    gold = arxiv_visible_abstract(client.arxiv_document)
                row.update(compare_abstract(gold, result.abstract))
                row.update(
                    status="verified" if row["comparison"] in ("exact", "near-exact") else "failed",
                    source=result.abstract_source, source_url=result.abstract_url,
                    http_requests=sum(bool(event.get("sent")) for event in result.attempts),
                    lookup_note=result.source_note,
                )
                if row["status"] == "failed":
                    row["failure_reason"] = result.error_message or f"official abstract comparison: {row['comparison']}"
                for event in result.attempts:
                    trace_output.write(json.dumps({"id": row["id"], **event}, ensure_ascii=False) + "\n")
            except (requests.RequestException, ET.ParseError, ValueError) as exc:
                row.update(status="failed", failure_reason=f"{type(exc).__name__}: {exc}")
            results.append(row)
            writer.writerow(row)
            csv_output.flush()
            trace_output.flush()
            print(row["id"], row["status"], row.get("source", ""), row.get("elapsed_ms", ""), flush=True)

    verified = [row for row in results if row["status"] == "verified"]
    times = [row["elapsed_ms"] for row in results if "elapsed_ms" in row]
    summary = {
        "semantic_scholar_condition": "injected_429_no_network",
        "papers": len(results),
        "verified": len(verified),
        "verified_rate": len(verified) / len(results),
        "verified_sources": dict(Counter(row["source"] for row in verified)),
        "comparison_counts": dict(Counter(row.get("comparison", "unavailable") for row in results)),
        "lookup_total_ms": sum(times),
        "lookup_median_ms": statistics.median(times) if times else None,
        "wall_seconds_including_reference_checks": round(time.monotonic() - wall_start, 3),
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
