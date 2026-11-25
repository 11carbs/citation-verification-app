"""New conversion workspace; existing citation and model workflow stays separate."""
import json
import streamlit as st
from src.document_conversion import (
    SUPPORTED_EXTENSIONS, ConversionError, conversion_capabilities, convert_document,
)

st.set_page_config(page_title="論文轉 Markdown", layout="wide")
st.title("論文輸入與 Markdown 轉換")
st.caption("轉換工作區：本頁尚未自動抽取引用或呼叫模型。")
if not conversion_capabilities()["doc_ready"]:
    st.info("此環境未安裝 LibreOffice，DOC 暫時無法轉換；其餘格式可測試。")
uploaded = st.file_uploader("上傳待查核論文（30 MiB 以下）", type=list(SUPPORTED_EXTENSIONS))
if uploaded is not None:
    try:
        result = convert_document(uploaded.getvalue(), uploaded.name)
    except ConversionError as error:
        st.error(f"{error.code}：{error}")
    else:
        st.success(f"完成轉換：{len(result.blocks)} 個文字區塊")
        for warning in result.warnings:
            st.warning(warning)
        # Display source literally; uploaded HTML/Markdown is not trusted UI content.
        st.code(result.markdown, language="markdown")
        st.download_button("下載 Markdown", result.markdown.encode("utf-8"),
                           file_name=result.document_id + ".md", mime="text/markdown")
        st.download_button("下載文件結構 JSON", json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                           file_name=result.document_id + ".json", mime="application/json")
