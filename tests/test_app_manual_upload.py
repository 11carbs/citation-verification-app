"""第二人：主畫面的「手動上傳被引文獻」（分工：手動上傳備援）。

這一份只驗畫面這一層——檔案有沒有被收下、配到哪一條 ref、配錯時會不會擋下來。
上傳之後怎麼轉檔、怎麼蓋過自動取得的全文，在 test_fulltext_sources.py 與
test_source_evidence.py。
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.fulltext_sources import FullTextFetcher


APP = Path(__file__).parents[1] / "app.py"
MARKDOWN = ("walters.md", b"# Paper\n\nBody of the cited work.", "text/markdown")
PDF = ("isik.pdf", b"%PDF-1.4 fake", "application/pdf")


REF_LIST = (
    '[3] W. Walters, "Fabrication and errors in citations," Sci. Rep., 2023.\n'
    '[7] M. Isik, "A large language model pipeline," Sci. Rep., 2026.'
)


def started(reference_text: str = REF_LIST) -> AppTest:
    """開好畫面並貼上參考文獻清單。

    上傳一定要配到清單裡真的存在的 ref 編號，所以每個測試都要先有清單。
    """

    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    app.text_area[1].set_value(reference_text).run()
    assert not app.exception
    return app


def test_the_page_offers_a_place_to_upload_a_cited_paper():
    app = started()

    assert len(app.file_uploader) == 1
    assert app.file_uploader[0].label == "被引文獻檔案"


def test_every_accepted_extension_is_one_the_converter_can_actually_read():
    app = started()
    fetcher = FullTextFetcher()

    for suffix in app.file_uploader[0].allowed_type:
        result = fetcher.fetch_upload(f"paper{suffix}", b"")
        # 空檔案當然轉不出東西，但不可以是「不支援的副檔名」——那表示畫面收了
        # 一個轉檔層根本不認得的格式，使用者會一直傳一個必定失敗的檔案
        assert "不支援的副檔名" not in result.note, suffix


def test_an_uploaded_file_is_not_used_until_it_is_given_a_ref_number():
    app = started()

    app.file_uploader[0].set_value([MARKDOWN]).run()

    assert not app.exception
    assert app.session_state["manual_upload_plan"] == {}
    assert len(app.number_input) == 1  # 每個檔案配一個編號輸入框
    # 還沒指定編號不是錯誤，不可以跳「ref [0] 不在參考文獻清單裡」這種訊息
    assert not app.error


def test_giving_a_ref_number_puts_the_file_into_the_plan():
    app = started()
    app.file_uploader[0].set_value([MARKDOWN]).run()

    app.number_input[0].set_value(3).run()

    plan = app.session_state["manual_upload_plan"]
    assert set(plan) == {3}
    assert plan[3].name == "walters.md"
    assert plan[3].data == MARKDOWN[1]
    # 畫面要寫出這個檔案會被當成「哪一篇文獻」，不是只印編號
    shown = [caption.value for caption in app.caption]
    assert any("[3] walters.md" in line and "Fabrication and errors" in line for line in shown)


def test_two_files_can_cover_two_different_refs():
    app = started()
    app.file_uploader[0].set_value([MARKDOWN, PDF]).run()

    app.number_input[0].set_value(3).run()
    app.number_input[1].set_value(7).run()

    plan = app.session_state["manual_upload_plan"]
    assert {number: upload.name for number, upload in plan.items()} == {
        3: "walters.md", 7: "isik.pdf",
    }


def test_the_same_ref_number_on_two_files_uses_neither_of_them():
    app = started()
    app.file_uploader[0].set_value([MARKDOWN, PDF]).run()

    app.number_input[0].set_value(3).run()
    app.number_input[1].set_value(3).run()

    # 猜哪一個該贏就是拿別篇論文的全文去比對，那會是假的「引用與來源不符」
    assert app.session_state["manual_upload_plan"] == {}
    assert any("一個以上的檔案" in message.value for message in app.error)


def test_each_ref_number_box_belongs_to_one_file_not_to_a_position():
    """編號要綁在檔案身上，不可以綁在清單順序上。

    綁順序的話，使用者從清單中間移掉一個檔案，後面每個檔案的編號都會往前錯一格，
    等於把別篇論文的全文指給這一條 ref。``AppTest`` 每次 ``set_value`` 都會重發
    ``file_id``，模擬不了「移掉其中一個」，所以這裡驗的是鍵本身怎麼來的。
    """

    app = started()
    app.file_uploader[0].set_value([MARKDOWN, PDF]).run()

    file_ids = [item.file_id for item in app.file_uploader[0].value]
    keys = {box.key for box in app.number_input}

    assert len(file_ids) == 2
    assert keys == {f"manual_ref_{file_id}" for file_id in file_ids}


def test_turning_off_the_evidence_layer_warns_that_uploads_will_be_ignored():
    app = started()
    app.file_uploader[0].set_value([MARKDOWN]).run()
    app.number_input[0].set_value(3).run()

    app.toggle(key="use_evidence_layer").set_value(False).run()

    assert any("不會被使用" in message.value for message in app.warning)


def test_the_evidence_layer_is_on_by_default():
    app = started()

    assert app.session_state["use_evidence_layer"] is True


def test_the_mock_run_still_works_and_is_untouched_by_the_upload_box():
    app = started()

    app.button[1].click().run()  # 「開始比對」

    assert not app.exception
    assert app.session_state["result_mode"] == "mock"
    assert app.session_state["analysis_rows"]
    assert len(app.dataframe) == 1


def test_clearing_the_form_also_drops_the_uploaded_files():
    """「清除」代表要換一篇論文，別篇論文的 ref [3] 是另一份文獻。

    留著上一篇的檔案，等於把上一篇的全文指給這一篇的 [3]，判斷結果會是
    假的「引用與來源不符」。
    """

    app = started()
    app.file_uploader[0].set_value([MARKDOWN]).run()
    app.number_input[0].set_value(3).run()
    assert app.session_state["manual_upload_plan"]

    app.button[0].click().run()  # 「清除」

    assert not app.exception
    assert app.session_state["manual_upload_plan"] == {}
    assert app.file_uploader[0].value == []
    assert len(app.number_input) == 0


# -------------------------------------- 端到端：上傳的內容真的變成判斷用的證據


CLAIM = "The method reduces treatment time [1]."
REF = '[1] A. Author, "A study of treatment time," J. Care, 2026.'
# 要長過 MIN_FULL_TEXT_CHARS（1000 字），否則會被當成付費牆那類的空殼頁擋掉
UPLOADED_BODY = (
    "# A study of treatment time\n\n"
    "## Results\n\n"
    "The proposed method reduces treatment time by 40% across the trial cohort.\n\n"
    + "We report the full protocol, the cohort characteristics and the analysis plan. " * 20
)
UPLOADED_PAPER = ("uploaded.md", UPLOADED_BODY.encode("utf-8"), "text/markdown")


class JevStub:
    """接住送往 Jev 的每一次請求，不連網。"""

    def __init__(self, monkeypatch):
        self.states: list[dict] = []
        self.questions: list[str] = []
        self.answers = {
            "title_relevance": {
                "type": "choice", "choice": "related", "confidence": 0.6,
                "probabilities": {"related": 0.8, "uncertain": 0.1, "unrelated": 0.1},
            },
            # 分數跟著證據走。固定回 0.82 的話，「不會變成不支持」這種斷言是
            # stub 保證的，不是被測程式保證的——垃圾證據被當成全文收下也會通過。
            "citation_support": None,
            "evidence_excerpt": {"type": "choice", "choice": "none"},
        }
        monkeypatch.setattr("src.jev_adapter.urlopen", self.open)
        monkeypatch.setattr("src.method_b.urlopen", self.open_method_b)

    def open(self, request, *, timeout):
        import json
        from io import BytesIO
        payload = json.loads(request.data)
        [(question_id, _)] = payload["questions"].items()
        self.questions.append(question_id)
        self.states.append(payload["state"])
        answer = self.answers[question_id]
        if question_id == "citation_support":
            evidence = payload["state"].get("evidence", "")
            # 證據裡真的講到主張的內容才給高分，否則低分
            answer = {"type": "noul",
                      "noul": 0.82 if "reduces treatment time" in evidence else 0.12}
        body = {"model": payload["model"], "id": f"call-{len(self.states)}",
                "answers": {question_id: answer}}
        return BytesIO(json.dumps(body).encode("utf-8"))

    def open_method_b(self, request, *, timeout):
        import json
        from io import BytesIO
        result = {"results": [{"pair_id": "pair_001", "support_score": 0.5}]}
        body = {"choices": [{"message": {"content": json.dumps(result)}}]}
        return BytesIO(json.dumps(body).encode("utf-8"))


def test_the_uploaded_file_is_what_jev_actually_judges_against(monkeypatch):
    stub = JevStub(monkeypatch)
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.secrets["OPENROUTER_API_KEY"] = "test-placeholder"
    app.run()
    app.toggle[0].set_value(False)            # 關掉 mock，走真的流程
    app.file_uploader[0].set_value([UPLOADED_PAPER]).run()
    app.number_input[0].set_value(1).run()
    app.text_area[0].set_value(CLAIM)
    app.text_area[1].set_value(REF)

    app.button[1].click().run()

    assert not app.exception
    [support] = [state for question, state in zip(stub.questions, stub.states)
                 if question == "citation_support"]
    # 送進判斷的就是上傳檔案裡的句子，不是書目、不是摘要
    assert "reduces treatment time by 40%" in support["evidence"]
    assert support["evidence_type"] == "全文"
    [row] = app.session_state["analysis_rows"]
    assert row.method_a.evidence_basis.value == "全文"
    assert row.method_a.support_score == 0.82


def test_without_an_upload_the_same_reference_has_no_full_text_to_judge(monkeypatch):
    """對照組：拿掉上傳，這一筆就沒有全文可用（這份書目是假的，查不到）。

    兩者唯一的差別只有「有沒有上傳」，所以上面那個測試證明的是上傳生效，
    不是流程剛好會通。
    """

    stub = JevStub(monkeypatch)
    monkeypatch.setattr("src.fulltext_sources.FullTextFetcher.fetch",
                        lambda self, meta: _no_full_text())
    monkeypatch.setattr("src.reference_lookup.ReferenceLookupAdapter.lookup",
                        lambda self, pair: _not_found(pair))
    # 這條書目沒有 DOI，沒攔住的話取證層會真的去打 Crossref：測試不可以連外網，
    # 而且那會動到 Crossref 節流的模組層狀態，害後面的測試誤判
    monkeypatch.setattr("src.source_evidence.match_reference",
                        lambda raw, meta, http, **kwargs: _no_match())
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.secrets["OPENROUTER_API_KEY"] = "test-placeholder"
    app.run()
    app.toggle[0].set_value(False)
    app.text_area[0].set_value(CLAIM)
    app.text_area[1].set_value(REF)

    app.button[1].click().run()

    assert not app.exception
    supports = [state for question, state in zip(stub.questions, stub.states)
                if question == "citation_support"]
    assert supports, "沒有問到 Q2 的話，下面那句 all(...) 會空通過"
    assert all("reduces treatment time by 40%" not in state["evidence"] for state in supports)
    [row] = app.session_state["analysis_rows"]
    assert row.method_a.evidence_basis.value != "全文"


def _no_full_text():
    from src.fulltext_sources import FullTextResult, FullTextStatus
    return FullTextResult(status=FullTextStatus.NO_OPEN_ACCESS, note="測試：沒有開放存取位置")


def _not_found(pair):
    from src.adapters import ReferenceLookupResult
    from src.schemas import SourceStatus
    return ReferenceLookupResult(ref_number=pair.ref_number, title="A study of treatment time",
                                 source_status=SourceStatus.NOT_FOUND,
                                 source_note="測試：查無此文獻")


def _no_match():
    from src.reference_matching import MatchDecision, MatchStatus
    return MatchDecision(status=MatchStatus.NO_CANDIDATE, note="測試：不比對")


def test_the_results_table_shows_that_this_row_used_an_uploaded_file(monkeypatch):
    """評估時必須分得出哪幾列的證據是人給的、哪幾列是系統自己找到的。

    這兩件事混在一起，就等於拿「使用者提供的全文」去跟「系統自己抓的全文」
    一起算系統效能。
    """

    JevStub(monkeypatch)
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.secrets["OPENROUTER_API_KEY"] = "test-placeholder"
    app.run()
    app.toggle[0].set_value(False)
    app.file_uploader[0].set_value([UPLOADED_PAPER]).run()
    app.number_input[0].set_value(1).run()
    app.text_area[0].set_value(CLAIM)
    app.text_area[1].set_value(REF)

    app.button[1].click().run()

    assert not app.exception
    [record] = app.dataframe[0].value.to_dict("records")
    assert "手動上傳" in record["摘要查詢紀錄"]
    assert "uploaded.md" in record["摘要查詢紀錄"]
    assert record["方法 A 判斷依據"] == "全文"
    # ⚠️ 共用輸出格式裡「方法 A 摘要來源」「摘要來源網址」指的都是**摘要**的來源，
    # 不把全文網址或上傳的檔名塞進去，否則欄位含義會變掉（而且「摘要來源網址」是
    # LinkColumn，填檔名會是點不開的死連結）。證據的來源寫在「摘要查詢紀錄」。
    assert record["方法 A 摘要來源"] == ""
    assert record["摘要來源網址"] == ""


def test_a_rejected_upload_says_why_in_the_results_table(monkeypatch):
    """付費牆頁面被擋下來之後，使用者要看得到原因，否則會一直重傳同一個檔案。"""

    JevStub(monkeypatch)
    monkeypatch.setattr("src.fulltext_sources.FullTextFetcher.fetch",
                        lambda self, meta: _no_full_text())
    monkeypatch.setattr("src.reference_lookup.ReferenceLookupAdapter.lookup",
                        lambda self, pair: _not_found(pair))
    monkeypatch.setattr("src.source_evidence.match_reference",
                        lambda raw, meta, http, **kwargs: _no_match())
    paywall = ("saved_page.html",
               b"<html><body><h1>A study of treatment time</h1>"
               b"<p>Please purchase access to read this article.</p></body></html>",
               "text/html")
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.secrets["OPENROUTER_API_KEY"] = "test-placeholder"
    app.run()
    app.toggle[0].set_value(False)
    app.file_uploader[0].set_value([paywall]).run()
    app.number_input[0].set_value(1).run()
    app.text_area[0].set_value(CLAIM)
    app.text_area[1].set_value(REF)

    app.button[1].click().run()

    assert not app.exception
    [record] = app.dataframe[0].value.to_dict("records")
    trail = f"{record['摘要查詢紀錄']} {record['錯誤狀態']}"
    assert "手動上傳（saved_page.html）沒能用" in trail
    assert "短到不像全文" in trail
    # 要守的是「那個檔案沒有變成證據」：判斷依據不可以是全文。
    # 這一列最後是「篇名」，因為自動那條路也拿不到東西，於是走既有的用篇名評分
    # （組內 2026-11-27 的決定）。那條路本來就可能給出不支持，不是這個上傳造成的，
    # 統計時本來就要用「判斷依據＝篇名」把這些列篩掉。
    assert record["方法 A 判斷依據"] == "篇名"
    assert record["方法 A 判斷依據"] != "全文"


# ---------------------------------- 上傳必須綁到「哪一篇文獻」，不是只綁編號


OTHER_REF_LIST = (
    '[3] M. Isik, "A large language model pipeline for citation scoring," Sci. Rep., 2026.\n'
    '[7] W. Walters, "Fabrication and errors in citations," Sci. Rep., 2023.'
)


def test_changing_the_reference_list_stops_the_old_file_being_used():
    """換一篇論文卻沒按「清除」，是這個功能最危險的用法。

    ref [3] 還在，但已經是另一份文獻了。沒擋的話，系統會拿上一篇的全文去比對
    這一篇的 [3]，而且判斷依據會寫「全文」、看起來有憑有據，結果是假的
    「引用與來源不符」。
    """

    app = started()
    app.file_uploader[0].set_value([MARKDOWN]).run()
    app.number_input[0].set_value(3).run()
    assert set(app.session_state["manual_upload_plan"]) == {3}

    app.text_area[1].set_value(OTHER_REF_LIST).run()   # 只換參考文獻，不按「清除」

    assert app.session_state["manual_upload_plan"] == {}
    assert any("書目換了內容" in message.value for message in app.error)

    # ⚠️ 只斷言到上面為止是不夠的：拒絕必須**撐過下一個 rerun**，因為「按下開始比對」
    # 本身就是下一個 rerun。第一版的拒絕只活一個 rerun，所以實際分析照跑。
    for _ in range(2):
        app.run()
        assert app.session_state["manual_upload_plan"] == {}
        assert any("書目換了內容" in message.value for message in app.error)

    app.button[1].click().run()
    assert not app.exception
    assert app.session_state["manual_upload_plan"] == {}


def test_moving_a_file_to_another_ref_number_is_not_treated_as_a_changed_reference():
    """使用者自己改編號是刻意指定，不是「書目換了內容」。

    綁定如果只記書目不記編號，改編號會跳出一句講錯的錯誤訊息。
    """

    app = started()
    app.file_uploader[0].set_value([MARKDOWN]).run()
    app.number_input[0].set_value(3).run()

    app.number_input[0].set_value(7).run()

    assert set(app.session_state["manual_upload_plan"]) == {7}
    assert not any("書目換了內容" in message.value for message in app.error)


def test_re_uploading_the_file_clears_a_rejection():
    """重新上傳是使用者明確表示「這個檔案對應的就是現在這一篇」的逃生門。"""

    app = started()
    app.file_uploader[0].set_value([MARKDOWN]).run()
    app.number_input[0].set_value(3).run()
    app.text_area[1].set_value(OTHER_REF_LIST).run()
    assert app.session_state["manual_upload_plan"] == {}

    app.file_uploader[0].set_value([MARKDOWN]).run()   # 新的 file_id，編號歸 0
    assert app.session_state["manual_upload_plan"] == {}
    app.number_input[0].set_value(3).run()

    assert set(app.session_state["manual_upload_plan"]) == {3}


def test_a_ref_number_that_is_not_in_the_list_is_refused_out_loud():
    """編號打錯時，原本畫面會寫「比對時會使用」，實際上整份檔案不會被用到。"""

    app = started()
    app.file_uploader[0].set_value([MARKDOWN]).run()

    app.number_input[0].set_value(99).run()

    assert app.session_state["manual_upload_plan"] == {}
    assert any("不在目前的參考文獻清單裡" in message.value for message in app.error)


def test_the_file_is_shown_next_to_the_reference_it_will_stand_in_for():
    app = started()
    app.file_uploader[0].set_value([MARKDOWN]).run()

    app.number_input[0].set_value(7).run()

    shown = " ".join(caption.value for caption in app.caption)
    assert "[7] walters.md" in shown
    assert "A large language model pipeline" in shown   # [7] 現在指的是這一篇


def test_the_plan_is_not_advertised_while_the_evidence_layer_is_off():
    """開關關掉時不可以又說「不會被使用」又說「比對時會使用」。"""

    app = started()
    app.file_uploader[0].set_value([MARKDOWN]).run()
    app.number_input[0].set_value(3).run()

    app.toggle(key="use_evidence_layer").set_value(False).run()

    assert any("不會被使用" in message.value for message in app.warning)
    assert not any("比對時會使用" in message.value for message in app.success)


def test_a_rejected_upload_is_still_explained_when_the_abstract_route_works(monkeypatch):
    """「退摘要」是最常見的 fallback。

    只在「摘要查詢紀錄」空的時候才補原因的話，這條路上查詢層已經寫了自己的紀錄，
    上傳失敗的原因就永遠寫不進去，使用者會一直重傳同一個轉不了的檔案。
    """

    JevStub(monkeypatch)
    monkeypatch.setattr("src.fulltext_sources.FullTextFetcher.fetch",
                        lambda self, meta: _no_full_text())
    monkeypatch.setattr("src.reference_lookup.ReferenceLookupAdapter.lookup",
                        lambda self, pair: _found_abstract(pair))
    monkeypatch.setattr("src.source_evidence.match_reference",
                        lambda raw, meta, http, **kwargs: _no_match())
    paywall = ("saved_page.html",
               b"<html><body><p>Please purchase access.</p></body></html>", "text/html")
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.secrets["OPENROUTER_API_KEY"] = "test-placeholder"
    app.run()
    app.toggle[0].set_value(False)
    app.file_uploader[0].set_value([paywall]).run()
    app.number_input[0].set_value(1).run()
    app.text_area[0].set_value(CLAIM)
    app.text_area[1].set_value(REF)

    app.button[1].click().run()

    assert not app.exception
    [record] = app.dataframe[0].value.to_dict("records")
    assert record["方法 A 判斷依據"] == "摘要"          # 退到摘要了
    assert "手動上傳（saved_page.html）沒能用" in record["摘要查詢紀錄"]
    assert "Semantic Scholar" in record["摘要查詢紀錄"]  # 查詢層自己的紀錄也還在


def _found_abstract(pair):
    from src.adapters import ReferenceLookupResult
    from src.schemas import SourceStatus
    return ReferenceLookupResult(
        ref_number=pair.ref_number, title="A study of treatment time",
        abstract="The proposed method reduces treatment time in a small cohort.",
        source_status=SourceStatus.GOT_ABSTRACT, abstract_source="semantic_scholar",
        abstract_url="https://example.org/abstract",
        source_note="Semantic Scholar 用篇名找到")
