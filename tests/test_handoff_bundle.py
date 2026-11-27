"""Contract checks for handoff to full-text retrieval, without calling it."""

from dataclasses import replace
from io import BytesIO
import json

import pytest

from src.document_conversion import convert_document, convert_pasted_paper
from src.handoff_bundle import (
    HandoffValidationError, build_handoff_bundle, pairing_preview_csv_bytes,
    pairing_preview_rows, whole_paper_result_csv_bytes, whole_paper_result_rows,
)
from src.whole_paper import extract_whole_paper


def _example():
    document = convert_pasted_paper(
        "同一主張使用多篇來源 [1], [2], [9].\n\n參考文獻\n"
        "[1] A. Author, First source.\n[2] B. Author, Second source.\n"
        "[2] B. Other, Duplicate number.\n[3] C. Author, Uncited source."
    )
    return document, extract_whole_paper(document)


def test_bundle_separates_confirmed_and_review_pairs_without_uncited_refs():
    document, outcome = _example()
    bundle = build_handoff_bundle(document, outcome)
    assert bundle.schema_version == "citation-batch.v1"
    assert [pair.citation_marker for pair in bundle.ready_pairs] == ["[1]"]
    assert [pair.match_status for pair in bundle.review_pairs] == ["ambiguous", "missing_reference"]
    assert [ref.ref_id for ref in bundle.references] == ["ref_n_1", "ref_n_2", "ref_n_2_dup2"]
    assert len({pair.claim_id for pair in (*bundle.ready_pairs, *bundle.review_pairs)}) == 1
    assert all(pair.document_id == bundle.document_id for pair in bundle.ready_pairs)
    assert bundle.review_pairs[0].candidate_ref_ids == ("ref_n_2", "ref_n_2_dup2")
    assert bundle.review_pairs[1].reference is None


def test_author_year_pair_and_original_location_survive_json_export():
    document = convert_pasted_paper(
        "Wang (2024) reported a result.\n\nReferences\nWang, T. (2024). Source."
    )
    bundle = build_handoff_bundle(document, extract_whole_paper(document))
    payload = json.loads(bundle.to_json_bytes())
    pair = payload["ready_pairs"][0]
    assert pair["reference"]["ref_id"] == "ref_a_001"
    assert pair["source_locations"][0]["line_start"] == 1
    assert payload["references"][0]["raw_text"].startswith("Wang, T.")
    assert payload["review_pairs"] == []


def test_preview_rows_and_csv_include_review_states_and_chinese():
    document, outcome = _example()
    bundle = build_handoff_bundle(document, outcome)
    rows = pairing_preview_rows(bundle)
    assert [row["match_status"] for row in rows] == ["matched", "ambiguous", "missing_reference"]
    assert rows[1]["candidate_ref_ids"] == "ref_n_2; ref_n_2_dup2"
    exported = pairing_preview_csv_bytes(bundle)
    assert exported.startswith(b"\xef\xbb\xbf")
    assert "同一主張" in exported.decode("utf-8-sig")
    assert "missing_reference" in exported.decode("utf-8-sig")


def test_whole_paper_result_table_keeps_ab_fields_empty_until_judged():
    document, outcome = _example()
    bundle = build_handoff_bundle(document, outcome)
    rows = whole_paper_result_rows(bundle)
    assert len(rows) == 3
    assert rows[0]["ref ID（系統）"] == "ref_n_1"
    assert rows[0]["原文編號"] == "1"
    assert rows[0]["方法 A 支持分數"] == ""
    assert rows[0]["方法 B 支持分數"] == ""
    assert rows[0]["方法 A 支持分類（暫定）"] == ""
    assert rows[0]["方法 B 支持分類（暫定）"] == ""
    assert rows[0]["方法 A 判斷狀態"] == "尚未執行"
    assert rows[1]["ref 配對狀態"] == "待人工核對"
    exported = whole_paper_result_csv_bytes(bundle).decode("utf-8-sig")
    assert "方法 A 支持分數" in exported
    assert "方法 B 支持分數" in exported
    assert "方法 A 支持分類（暫定）" in exported


@pytest.mark.parametrize("change", [
    lambda pair: replace(pair, source_locations=()),
    lambda pair: replace(pair, document_id="other"),
    lambda pair: replace(pair, citation_group_id="other"),
])
def test_invalid_links_cannot_be_handed_off(change):
    document, outcome = _example()
    altered = replace(outcome, pairs=(change(outcome.pairs[0]), *outcome.pairs[1:]))
    with pytest.raises(HandoffValidationError):
        build_handoff_bundle(document, altered)


def test_duplicate_pair_id_is_rejected():
    document, outcome = _example()
    repeated = replace(outcome.pairs[1], pair_id=outcome.pairs[0].pair_id)
    with pytest.raises(HandoffValidationError):
        build_handoff_bundle(document, replace(outcome, pairs=(outcome.pairs[0], repeated)))


def test_unparsed_pair_stays_out_of_source_lookup():
    document = convert_pasted_paper(
        "A result (Wang 2024).\n\nReferences\nWang, T. (2024). Source."
    )
    bundle = build_handoff_bundle(document, extract_whole_paper(document))
    assert not bundle.ready_pairs
    assert len(bundle.review_pairs) == 1
    assert bundle.review_pairs[0].match_status == "unparsed"
    assert not bundle.references


@pytest.mark.parametrize("extension", ["pdf", "docx"])
def test_two_input_formats_produce_valid_handoff(extension):
    if extension == "pdf":
        import pymupdf
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "Claim [1].")
        page.insert_text((72, 120), "References")
        page.insert_text((72, 145), "[1] A. Author, Source.")
        data = pdf.tobytes()
    elif extension == "docx":
        from docx import Document
        docx = Document()
        docx.add_paragraph("Claim [1].")
        docx.add_heading("References", 1)
        docx.add_paragraph("[1] A. Author, Source.")
        stream = BytesIO()
        docx.save(stream)
        data = stream.getvalue()
    document = convert_document(data, "paper." + extension)
    bundle = build_handoff_bundle(document, extract_whole_paper(document))
    assert len(bundle.ready_pairs) == 1
    assert len(bundle.references) == 1
    assert not bundle.review_pairs
