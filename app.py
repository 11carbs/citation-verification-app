"""Minimal Streamlit shell for the citation-verification prototype."""

from __future__ import annotations

import streamlit as st

from src.citation_parser import CitationParseError
from src.mock_adapters import MockJevAdapter, MockReferenceLookupAdapter
from src.mock_data import MOCK_CLAIM, MOCK_REFERENCES
from src.pairing import build_pairing_outcome
from src.pipeline import run_method_a_pipeline
from src.presentation import (
    build_output_dataframe,
    dataframe_to_csv_bytes,
    style_output_dataframe,
)
from src.schemas import AnalysisRow, ReferenceMatchStatus
from src.validation import IssueSeverity, validate_required_inputs


st.set_page_config(
    page_title="未獲來源支持引用偵測系統",
    page_icon="🔎",
    layout="wide",
)


def initialize_state() -> None:
    st.session_state.setdefault("claim_text", MOCK_CLAIM)
    st.session_state.setdefault("reference_text", MOCK_REFERENCES)
    st.session_state.setdefault("show_results", False)
    st.session_state.setdefault("analysis_rows", [])
    st.session_state.setdefault("result_issues", [])
    st.session_state.setdefault("use_mock_pipeline", True)


def clear_form() -> None:
    st.session_state.claim_text = ""
    st.session_state.reference_text = ""
    st.session_state.show_results = False
    st.session_state.analysis_rows = []
    st.session_state.result_issues = []


initialize_state()

st.title("未獲來源支持引用偵測系統")
st.caption("第一階段原型：人工輸入、限定 IEEE 格式、全文暫不處理")

st.progress(0.20, text="輸入 → 擷取引用 → 比對 → 結果 → 下載")

with st.container(border=True):
    st.subheader("輸入資料")
    st.toggle(
        "開發測試模式：使用 mock 摘要與 Jev 結果",
        key="use_mock_pipeline",
        help="此模式不會呼叫外部 API；結果只用來測試串接與畫面。",
    )
    st.text_area(
        "① 欲比對的段落",
        key="claim_text",
        height=140,
        help="請貼上含有 IEEE 文內引用編號（例如 [79]）的段落。",
    )
    st.text_area(
        "② 參考文獻清單（ref）",
        key="reference_text",
        height=260,
        help="請貼上論文最後的參考文獻清單，不用先挑選。",
    )

    left, right = st.columns([1, 1])
    with left:
        st.button("清除", on_click=clear_form, width="stretch")
    with right:
        if st.button("開始比對", type="primary", width="stretch"):
            input_issues = validate_required_inputs(
                st.session_state.claim_text,
                st.session_state.reference_text,
            )
            if input_issues:
                for issue in input_issues:
                    st.error(issue.message)
                st.session_state.show_results = False
            else:
                try:
                    outcome = build_pairing_outcome(
                        st.session_state.claim_text,
                        st.session_state.reference_text,
                    )
                except CitationParseError as error:
                    st.error(str(error))
                    st.session_state.show_results = False
                else:
                    if st.session_state.use_mock_pipeline:
                        rows = run_method_a_pipeline(
                            outcome.pairs,
                            MockReferenceLookupAdapter(),
                            MockJevAdapter(),
                        )
                    else:
                        rows = [AnalysisRow(pair=pair) for pair in outcome.pairs]
                    st.session_state.analysis_rows = rows
                    st.session_state.result_issues = outcome.issues
                    st.session_state.show_results = True

if st.session_state.show_results:
    st.divider()
    st.subheader("引用配對結果")
    rows: list[AnalysisRow] = st.session_state.analysis_rows
    result_issues = st.session_state.result_issues
    for issue in result_issues:
        if issue.severity is IssueSeverity.ERROR:
            st.error(issue.message)
        else:
            st.warning(issue.message)
    if not rows:
        if not result_issues:
            st.warning("段落中沒有偵測到可解析的 IEEE 數字引用。")
    else:
        missing_count = sum(
            row.pair.match_status is ReferenceMatchStatus.MISSING_REFERENCE
            for row in rows
        )
        st.write(f"偵測到 {len(rows)} 個引用配對")
        if missing_count:
            st.warning(f"其中 {missing_count} 筆在 ref 清單中找不到對應條目。")
        if st.session_state.use_mock_pipeline:
            st.warning(
                "目前方法 A 顯示的是 MOCK 摘要與 Jev 結果，只供串接測試；"
                "方法 B 尚未串接並保持空白。"
            )
        else:
            st.info("方法 A 與方法 B 尚未串接，因此相關結果欄位暫時留白。")
        st.markdown(
            "**暫定分級顏色：** "
            "<span style='background:#E8F5E9;color:#1B5E20;padding:3px 10px;"
            "border-radius:12px;font-weight:600'>支持</span> "
            "<span style='background:#FFF3E0;color:#A65300;padding:3px 10px;"
            "border-radius:12px;font-weight:600'>不確定</span> "
            "<span style='background:#FDECEC;color:#B42318;padding:3px 10px;"
            "border-radius:12px;font-weight:600'>不支持</span>",
            unsafe_allow_html=True,
        )
        frame = build_output_dataframe(rows)
        st.dataframe(
            style_output_dataframe(frame),
            hide_index=True,
            width="stretch",
            column_config={
                "引用陳述": st.column_config.TextColumn(width="large"),
                "被引文獻": st.column_config.TextColumn(width="large"),
                "方法 A 支持分數": st.column_config.NumberColumn(format="%.2f"),
                "方法 B 支持分數": st.column_config.NumberColumn(format="%.2f"),
            },
        )
        st.download_button(
            "匯出 CSV",
            data=dataframe_to_csv_bytes(frame),
            file_name="citation_pairing_results.csv",
            mime="text/csv",
            type="primary",
        )



