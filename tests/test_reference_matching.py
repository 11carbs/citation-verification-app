"""整條書目丟 Crossref 比對 ＋ 獨立驗證。

這一層的重點不是提高命中率，是**不要給錯的篇名**。錯的篇名會讓系統去取到
別篇論文的全文，產生假的「引用與來源不符」。所以多數測試在守「什麼時候要拒絕」。
"""
from __future__ import annotations

import pytest

from src.reference_matching import (
    CROSSREF_MIN_SCORE,
    MatchStatus,
    match_reference,
)
from src.reference_metadata import extract_reference_metadata


RAW = (
    "Ouyang, L., Wu, J., Jiang, X., Almeida, D., Wainwright, C. (2022). "
    "Training language models to follow instructions with human feedback. "
    "Advances in Neural Information Processing Systems, 35."
)
REAL_TITLE = "Training Language Models to Follow Instructions with Human Feedback"


class FakeHttp:
    """只回一筆候選。``calls`` 留著檢查有沒有多送請求。"""

    def __init__(self, item: dict | None = None, *, status: int = 200,
                 error: str = "", payload: object = None):
        self.item, self.status, self.error = item, status, error
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params or {}))
        if self.error or self.status != 200:
            return self.status, None, self.error
        if self.payload is not None:
            return 200, self.payload, ""
        items = [self.item] if self.item else []
        return 200, {"message": {"items": items}}, ""


def candidate(*, title=REAL_TITLE, score=150.0, year=2022,
              families=("Ouyang", "Wu", "Jiang"), doi="10.5555/neurips.2022",
              type="journal-article"):
    item = {"title": [title] if title else [], "score": score, "DOI": doi,
            "author": [{"family": f} for f in families]}
    if year is not None:
        item["issued"] = {"date-parts": [[year]]}
    if type is not None:
        item["type"] = type
    return item


def decide(raw=RAW, **kwargs):
    http = FakeHttp(candidate(**kwargs))
    return match_reference(raw, extract_reference_metadata(raw), http), http


# ---------- 採用 ----------

def test_a_strong_score_is_confirmed() -> None:
    decision, http = decide()
    assert decision.status is MatchStatus.CONFIRMED
    assert decision.title == REAL_TITLE
    assert decision.doi == "10.5555/neurips.2022"
    assert len(http.calls) == 1, "一筆書目只該送一次請求"
    url, params = http.calls[0]
    assert params["query.bibliographic"].startswith("Ouyang, L.")
    assert params["rows"] == 1


def test_any_accepted_score_still_needs_the_first_author_to_match() -> None:
    """分數再高也要作者相符。

    2026-11-27 的 45 筆小樣本看起來「>=120 全對、可以免核對」，200 筆重測是 93%，
    不成立。分數是 Crossref 自己給的，拿它驗證它自己的答案不叫驗證。
    """
    for score in (CROSSREF_MIN_SCORE + 5, 150.0, 400.0):
        assert decide(score=score)[0].status is MatchStatus.CONFIRMED, score
        wrong = decide(score=score, families=("Smith", "Jones"))[0]
        assert wrong.status is MatchStatus.AUTHOR_MISMATCH, score
        assert wrong.title == ""


def test_a_one_year_difference_is_tolerated() -> None:
    """線上先行出版與正式出版常差一年。"""
    assert decide(year=2021)[0].status is MatchStatus.CONFIRMED
    assert decide(year=2023)[0].status is MatchStatus.CONFIRMED


# ---------- 拒絕 ----------

def test_a_low_score_is_refused_and_gives_no_title() -> None:
    """校準樣本裡 60 分以下沒有一筆是對的，所以低分不是「比較不確定」是「錯的」。"""
    decision, _ = decide(score=CROSSREF_MIN_SCORE - 1, title="Some Unrelated Paper")
    assert decision.status is MatchStatus.LOW_SCORE
    assert decision.title == "", "沒通過驗證就不可以給篇名"
    assert decision.candidate_title == "Some Unrelated Paper", "但要留著給人看為什麼被拒"


def test_a_wrong_year_is_refused() -> None:
    decision, _ = decide(year=2015)
    assert decision.status is MatchStatus.YEAR_MISMATCH
    assert decision.title == ""
    assert "2022" in decision.note and "2015" in decision.note


def test_a_missing_first_author_cannot_rescue_a_middling_score() -> None:
    """抽不到第一作者就沒有獨立證據，不能只憑 Crossref 的分數採用。"""
    raw = "Training language models to follow instructions. NeurIPS, 35, 2022."
    http = FakeHttp(candidate(score=CROSSREF_MIN_SCORE + 5))
    decision = match_reference(raw, extract_reference_metadata(raw), http)
    assert decision.status is MatchStatus.AUTHOR_MISMATCH
    assert decision.title == ""


def test_no_candidate_is_not_the_same_as_a_failed_query() -> None:
    """查無與查詢失敗要分開：後者重跑可能有結果，不可以當成文獻不存在。"""
    empty = match_reference(RAW, extract_reference_metadata(RAW), FakeHttp(None))
    broken = match_reference(RAW, extract_reference_metadata(RAW),
                             FakeHttp(status=500, error="boom"))
    assert empty.status is MatchStatus.NO_CANDIDATE
    assert broken.status is MatchStatus.LOOKUP_FAILED
    assert empty.status is not broken.status


def test_a_malformed_payload_is_a_failure_not_a_match() -> None:
    http = FakeHttp(payload={"message": {"items": ["not a dict"]}})
    decision = match_reference(RAW, extract_reference_metadata(RAW), http)
    assert decision.status is MatchStatus.NO_CANDIDATE
    assert decision.title == ""


def test_a_candidate_without_a_title_is_refused() -> None:
    decision, _ = decide(title="")
    assert decision.status is MatchStatus.NO_CANDIDATE
    assert decision.title == ""


def test_a_very_short_entry_is_not_sent() -> None:
    """太短的字串送出去只會拿到雜訊，而且白花一次請求。"""
    http = FakeHttp(candidate())
    decision = match_reference("ibid.", extract_reference_metadata("ibid."), http)
    assert decision.status is MatchStatus.NOT_ATTEMPTED
    assert http.calls == []


# ---------- 可追溯 ----------

def test_a_disagreement_with_the_local_parse_is_recorded() -> None:
    """兩條路給出不同篇名時要留痕跡，之後查得出是哪一邊錯。"""
    decision, _ = decide(title="A Completely Different Paper About Something Else")
    assert decision.status is MatchStatus.CONFIRMED
    assert "與本機解析的篇名不同" in decision.note
    assert "Training language models" in decision.note


def test_a_surname_written_last_name_first_is_understood() -> None:
    """書目可能寫「Abalkina, A.」也可能寫「Anna Abalkina」，姓氏位置不同。"""
    for raw in (
        "Abalkina, A. (2023). Paper mills: a novel form of publishing malpractice. Meta-Psychology, 7.",
        "Anna Abalkina. 2023. Paper mills: a novel form of publishing malpractice. Meta-Psychology, 7.",
    ):
        http = FakeHttp(candidate(score=CROSSREF_MIN_SCORE + 5, year=2023,
                                  families=("Abalkina", "Bishop")))
        decision = match_reference(raw, extract_reference_metadata(raw), http)
        assert decision.status is MatchStatus.CONFIRMED, raw


@pytest.mark.parametrize("status", list(MatchStatus))
def test_only_a_confirmed_decision_ever_carries_a_title(status) -> None:
    """守一條不變量：沒驗證過就沒有篇名。"""
    from src.reference_matching import MatchDecision

    decision = MatchDecision(status=status, title="x" if status is MatchStatus.CONFIRMED else "")
    assert decision.confirmed == (status is MatchStatus.CONFIRMED)
    assert bool(decision.title) <= decision.confirmed


def test_crossref_requests_are_paced_for_the_public_pool() -> None:
    """Crossref 公開池的「Filters and queries」是每秒 1 次、並行 1，超過回 429。

    我們沒有帶 mailto（那要送出使用者的 email），所以走的是公開池。
    共用的 HTTP client 必須替 api.crossref.org 排隊，否則整批跑下去一定被擋。
    來源：Crossref access-and-authentication 文件，2026-11-27 查。
    """
    from urllib.parse import urlsplit

    from src.reference_lookup import (
        CROSSREF_BASE,
        CROSSREF_MIN_INTERVAL,
        _CROSSREF_HOST,
        _CROSSREF_SCHEDULE,
    )

    assert _CROSSREF_HOST == urlsplit(CROSSREF_BASE).netloc
    assert CROSSREF_MIN_INTERVAL >= 1.0, "公開池每秒只能一次"
    assert _CROSSREF_SCHEDULE is not None


def test_the_email_is_not_sent_to_crossref() -> None:
    """禮貌池要帶 mailto，但那是使用者的 email，不預設送出。"""
    http = FakeHttp(candidate())
    match_reference(RAW, extract_reference_metadata(RAW), http)
    [(url, params)] = http.calls
    sent = f"{url}?{params}".casefold()
    assert "mailto" not in sent
    assert "@" not in str(params)


def test_a_vancouver_surname_written_before_the_initials_is_matched() -> None:
    """`Rumble SM` 的姓在前、縮寫在後；`Anna Abalkina` 的姓在後。頭尾都要當候選。"""
    raw = ("Rumble SM, Lacroute P, Dalca AV, Fiume M, Sidow A, Brudno M. "
           "SHRiMP: accurate mapping of short color-space reads. "
           "PLoS Comput Biol. 2009;5(5):e1000386.")
    http = FakeHttp(candidate(score=CROSSREF_MIN_SCORE + 20, year=2009,
                              families=("Rumble", "Lacroute"),
                              title="SHRiMP: Accurate Mapping of Short Color-space Reads"))
    decision = match_reference(raw, extract_reference_metadata(raw), http)
    assert decision.status is MatchStatus.CONFIRMED


def test_initials_are_not_treated_as_a_surname() -> None:
    """`SM`、`A.` 這種縮寫當候選的話幾乎一定會誤中，等於沒驗證。"""
    from src.reference_matching import _surname_candidates

    assert _surname_candidates("Rumble SM") == {"rumble"}
    assert _surname_candidates("Abalkina, A.") == {"abalkina"}
    assert _surname_candidates("Anna Abalkina") == {"anna", "abalkina"}
    assert _surname_candidates("A. B.") == set()


def test_evidence_survives_when_the_title_cannot_be_parsed() -> None:
    """篇名抽不到時仍要有作者或年份，否則這一層永遠驗證不過、等於白做。

    2026-11-27 實測：原本 60 筆 regex 失敗的書目裡，抽得到第一作者的是 0 筆。
    """
    raw = ("Okubo, K. et al. Large scale cDNA sequencing for analysis of quantitative "
           "and qualitative aspects of gene expression. Nat Genet 2, 173-179 (1992).")
    meta = extract_reference_metadata(raw)
    assert meta.first_author, "抽不到作者就沒有東西可以驗證比對結果"
    assert meta.publication_year == 1992


def test_the_score_threshold_is_the_calibrated_one() -> None:
    """門檻是量出來的，不是調出來的，改動它要有新的校準數據。

    298 筆校準（語料：218 份開放存取論文全文裡的真實書目）：
      score>=70 ＋作者＋年份 → 採用 58%、判定同一篇 97%
      score>=90 ＋作者＋年份 → 採用 53%、判定同一篇 99%   ← 採用這條
    少 5 個百分點的覆蓋率，換掉一半的錯誤。
    """
    assert CROSSREF_MIN_SCORE == 90.0


def test_a_score_between_seventy_and_ninety_is_refused() -> None:
    """70–90 這一段的分層一致率只有 57%，不可以採用。"""
    for score in (70.0, 80.0, 89.9):
        decision, _ = decide(score=score)
        assert decision.status is MatchStatus.LOW_SCORE, score
        assert decision.title == ""


# ---------- 附屬紀錄不是來源文本 ----------

def test_a_peer_review_record_is_refused_even_at_the_highest_score() -> None:
    """審稿回覆的作者、年份、篇名都跟正篇一樣，分數還可能是全場最高。

    2026-11-28 實測（SciCiteVal 24 筆）：scite 那篇拿到 216.2 分，全場最高，
    作者與年份都對，但 DOI 是 `10.1162/qss_a_00146/v2/response1`，type=peer-review。
    取全文只抓到 14 字，退回摘要，證據從 55,292 字掉到 1,354 字——比不比對還糟。
    分數與作者核對都擋不住這種候選，只有型別擋得住。
    """
    decision, _ = decide(score=216.2, type="peer-review",
                         title='Author response for "Training Language Models..."')
    assert decision.status is MatchStatus.WRONG_TYPE
    assert decision.title == ""
    assert decision.doi == "", "壞 DOI 會讓取全文抓到附屬紀錄，一個字都不可以外流"
    assert "peer-review" in decision.note


@pytest.mark.parametrize("bad", ["peer-review", "component", "journal-issue",
                                 "journal-volume", "book-series", "grant"])
def test_every_rejected_type_is_actually_refused(bad) -> None:
    assert decide(type=bad)[0].status is MatchStatus.WRONG_TYPE


@pytest.mark.parametrize("good", ["journal-article", "proceedings-article",
                                  "posted-content", "book-chapter", "dissertation"])
def test_a_real_document_type_is_still_accepted(good) -> None:
    """用排除法不是允許清單：Crossref 新增型別時寧可放行再補規則。"""
    assert decide(type=good)[0].status is MatchStatus.CONFIRMED


def test_a_candidate_with_no_type_field_is_not_refused() -> None:
    """舊的快取紀錄或 select 沒帶到 type 時，不可以整批拒掉。"""
    assert decide(type=None)[0].status is MatchStatus.CONFIRMED


def test_the_type_is_asked_for_in_the_query() -> None:
    """沒跟 Crossref 要 type，上面那些檢查就永遠拿不到值、永遠放行。"""
    _, http = decide()
    [(_url, params)] = http.calls
    assert "type" in params["select"].split(",")


# ---------- 短姓氏 ----------

def test_a_two_letter_surname_is_matched() -> None:
    """`Ho`、`Wu`、`Li`、`Ng`、`Xu` 是姓氏，不是縮寫。

    2026-11-28 實測：`Xanh Ho` 的候選 114.6 分、篇名完全正確，卻被判
    「第一作者對不上」，因為舊版用 `len(t) > 2` 排除縮寫，把兩個字母的姓氏
    一起排除了。這不是偶發錯誤，是整類東亞與東南亞姓氏都驗證不過。
    """
    raw = ("Xanh Ho, Sunisth Kumar, Yun-Ang Wu, Florian Boudin, Atsuhiro Takasu, "
           "and Akiko Aizawa. 2025. Table-text alignment: Explaining claim "
           "verification against tables in scientific papers.")
    http = FakeHttp(candidate(score=114.6, year=2025, families=("Ho", "Kumar", "Wu"),
                              title="Table-Text Alignment: Explaining Claim Verification"))
    decision = match_reference(raw, extract_reference_metadata(raw), http)
    assert decision.status is MatchStatus.CONFIRMED


def test_a_short_surname_is_not_matched_inside_a_longer_one() -> None:
    """允許兩個字母的姓氏，就一定要改用 token 比對。

    `"ho" in "thomson schneider"` 是 True。如果還用子字串比對，放寬長度限制
    等於把作者核對整個拿掉——那是這一層唯一的獨立驗證。
    """
    raw = ("Xanh Ho. 2025. Table-text alignment: Explaining claim verification "
           "against tables in scientific papers.")
    http = FakeHttp(candidate(score=150.0, year=2025,
                              families=("Thomson", "Schneider", "Wuhan")))
    decision = match_reference(raw, extract_reference_metadata(raw), http)
    assert decision.status is MatchStatus.AUTHOR_MISMATCH
    assert decision.title == ""


def test_a_compound_surname_is_split_into_tokens() -> None:
    """`van der Berg`、`da Silva` 這種複姓要拆開才對得上。"""
    from src.reference_matching import _candidate_surnames

    found = _candidate_surnames({"author": [{"family": "van der Berg"},
                                            {"family": "da Silva"}]})
    assert {"van", "der", "berg", "da", "silva"} <= found


def test_a_name_particle_is_not_an_independent_candidate() -> None:
    """`da`、`van`、`de` 單獨比對幾乎一定誤中。

    任一候選對上就算相符，所以多一個雜訊候選就等於少一道驗證。
    """
    from src.reference_matching import _surname_candidates

    assert _surname_candidates("Jaime A. Teixeira da Silva") == {
        "jaime", "teixeira", "silva"}
    assert "da" not in _surname_candidates("Jaime A. Teixeira da Silva")
    assert _surname_candidates("Ludwig van Beethoven") == {"ludwig", "beethoven"}


def test_a_surname_written_in_capitals_is_kept() -> None:
    """有些書目把姓氏整個大寫，那是姓氏不是縮寫；短的全大寫才是縮寫。"""
    from src.reference_matching import _surname_candidates

    assert _surname_candidates("ABALKINA, A.") == {"abalkina"}
    assert _surname_candidates("Rumble SM") == {"rumble"}
    assert _surname_candidates("Smith JR") == {"smith"}


def test_the_type_is_compared_without_case() -> None:
    """Crossref 目前回小寫，但不要靠對方的大小寫習慣來守規則。"""
    for written in ("Peer-Review", "PEER-REVIEW", "peer-review"):
        assert decide(type=written)[0].status is MatchStatus.WRONG_TYPE, written
