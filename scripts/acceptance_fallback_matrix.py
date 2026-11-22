"""Run four cold-cache abstract lookup scenarios against a bibliography fixture."""

import argparse
import csv
from html import escape
import json
from pathlib import Path
import time
from unittest.mock import patch
from urllib.parse import urlsplit

import requests
import streamlit as st

from src.abstract_sources import acl_anthology_id
from src.reference_lookup import ReferenceLookupAdapter, RequestsClient, S2_BASE, S2_FIELDS
from src.reference_metadata import ReferenceMetadata, extract_reference_metadata
from src.schemas import CitationPair, SourceStatus


S2_HOST = urlsplit(S2_BASE).hostname
SCENARIOS = ("normal", "forced_429", "no_abstract", "backups_missing_or_mismatch")


def scholar_id(meta: ReferenceMetadata) -> str:
    acl = acl_anthology_id(meta.doi)
    if acl:
        return f"ACL:{acl}"
    return f"DOI:{meta.doi}" if meta.doi else f"ARXIV:{meta.arxiv_id}"


def fake_response(status: int, content: dict | str) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.encoding = "utf-8"
    response._content = (content if isinstance(content, str) else json.dumps(content)).encode("utf-8")
    if status == 429:
        response.headers["Retry-After"] = "120"
    return response


def negative_backup(url: str, meta: ReferenceMetadata) -> requests.Response:
    host = urlsplit(url).hostname
    if host in ("aclanthology.org", "arxiv.org"):
        title = escape(meta.title, quote=True)
        return fake_response(200, f'<meta name="citation_title" content="{title}">'
                             '<meta name="citation_doi" content="10.9999/wrong-work">'
                             '<meta name="citation_arxiv_id" content="9999.99999">'
                             '<meta name="citation_abstract" content="Wrong work.">'
                             '<div class="acl-abstract"><p>Wrong work.</p></div>')
    if host == "api.openalex.org":
        work = {"doi": "10.9999/wrong-work", "display_name": meta.title,
                "abstract_inverted_index": {"Wrong": [0], "work.": [1]}}
        return fake_response(200, work if "/doi:" in url else {"results": [work]})
    return fake_response(404, {"error": "controlled negative control"})


def run_case(paper: dict, meta: ReferenceMetadata, scenario: str, primary: dict | None,
             primary_mode: str, s2_key: str, oa_key: str) -> tuple[dict, list[dict], str]:
    client = RequestsClient(retries=1, timeout=15, request_source=f"fallback-matrix:{scenario}")
    real_get = client.session.get
    simulated_endpoints: dict[str, str] = {}

    def get(url, **kwargs):
        if urlsplit(url).hostname == S2_HOST:
            simulated_endpoints[url] = primary_mode if scenario == "normal" else "injected_response"
            if scenario == "normal" and primary:
                return fake_response(200, primary)
            if scenario == "forced_429":
                return fake_response(429, {"message": "Controlled local 429"})
            if scenario == "no_abstract":
                return fake_response(200, {"title": meta.title, "abstract": None, "externalIds": {
                    "DOI": meta.doi, "ACL": acl_anthology_id(meta.doi), "ArXiv": meta.arxiv_id,
                }})
            return fake_response(404, {"message": "Controlled missing Semantic Scholar record"})
        if scenario == "backups_missing_or_mismatch":
            simulated_endpoints[url] = "injected_response"
            return negative_backup(url, meta)
        return real_get(url, **kwargs)

    pair = CitationPair(paper["name"], "Abstract retrieval acceptance [1].", "[1]", 1, paper["ref"])
    start = time.monotonic()
    with client.session, patch.object(client.session, "get", side_effect=get):
        adapter = ReferenceLookupAdapter(s2_key=s2_key, openalex_key=oa_key, http=client, scholar_cache={})
        result = adapter.lookup(pair)
    elapsed = round((time.monotonic() - start) * 1000)
    events = []
    for event in result.attempts:
        replay = simulated_endpoints.get(event["endpoint"])
        if replay:
            event = {**event, "sent": False, "transport": replay}
        events.append({"paper": paper["name"], "scenario": scenario, **event})

    recovered = result.source_status is SourceStatus.GOT_ABSTRACT and bool(result.abstract)
    control = scenario == "backups_missing_or_mismatch"
    passed = not recovered if control else recovered
    if scenario == "normal" and not primary:
        passed = False
    if scenario == "forced_429":
        assert any(event.get("status") == 429 for event in events if S2_HOST in event["endpoint"])
        assert any(event["sent"] and S2_HOST not in event["endpoint"] for event in events)
    if control:
        assert any("api.openalex.org" in event["endpoint"] for event in events)
    first_reason = "Semantic Scholar 批次未回傳這筆正常摘要" if scenario == "normal" and not primary else ""
    return {
        "paper": paper["name"], "doi_or_arxiv": meta.doi or meta.arxiv_id, "scenario": scenario,
        "primary_input": primary_mode if scenario == "normal" else "injected_response",
        "passed": passed, "abstract_obtained": recovered, "status": result.source_status.value,
        "source": result.abstract_source, "source_url": result.abstract_url, "abstract_chars": len(result.abstract),
        "wall_ms": elapsed, "actual_http_requests": sum(event["sent"] for event in events),
        "reason": first_reason or result.error_message or result.source_note,
    }, events, result.abstract


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--papers", type=Path, default=Path("tests/data/abstract_fallback_papers.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    papers = json.loads(args.papers.read_text(encoding="utf-8"))
    s2_key = st.secrets.get("S2_API_KEY", "").strip()
    oa_key = st.secrets.get("OPENALEX_API_KEY", "").strip()
    metadata = [extract_reference_metadata(paper["ref"]) for paper in papers]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    batch = [None] * len(papers)
    try:
        with requests.post(f"{S2_BASE}/paper/batch", headers={"x-api-key": s2_key},
                           params={"fields": S2_FIELDS}, json={"ids": [scholar_id(meta) for meta in metadata]},
                           timeout=20, allow_redirects=False) as response:
            info = {"status": response.status_code, "count": len(papers),
                    "request_id": response.headers.get("x-amzn-RequestId"),
                    "retry_after": response.headers.get("Retry-After")}
            if response.ok:
                batch = response.json()
    except (requests.RequestException, ValueError) as error:
        info = {"status": None, "error_type": type(error).__name__}
    (args.output_dir / "semantic_scholar_batch.json").write_text(json.dumps(info, indent=2), encoding="utf-8")

    results = []
    trace_path = args.output_dir / "fallback_attempts.jsonl"
    with trace_path.open("w", encoding="utf-8") as trace:
        for index, (paper, meta) in enumerate(zip(papers, metadata, strict=True)):
            primary = batch[index] if index < len(batch) and isinstance(batch[index], dict) else None
            if primary and not primary.get("abstract"):
                primary = None
            primary_mode = "live_batch_replay" if primary else "unavailable"
            order = SCENARIOS if primary else (*SCENARIOS[1:], "normal")
            paper_results = {}
            for scenario in order:
                row, attempts, abstract = run_case(paper, meta, scenario, primary, primary_mode, s2_key, oa_key)
                paper_results[scenario] = row
                for attempt in attempts:
                    trace.write(json.dumps(attempt, ensure_ascii=False) + "\n")
                trace.flush()
                print(row["paper"], scenario, row["source"] or row["status"], row["passed"], flush=True)
                if scenario == "forced_429" and not primary and abstract:
                    # Exercise the healthy adapter path with already obtained real evidence when
                    # the live index was unavailable. This is explicitly a controlled 200.
                    primary = {"title": meta.title, "abstract": abstract, "externalIds": {
                        "DOI": meta.doi, "ACL": acl_anthology_id(meta.doi), "ArXiv": meta.arxiv_id,
                    }}
                    primary_mode = "controlled_200_using_retrieved_backup"
            results.extend(paper_results[scenario] for scenario in SCENARIOS)

    with (args.output_dir / "fallback_matrix.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)


if __name__ == "__main__":
    main()
