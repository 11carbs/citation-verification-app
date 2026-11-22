"""Capture one rate-limit observation without automatic retries or redirects."""

import argparse
import json
from pathlib import Path

import streamlit as st

from src.reference_lookup import RequestsClient, S2_BASE


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Non-secret label for this device and network")
    parser.add_argument("--endpoint", choices=("paper", "protected"), default="paper")
    parser.add_argument("--anonymous-control", action="store_true", help="One unauthenticated paper control")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.anonymous_control and args.endpoint != "paper":
        parser.error("the protected endpoint requires authentication")

    headers = None
    if not args.anonymous_control:
        key = st.secrets.get("S2_API_KEY", "").strip()
        if not key:
            parser.error("S2_API_KEY is missing")
        headers = {"x-api-key": key}
    if args.endpoint == "paper":
        url = f"{S2_BASE}/paper/ARXIV:2005.11401"
        params = {"fields": "title"}
    else:
        url = "https://api.semanticscholar.org/datasets/v1/release/latest/dataset/abstracts"
        params = None

    client = RequestsClient(retries=1, timeout=12, request_source=args.source)
    with client.session:
        client.get(url, params, headers)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as output:
        for attempt in client.attempts:
            output.write(json.dumps(attempt, ensure_ascii=False) + "\n")
            print(json.dumps(attempt, ensure_ascii=False))


if __name__ == "__main__":
    main()
