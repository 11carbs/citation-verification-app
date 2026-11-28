"""Minimal Streamlit shell for the citation-verification prototype."""

from __future__ import annotations

import os

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from dataclasses import dataclass

from src.citation_parser import CitationParseError
from src.fulltext_sources import FullTextFetcher, ManualUpload
from src.jev_adapter import OpenRouterJevAdapter
from src.method_b import DEFAULT_METHOD_B_MODEL, run_method_b
from src.mock_adapters import MockJevAdapter, MockReferenceLookupAdapter
from src.mock_data import MOCK_CLAIM, MOCK_REFERENCES
from src.pairing import build_pairing_outcome
from src.pipeline import run_method_a_pipeline
from src.reference_parser import parse_reference_list
from src.presentation import (
    build_output_dataframe,
    dataframe_to_csv_bytes,
    style_output_dataframe,
    summarize_shared_claims,
)
from src.reference_lookup import SCHOLAR_REQUEST_SCHEDULE, ReferenceLookupAdapter, RequestsClient
from src.schemas import AnalysisRow, CitationPair, ReferenceMatchStatus
from src.source_evidence import EvidenceResolver
from src.validation import IssueSeverity, validate_required_inputs


# 轉檔那一層支援的副檔名（見 FullTextFetcher.fetch_upload）。少列一個，使用者就
# 會傳進一個必定失敗的檔案，所以這份清單要跟著那邊一起改。
MANUAL_UPLOAD_TYPES = ["pdf", "html", "htm", "xml", "nxml", "jats", "md", "markdown", "txt"]


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
    st.session_state.setdefault("result_mode", "")
    st.session_state.setdefault("reference_cooldowns", {})
    st.session_state.setdefault("scholar_abstracts", {})
    st.session_state.setdefault("use_mock_pipeline", True)
    st.session_state.setdefault("use_evidence_layer", True)
    st.session_state.setdefault("manual_round", 0)
    st.session_state.setdefault("manual_upload_plan", {})


def clear_form() -> None:
    st.session_state.claim_text = ""
    st.session_state.reference_text = ""
    # 上傳的檔案也要一起清掉：清除代表要換一篇論文，而別篇論文的 ref [3] 是另一份
    # 文獻。留著只會把上一篇的全文指給這一篇，結果是假的「引用與來源不符」。
    # file_uploader 的 key 是唯讀的（指派會丟 StreamlitValueAssignmentNotAllowedError），
    # 所以換一個 key 讓它重新長出來。
    st.session_state.manual_round += 1
    st.session_state.manual_upload_plan = {}
    st.session_state.show_results = False
    st.session_state.analysis_rows = []
    st.session_state.result_issues = []
    st.session_state.result_mode = ""


def read_api_key(name: str) -> str:
    try:
        value = st.secrets.get(name, "")
    except StreamlitSecretNotFoundError:
        return ""
    return value.strip() if isinstance(value, str) else ""


def manual_ref_key(item) -> str:
    """每個上傳檔案自己的編號輸入框。

    用 ``file_id`` 而不是順序：拿掉清單中間一個檔案時，用順序當鍵會讓後面每個
    檔案的編號往前錯一格，等於把別篇論文的全文指給這一條 ref。
    """

    return f"manual_ref_{getattr(item, 'file_id', None) or item.name}"


@dataclass(frozen=True, slots=True)
class UploadPlan:
    """哪些上傳檔案會被使用，以及其他檔案為什麼不被使用。"""

    uploads: dict[int, ManualUpload]
    bound: dict[int, str]              # ref 編號 -> 它目前對應的書目原文
    problems: list[str]
    # 要寫回 session state 的綁定：檔案 -> (ref 編號, 當時那條書目的原文)。
    # **被拒絕的檔案要保留它原本的綁定**，不可以只收這次通過的。只收通過的話，
    # 下一個 rerun 查不到舊綁定就等於沒綁過，檢查整個跳過、檔案被重新綁到新書目——
    # 而「按下開始比對」本身就是下一個 rerun，所以拒絕根本擋不到那一次分析。
    keep: dict[str, tuple[int, str]]


def assign_manual_uploads(items, reference_text: str) -> UploadPlan:
    """把上傳的檔案配到使用者填的 ref 編號上，並檢查那個編號現在指的是哪一篇。

    編號留 0 代表還沒指定，不送進比對。另外擋三件事，每一件都會讓系統拿**別篇
    論文**的全文去比對，產生假的「引用與來源不符」：

    1. 同一個編號被指定給兩個檔案 → 兩個都不用。誰贏都只是猜的。
    2. 編號不在目前的參考文獻清單裡（打錯字、清單還沒貼）→ 不用。
       原本這種情況完全沒有提示，畫面還會寫「比對時會使用」，其實整份沒作用。
    3. 檔案綁定的那條書目**換內容了**（換了一篇論文卻沒按「清除」）→ 不用。
       這是最危險的一種：ref [1] 還在，但已經是另一份文獻了。
    """

    entries = parse_reference_list(reference_text)
    previous = st.session_state.get("manual_bound_refs") or {}
    uploads: dict[int, ManualUpload] = {}
    bound: dict[int, str] = {}
    problems: list[str] = []
    keep: dict[str, tuple[int, str]] = {}

    chosen = [(item, int(st.session_state.get(manual_ref_key(item), 0) or 0))
              for item in items]
    chosen = [(item, number) for item, number in chosen if number > 0]

    # 撞號先判，而且要判在「編號存不存在」之前：兩個檔案搶同一個編號是使用者的
    # 指定本身有問題，不會因為那個編號剛好不在清單裡就不算。
    picked = [number for _, number in chosen]
    clashes = {number for number in picked if picked.count(number) > 1}
    for number in sorted(clashes):
        problems.append(f"ref [{number}] 被指定給一個以上的檔案，這一條的上傳全部不使用。")

    for item, number in chosen:
        key = manual_ref_key(item)
        was = previous.get(key)
        if was is not None:
            # 預設留著舊綁定。被拒絕的檔案如果不留，下一個 rerun 就查不到綁定，
            # 檢查會整個跳過、檔案被重新綁到新書目。
            keep[key] = (int(was[0]), str(was[1]))
        if number in clashes:
            continue
        entry = entries.get(number)
        if entry is None:
            problems.append(
                f"ref [{number}]（{item.name}）不在目前的參考文獻清單裡，這個檔案不會被使用。")
            continue
        # 使用者自己把檔案改配到另一個編號，是刻意的指定，重新綁定就好。
        # 只有「編號沒變、那條書目的內容變了」才是換了一篇論文卻沒按「清除」。
        if was is not None and int(was[0]) == number and str(was[1]) != entry.raw_text:
            problems.append(
                f"ref [{number}] 的書目換了內容，{item.name} 不會被使用。"
                "確認這個檔案對應的還是同一篇文獻之後，把編號改一次（或重新上傳檔案）"
                "就會重新綁定；要整批重來請按「清除」。")
            continue
        uploads[number] = ManualUpload(name=item.name, data=item.getvalue(), label=item.name)
        bound[number] = entry.raw_text
        keep[key] = (number, entry.raw_text)
    return UploadPlan(uploads=uploads, bound=bound, problems=problems, keep=keep)


def collect_manual_uploads() -> dict[int, ManualUpload]:
    return st.session_state.get("manual_upload_plan") or {}


def build_evidence_resolver() -> EvidenceResolver:
    """取全文 → 退摘要 的單一入口，手動上傳優先（現行架構第 3 點）。"""

    reference_adapter = build_reference_adapter()
    return EvidenceResolver(
        FullTextFetcher(openalex_key=read_api_key("OPENALEX_API_KEY")),
        reference_adapter,
        manual_uploads=collect_manual_uploads(),
    )


def build_reference_adapter() -> ReferenceLookupAdapter:
    return ReferenceLookupAdapter(
        s2_key=read_api_key("S2_API_KEY"),
        openalex_key=read_api_key("OPENALEX_API_KEY"),
        http=RequestsClient(
            cooldowns=st.session_state.reference_cooldowns,
            scholar_schedule=SCHOLAR_REQUEST_SCHEDULE,
            request_source=os.environ.get("CITATION_REQUEST_SOURCE", "streamlit-app"),
        ),
        scholar_cache=st.session_state.scholar_abstracts,
    )


def analyze_pairs(pairs: list[CitationPair]) -> tuple[list[AnalysisRow], str]:
    if st.session_state.use_mock_pipeline:
        rows = run_method_a_pipeline(pairs, MockReferenceLookupAdapter(), MockJevAdapter())
        return rows, "mock"

    api_key = read_api_key("OPENROUTER_API_KEY")
    if not api_key:
        return [AnalysisRow(pair=pair) for pair in pairs], "missing_key"

    jev_adapter = OpenRouterJevAdapter(
        api_key, include_evidence_excerpt=True, resolve_abstract_title=True,
    )
    if st.session_state.use_evidence_layer:
        # 現行架構第 5 點：兩條判斷路徑要拿到同一份證據，所以證據由取證層先備好
        rows = run_method_a_pipeline(
            pairs, None, jev_adapter, evidence_resolver=build_evidence_resolver(),
        )
    else:
        # 關掉取證層時走原本的分階段查摘要，手動上傳不會生效
        rows = run_method_a_pipeline(pairs, build_reference_adapter(), jev_adapter)
    rows = run_method_b(
        rows, st.session_state.claim_text, st.session_state.reference_text, api_key,
        model=os.environ.get("OPENROUTER_METHOD_B_MODEL") or DEFAULT_METHOD_B_MODEL,
    )
    return rows, "jev"


initialize_state()

st.title("未獲來源支持引用偵測系統")
st.caption("原型：貼上段落與參考文獻；被引文獻自動取全文，取不到退摘要，也可手動上傳")

st.progress(0.20, text="輸入 → 擷取引用 → 比對 → 結果 → 下載")

with st.container(border=True):
    st.subheader("輸入資料")
    use_mock_pipeline = st.toggle(
        "開發測試模式：使用 mock 摘要與 Jev 結果",
        key="use_mock_pipeline",
        help="此模式不會呼叫外部 API；結果只用來測試串接與畫面。",
    )
    if not use_mock_pipeline:
        st.caption("正式比對時，方法 A 會將引用句和證據送到 OpenRouter，方法 B 會送出貼上的段落與書目。")
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

    with st.expander("③ 手動上傳被引文獻（備援，可不填）"):
        st.toggle(
            "自動取得被引文獻全文（取不到退摘要）",
            key="use_evidence_layer",
            help="關掉會退回只查摘要的舊流程，手動上傳也不會生效。",
        )
        if not st.session_state.use_evidence_layer:
            st.warning("自動取全文已關閉，這一區上傳的檔案不會被使用。")
        st.caption(
            "系統取不到全文的文獻（付費牆、掃描檔、查不到），可以自己上傳。"
            f"支援 {'、'.join(MANUAL_UPLOAD_TYPES)}。"
            "有上傳的那一條 ref 直接用你的檔案，不再連網找；檔案轉不出內容時會照常"
            "走自動流程，原因寫在結果表的「摘要查詢紀錄」。\n\n"
            "⚠️ 要上傳**完整全文**。出版社的付費牆頁面、只抄一段的筆記都會被擋下來"
            "（轉出來不到 1000 字就不算全文），因為那種內容會讓系統給出看起來有憑有據、"
            "其實是假的「不支持」。"
        )
        uploads = st.file_uploader(
            "被引文獻檔案",
            type=MANUAL_UPLOAD_TYPES,
            accept_multiple_files=True,
            key=f"manual_files_{st.session_state.manual_round}",
        )
        if uploads:
            st.caption("指定每個檔案對應的 ref 編號。留 0 表示這次不使用這個檔案。")
            for item in uploads:
                name_column, number_column = st.columns([4, 1])
                name_column.text(f"{item.name}（{item.size:,} bytes）")
                number_column.number_input(
                    "ref 編號", min_value=0, step=1, key=manual_ref_key(item),
                    label_visibility="collapsed",
                )
        plan = assign_manual_uploads(uploads or [], st.session_state.reference_text)
        st.session_state.manual_upload_plan = plan.uploads
        st.session_state.manual_bound_refs = plan.keep
        for problem in plan.problems:
            st.error(problem)
        if plan.uploads and st.session_state.use_evidence_layer:
            # 把檔案會被當成「哪一篇文獻」寫出來。只印編號的話，換了一篇論文卻沒按
            # 「清除」時，畫面看起來一切正常，實際上拿的是上一篇的全文。
            st.success("比對時會使用：")
            for number in sorted(plan.uploads):
                st.caption(f"[{number}] {plan.uploads[number].name} → {plan.bound[number]}")

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
                    with st.spinner("正在比對引用，請稍候。"):
                        rows, result_mode = analyze_pairs(outcome.pairs)
                    st.session_state.analysis_rows = rows
                    st.session_state.result_mode = result_mode
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
        shared_claims = summarize_shared_claims(rows)
        missing_count = sum(
            row.pair.match_status is ReferenceMatchStatus.MISSING_REFERENCE
            for row in rows
        )
        st.write(f"偵測到 {len(rows)} 個引用配對")
        if missing_count:
            st.warning(f"其中 {missing_count} 筆在 ref 清單中找不到對應條目。")
        result_mode = st.session_state.result_mode
        if result_mode == "mock":
            st.warning(
                "目前方法 A 顯示的是 MOCK 摘要與 Jev 結果，只供串接測試；"
                "方法 B 在開發測試模式保持空白。"
            )
        elif result_mode == "missing_key":
            st.warning("尚未設定 OPENROUTER_API_KEY，目前只顯示引用配對。")
        elif result_mode == "jev":
            st.info("方法 A 使用 Jev，方法 B 使用一般 LLM 直接判讀貼上的書目。")
        if shared_claims:
            st.subheader("方法 A 共引子主張涵蓋")
            for group in shared_claims:
                st.text(group.original_claim)
                st.write(
                    f"摘要支持 {group.supported}/{group.total} 項子主張"
                    f" · 待查 {group.pending} 項"
                    f" · 無法查核 {group.unverifiable} 項"
                )
            st.caption(
                "下表每列列出一篇候選文獻對一項子主張的判斷。涵蓋數以摘要支持為準。"
                "「無法查核」是候選文獻全都拿不到內容，不是比對後不支持；"
                "原因看各列的「來源狀態」與「摘要查詢紀錄」。"
            )
        st.markdown(
            "**證據層級：** "
            "<span style='background:#E8F5E9;color:#1B5E20;padding:3px 10px;"
            "border-radius:12px;font-weight:600'>支持</span> "
            "<span style='background:#FFF3E0;color:#A65300;padding:3px 10px;"
            "border-radius:12px;font-weight:600'>不確定</span> "
            "<span style='background:#FDECEC;color:#B42318;padding:3px 10px;"
            "border-radius:12px;font-weight:600'>不支持</span> "
            "<span style='background:#F2F4F7;color:#475467;padding:3px 10px;"
            "border-radius:12px;font-weight:600'>無法查核</span>",
            unsafe_allow_html=True,
        )
        frame = build_output_dataframe(rows)
        has_subclaims = frame["查核子主張"].ne("").any()
        table_columns = frame.columns.tolist()
        if has_subclaims:
            table_columns.remove("查核子主張")
            table_columns.insert(2, "查核子主張")
        comparison_columns = [
            "方法 A 證據解讀", "方法 A 摘要節錄",
            "方法 B 支持分數", "方法 B 證據層級", "方法 B 判斷依據", "方法 B 判斷狀態",
        ]
        for column in comparison_columns:
            table_columns.remove(column)
        comparison_start = table_columns.index("方法 A 證據層級") + 1
        table_columns[comparison_start:comparison_start] = comparison_columns
        st.dataframe(
            style_output_dataframe(frame),
            hide_index=True,
            width="stretch",
            column_order=table_columns,
            column_config={
                "引用陳述": st.column_config.TextColumn(width="large"),
                "查核子主張": st.column_config.TextColumn(width="medium") if has_subclaims else None,
                "共引群組": None,
                "共引摘要涵蓋": None,
                "共引待查數": None,
                "被引文獻": st.column_config.TextColumn(width="large"),
                "摘要查詢紀錄": st.column_config.TextColumn(width="medium"),
                "摘要來源網址": st.column_config.LinkColumn(),
                "摘要 HTTP 請求紀錄": None,
                "方法 A 支持分數": st.column_config.NumberColumn(format="plain"),
                "方法 B 支持分數": st.column_config.NumberColumn(format="plain"),
                "方法 A 查核紀錄": None,
                "方法 A 證據解讀": st.column_config.TextColumn(width="medium"),
                "方法 A 摘要節錄": st.column_config.TextColumn(
                    width="large", help="Jev 從原摘要選出的相關節錄，可用來核對原句的一部分。整句分級仍由 Q2 決定。",
                ),
                "方法 B 判斷狀態": st.column_config.TextColumn(width="medium"),
                "方法 B 查核紀錄": None,
            },
        )
        st.caption(
            "支持 ≥ 0.70 · 不確定 ≥ 0.40 且 < 0.70 · 不支持 < 0.40。"
            "方法 A 篇名不相關時直接列為不支持。"
            "全文與摘要都拿不到時改用篇名評分，「判斷依據」會填「篇名」——"
            "這種列只看過篇名、沒看過被引文獻內容，統計時要分開看。"
            "拿不到的原因看「來源狀態」，細節在「錯誤狀態」或「摘要查詢紀錄」。"
            "低分可能來自證據不足，判斷範圍見「方法 A 證據解讀」。"
        )
        st.download_button(
            "匯出 CSV",
            data=dataframe_to_csv_bytes(frame),
            file_name="citation_pairing_results.csv",
            mime="text/csv",
            type="primary",
            help="下載 A、B 的逐筆結果與查核紀錄。",
        )



