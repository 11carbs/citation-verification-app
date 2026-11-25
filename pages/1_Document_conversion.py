"""Whole-paper text and file inputs share one document conversion entry point."""
from dataclasses import asdict
from hashlib import sha256
import json
import streamlit as st
from src.document_conversion import (
    SUPPORTED_EXTENSIONS, ConversionError, convert_document, convert_pasted_paper,
)
from src.handoff_bundle import (
    HandoffValidationError, build_handoff_bundle, pairing_preview_csv_bytes,
    pairing_preview_rows,
)
from src.whole_paper import extract_whole_paper


def format_locations(locations):
    return "; ".join(
        f"{location.locator}，行 {location.line_start}–{location.line_end}"
        for location in locations
    )


st.set_page_config(page_title="論文轉 Markdown", layout="wide")
st.title("論文輸入與 Markdown 轉換")
st.caption("擷取整篇論文的數字型及作者年份型引用；本頁尚未取得來源全文或判斷支持程度。")
mode = st.radio("輸入方式", ("貼上整篇論文", "上傳檔案"), horizontal=True)
if mode == "貼上整篇論文":
    pasted = st.text_area(
        "論文全文（包含正文與文末參考文獻）",
        height=320,
        help="請貼上整篇論文文字；不用另外貼上 ref。純文字與 Markdown 都可以。",
    )
    filename = "pasted-paper.md"
    data = pasted.encode("utf-8") if pasted.strip() else b""
else:
    uploaded = st.file_uploader(
        "上傳待查核論文（30 MiB 以下）", type=list(SUPPORTED_EXTENSIONS)
    )
    filename = uploaded.name if uploaded is not None else ""
    data = uploaded.getvalue() if uploaded is not None else None

identity = (mode, filename, sha256(data).hexdigest()) if data is not None else None
if st.session_state.get("converted_identity") != identity:
    st.session_state.pop("converted_document", None)
    st.session_state.pop("paper_extraction", None)
    st.session_state.pop("handoff_bundle", None)
    st.session_state["converted_identity"] = identity

if st.button("轉成 Markdown", type="primary"):
    if data is None:
        st.error("請先上傳檔案。")
    else:
        try:
            converted = (
                convert_pasted_paper(pasted) if mode == "貼上整篇論文"
                else convert_document(data, filename)
            )
            st.session_state["converted_document"] = converted
            extracted = extract_whole_paper(converted)
            st.session_state["paper_extraction"] = extracted
            st.session_state["handoff_bundle"] = build_handoff_bundle(converted, extracted)
        except ConversionError as error:
            st.session_state.pop("converted_document", None)
            st.session_state.pop("paper_extraction", None)
            st.session_state.pop("handoff_bundle", None)
            st.error(f"{error.code}：{error}")
        except HandoffValidationError as error:
            st.session_state.pop("handoff_bundle", None)
            st.error(f"交接資料檢查失敗：{error}")

result = st.session_state.get("converted_document")
if result is not None:
    st.success(f"完成轉換：{len(result.blocks)} 個文字區塊")
    for warning in result.warnings:
        st.warning(warning)
    # Display source literally; uploaded HTML/Markdown is not trusted UI content.
    st.code(result.markdown, language="markdown")
    st.download_button("下載 Markdown", result.markdown.encode("utf-8"),
                       file_name=result.document_id + ".md", mime="text/markdown")
    st.download_button("下載文件結構 JSON", json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                       file_name=result.document_id + ".json", mime="application/json")

    outcome = st.session_state.get("paper_extraction")
    if outcome is not None:
        bundle = st.session_state.get("handoff_bundle")
        st.subheader("自動擷取的引用配對")
        st.write(f"參考文獻 {len(outcome.references)} 筆；引用配對 {len(outcome.pairs)} 筆")
        if bundle is not None:
            st.caption(
                f"可交第二人查來源：{len(bundle.ready_pairs)} 筆；"
                f"需人工核對：{len(bundle.review_pairs)} 筆。"
            )
        with st.expander("查看辨識的參考文獻"):
            if outcome.references:
                st.dataframe([{
                    "ref ID": ref.ref_id, "參考文獻原文": ref.raw_text,
                    "年份": ref.year, "DOI": ref.doi,
                    "原文位置": format_locations(ref.source_locations),
                } for ref in outcome.references], hide_index=True, width="stretch")
            else:
                st.info("尚未辨識到參考文獻條目。")
        for issue in outcome.issues:
            st.warning(f"{issue.code}：{issue.message}")
        if bundle is not None and outcome.pairs:
            st.dataframe([{
                "pair_id": row["pair_id"],
                "引用陳述": row["claim_text"],
                "引用標記": row["citation_marker"],
                "配對狀態": row["match_status"],
                "ref ID": row["ref_id"],
                "被引文獻": row["ref_raw"],
                "歧義候選": row["candidate_ref_ids"],
                "陳述原文位置": row["source_locations"],
                "待處理原因": row["error_message"],
            } for row in pairing_preview_rows(bundle)], hide_index=True, width="stretch")
        st.download_button(
            "下載完整擷取結果 JSON", json.dumps(asdict(outcome), ensure_ascii=False, indent=2),
            file_name=result.document_id + "-pairs.json", mime="application/json",
        )
        if bundle is not None:
            st.caption("交接檔只將已配對項目列入待查詢清單；缺失與歧義項目留在待核對清單。")
            st.download_button(
                "下載第二人交接 JSON", bundle.to_json_bytes(),
                file_name=result.document_id + "-handoff.json", mime="application/json",
            )
            st.download_button(
                "下載配對預覽 CSV", pairing_preview_csv_bytes(bundle),
                file_name=result.document_id + "-pairing.csv", mime="text/csv",
            )
