"""PDF/DOCX whole-paper upload and citation pairing."""
from dataclasses import asdict
from hashlib import sha256
import json
import pandas as pd
import streamlit as st
from src.document_conversion import SUPPORTED_EXTENSIONS, ConversionError, convert_document
from src.handoff_bundle import (
    HandoffValidationError, build_handoff_bundle, pairing_preview_csv_bytes,
    whole_paper_result_csv_bytes, whole_paper_result_rows,
)
from src.whole_paper import extract_whole_paper
from src.presentation import style_output_dataframe


def format_locations(locations):
    return "; ".join(
        f"{location.locator}，行 {location.line_start}–{location.line_end}"
        for location in locations
    )


st.set_page_config(page_title="未獲來源支持引用偵測系統", layout="wide")
st.title("未獲來源支持引用偵測系統")
st.caption("上傳 PDF 或 DOCX 論文，自動擷取引用並配對文末參考文獻。")
uploaded = st.file_uploader(
    "上傳待查核論文（PDF／DOCX，30 MiB 以下）", type=list(SUPPORTED_EXTENSIONS)
)
filename = uploaded.name if uploaded is not None else ""
data = uploaded.getvalue() if uploaded is not None else None

identity = (filename, sha256(data).hexdigest()) if data is not None else None
if st.session_state.get("converted_identity") != identity:
    st.session_state.pop("converted_document", None)
    st.session_state.pop("paper_extraction", None)
    st.session_state.pop("handoff_bundle", None)
    st.session_state["converted_identity"] = identity

if st.button("分析引用", type="primary"):
    if data is None:
        st.error("請先上傳檔案。")
    else:
        try:
            converted = convert_document(data, filename)
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
    st.success("論文已轉換，並完成引用與參考文獻的初步配對。")
    for warning in result.warnings:
        st.warning(warning)

    outcome = st.session_state.get("paper_extraction")
    if outcome is not None:
        bundle = st.session_state.get("handoff_bundle")
        st.subheader("引用配對結果")
        cited_sources = len({pair.reference.ref_id for pair in bundle.ready_pairs}) if bundle else 0
        claims = len({pair.claim_id for pair in outcome.pairs})
        st.write(
            f"參考文獻清單 {len(outcome.references)} 筆；辨識到帶引用的陳述 {claims} 段；"
            f"引用與來源的配對 {len(outcome.pairs)} 筆；已配對到 {cited_sources} 篇不同來源。"
        )
        st.caption("『帶引用的陳述』是正文中被程式切出的含引用文字，可能是一句或一段，不代表判斷正確。同一來源多次出現，或同一句引用多篇來源，都會增加配對筆數。")
        if bundle is not None:
            st.caption(
                f"找到唯一 ref、可交第二人查來源：{len(bundle.ready_pairs)} 筆；"
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
            st.info("方法 A／B 欄位已保留；本頁目前只完成引用配對，尚未執行來源查詢與模型判斷，因此分數與證據欄位保持空白。")
            st.caption("作者年份型文獻本來沒有原文編號；ref ID 是系統產生的識別碼。若無法對到文末書目，ref ID 與被引文獻會留空，原因顯示在配對狀態與錯誤狀態。")
            st.markdown(
                "**暫定支持分類：** "
                "<span style='background:#E8F5E9;color:#1B5E20;padding:3px 10px;border-radius:12px;font-weight:600'>支持</span> "
                "<span style='background:#FFF3E0;color:#A65300;padding:3px 10px;border-radius:12px;font-weight:600'>不確定</span> "
                "<span style='background:#FDECEC;color:#B42318;padding:3px 10px;border-radius:12px;font-weight:600'>不支持</span>",
                unsafe_allow_html=True,
            )
            frame = pd.DataFrame(whole_paper_result_rows(bundle))
            st.dataframe(
                style_output_dataframe(frame, ["方法 A 支持分類（暫定）", "方法 B 支持分類（暫定）"]),
                hide_index=True, width="stretch",
                column_config={
                    "引用陳述": st.column_config.TextColumn(width="large"),
                    "被引文獻": st.column_config.TextColumn(width="large"),
                },
            )
        if bundle is not None:
            st.download_button(
                "下載結果表 CSV（含 A／B 空白欄位）", whole_paper_result_csv_bytes(bundle),
                file_name=result.document_id + "-results.csv", mime="text/csv",
            )

    with st.expander("其他下載（團隊串接與資料核對）"):
        st.caption("以下檔案供團隊串接或人工核對；一般查看結果只需使用上方表格與 CSV。")
        st.download_button("下載 Markdown", result.markdown.encode("utf-8"),
                           file_name=result.document_id + ".md", mime="text/markdown")
        st.download_button("下載文件結構 JSON", json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                           file_name=result.document_id + ".json", mime="application/json")
        if outcome is not None:
            st.download_button(
                "下載完整擷取結果 JSON", json.dumps(asdict(outcome), ensure_ascii=False, indent=2),
                file_name=result.document_id + "-pairs.json", mime="application/json",
            )
        if outcome is not None and bundle is not None:
            st.caption("第二人交接檔只列出找到唯一 ref 的配對；缺失與歧義項目另列待核對。")
            st.download_button(
                "下載第二人交接 JSON", bundle.to_json_bytes(),
                file_name=result.document_id + "-handoff.json", mime="application/json",
            )
            st.download_button(
                "下載配對資料 CSV", pairing_preview_csv_bytes(bundle),
                file_name=result.document_id + "-pairing.csv", mime="text/csv",
            )
