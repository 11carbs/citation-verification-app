"""Whole-paper text and file inputs share one document conversion entry point."""
from hashlib import sha256
import json
import streamlit as st
from src.document_conversion import (
    SUPPORTED_EXTENSIONS, ConversionError, convert_document, convert_pasted_paper,
)

st.set_page_config(page_title="論文轉 Markdown", layout="wide")
st.title("論文輸入與 Markdown 轉換")
st.caption("轉換工作區：本頁尚未自動抽取引用或呼叫模型。")
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
    st.session_state["converted_identity"] = identity

if st.button("轉成 Markdown", type="primary"):
    if data is None:
        st.error("請先上傳檔案。")
    else:
        try:
            st.session_state["converted_document"] = (
                convert_pasted_paper(pasted) if mode == "貼上整篇論文"
                else convert_document(data, filename)
            )
        except ConversionError as error:
            st.session_state.pop("converted_document", None)
            st.error(f"{error.code}：{error}")

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
