"""判斷端改吃第二人備好的證據（現行架構第 5 點：同一批引用、同一份證據）。

這裡守的是：取證層的失敗不可以變成判斷結果，而且 Q1 已經不再決定要不要取證。
"""
from __future__ import annotations

from io import BytesIO
import json
from unittest.mock import Mock

import pytest

from src.adapters import JudgmentStatus, ReferenceLookupResult
import src.jev_adapter as jev_module
from src.jev_adapter import OpenRouterJevAdapter
from src.pipeline import run_method_a_pipeline
from src.schemas import CitationPair, EvidenceBasis, SourceStatus, SupportBand
from src.source_evidence import SourceEvidence


PAIR = CitationPair(
    "pair_001",
    "The method reduces treatment time [1].",
    "[1]",
    1,
    '[1] A. Author, "A study of treatment time," 2026.',
)
FULL_TEXT = (
    "Introduction. The study benchmarked a new method for treatment planning. "
    "Results show the method reduces treatment time by thirty percent in the trial cohort. "
    "Discussion. Further work is needed on generalisation."
)


class Stub:
    """依 question id 回答，順序不會錯位。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, score: float = 0.80):
        self.states: list[dict] = []
        self.events: list[str] = []
        self.score = score
        monkeypatch.setattr(jev_module, "urlopen", self.open)

    def open(self, request, *, timeout):
        payload = json.loads(request.data)
        [(qid, _)] = payload["questions"].items()
        self.events.append(qid)
        self.states.append(payload["state"])
        if qid == "title_relevance":
            probabilities = dict.fromkeys(("related", "uncertain", "unrelated"), 0.1)
            probabilities["related"] = 0.8
            answer = {"type": "choice", "choice": "related",
                      "probabilities": probabilities, "confidence": 0.6}
        else:
            answer = {"type": "noul", "noul": self.score}
        body = {"model": "m", "id": f"call-{len(self.states)}", "answers": {qid: answer}}
        return BytesIO(json.dumps(body).encode("utf-8"))


def resolver_returning(evidence: SourceEvidence) -> Mock:
    resolver = Mock()
    resolver.resolve.return_value = evidence
    return resolver


def full_text_evidence(**kwargs) -> SourceEvidence:
    defaults = dict(
        ref_number=1, text=FULL_TEXT, basis=EvidenceBasis.FULL_TEXT,
        source="arxiv_html", converter="trafilatura",
        reference=ReferenceLookupResult(1, "A study of treatment time"),
    )
    return SourceEvidence(**{**defaults, **kwargs})


def test_full_text_evidence_reaches_jev_and_is_labelled_as_full_text(monkeypatch):
    """全文要真的送到 Jev，而且標成「全文」不是「摘要」。"""
    stub = Stub(monkeypatch)
    resolver = resolver_returning(full_text_evidence())

    [row] = run_method_a_pipeline([PAIR], None, OpenRouterJevAdapter("test-placeholder"),
                                  evidence_resolver=resolver)

    assert stub.events == ["title_relevance", "citation_support"]
    assert stub.states[1]["evidence_type"] == "全文"
    assert "reduces treatment time" in stub.states[1]["evidence"]
    assert row.method_a.evidence_basis is EvidenceBasis.FULL_TEXT
    assert row.method_a.band is SupportBand.SUPPORTED
    assert row.to_record()["方法 A 判斷依據"] == "全文"


def test_only_the_retrieved_passage_is_sent_not_the_whole_document(monkeypatch):
    """送進去的是檢索出來的句子，不是整篇。整篇又貴又稀釋重點。"""
    stub = Stub(monkeypatch)
    padding = " ".join(f"Unrelated background sentence number {n}." for n in range(200))
    resolver = resolver_returning(full_text_evidence(text=FULL_TEXT + " " + padding))

    run_method_a_pipeline([PAIR], None, OpenRouterJevAdapter("test-placeholder"),
                          evidence_resolver=resolver)

    sent = stub.states[1]["evidence"]
    assert "reduces treatment time" in sent
    assert len(sent) < 2000, "整篇被原封不動送進去了"


def test_an_abstract_fallback_is_labelled_as_an_abstract(monkeypatch):
    """退回摘要時標「摘要」。摘要上的弱相符與全文上的意義不同，不可以混。"""
    stub = Stub(monkeypatch)
    resolver = resolver_returning(full_text_evidence(
        text="The study reports that the method reduces treatment time.",
        basis=EvidenceBasis.ABSTRACT, source="semantic_scholar", converter="",
    ))

    [row] = run_method_a_pipeline([PAIR], None, OpenRouterJevAdapter("test-placeholder"),
                                  evidence_resolver=resolver)

    assert stub.states[1]["evidence_type"] == "摘要"
    assert row.method_a.evidence_basis is EvidenceBasis.ABSTRACT


def test_no_evidence_falls_back_to_the_title_and_says_so(monkeypatch):
    """全文與摘要都拿不到時用篇名評分，並且說清楚這一列是怎麼來的。

    陳祖旭 2026-11-27 的做法，LJH 採用。⚠️ 與「只記錄該項目無法查核」這條規則不一致，
    所以輸出必須留下可以篩掉這些列的標記，否則評估的「不支持」會混進
    系統自己抓不到的文獻。
    """
    stub = Stub(monkeypatch, score=0.55)
    resolver = resolver_returning(full_text_evidence(
        text="", basis=EvidenceBasis.UNAVAILABLE, source="", converter="",
        note="沒有開放存取全文；摘要查不到",
    ))

    [row] = run_method_a_pipeline([PAIR], None, OpenRouterJevAdapter("test-placeholder"),
                                  evidence_resolver=resolver)

    assert stub.states[1]["evidence_type"] == "篇名"
    assert stub.states[1]["evidence"] == "A study of treatment time"
    record = row.to_record()
    assert record["方法 A 判斷依據"] == "篇名", "評估要靠這一欄把這些列篩掉"
    assert record["方法 A 證據解讀"] == "僅依篇名判斷，未取得被引文獻內容"
    assert record["錯誤狀態"] == "沒有開放存取全文；摘要查不到", "取不到的原因不可以丟掉"


def test_a_resolver_crash_is_not_a_judgment(monkeypatch):
    """取證層自己壞掉，跟「這篇沒有全文」是兩回事，不可以變成判斷結果。"""
    Stub(monkeypatch)
    resolver = Mock()
    resolver.resolve.side_effect = RuntimeError("boom")

    [row] = run_method_a_pipeline([PAIR], None, OpenRouterJevAdapter("test-placeholder"),
                                  evidence_resolver=resolver)

    assert row.judgment.status is JudgmentStatus.REFERENCE_ERROR
    assert row.method_a.support_score is None
    assert row.method_a.band is not SupportBand.UNSUPPORTED
    assert "RuntimeError" in row.to_record()["錯誤狀態"]


def test_evidence_for_the_wrong_reference_is_refused(monkeypatch):
    """取證層交回別筆 ref 的結果，拿去比對會產生假的「引用與來源不符」。"""
    Stub(monkeypatch)
    resolver = resolver_returning(full_text_evidence(
        reference=ReferenceLookupResult(2, "Another paper entirely"),
    ))

    [row] = run_method_a_pipeline([PAIR], None, OpenRouterJevAdapter("test-placeholder"),
                                  evidence_resolver=resolver)

    assert row.judgment.status is JudgmentStatus.REFERENCE_ERROR
    assert row.method_a.support_score is None


def test_the_same_reference_is_resolved_once_for_the_whole_batch(monkeypatch):
    """同一筆書目只取證一次。兩條判斷路徑要拿到同一份證據才能比較。"""
    Stub(monkeypatch)
    resolver = resolver_returning(full_text_evidence())
    second = CitationPair("pair_002", "It also lowers cost [1].", "[1]", 1, PAIR.ref_raw)

    run_method_a_pipeline([PAIR, second], None, OpenRouterJevAdapter("test-placeholder"),
                          evidence_resolver=resolver)

    # resolve() 自己有快取，pipeline 不可以繞過它另外查一次
    assert resolver.resolve.call_count == 2
    assert [call.args[0].pair_id for call in resolver.resolve.call_args_list] == [
        "pair_001", "pair_002",
    ]


def test_the_old_path_still_works_when_no_resolver_is_given(monkeypatch):
    """不傳 resolver 時維持原本的查摘要流程，既有呼叫端不受影響。"""
    stub = Stub(monkeypatch)
    reference_adapter = Mock()
    reference_adapter.lookup.side_effect = lambda pair: ReferenceLookupResult(
        1, "A study of treatment time", abstract="The method reduces treatment time.",
        source_status=SourceStatus.GOT_ABSTRACT, abstract_source="semantic_scholar",
    )

    [row] = run_method_a_pipeline([PAIR], reference_adapter,
                                  OpenRouterJevAdapter("test-placeholder"))

    reference_adapter.lookup.assert_called_once()
    assert stub.states[1]["evidence_type"] == "摘要"
    assert row.method_a.band is SupportBand.SUPPORTED


# --- 2026-11-27 第三人的建議：篇名保留，取得全文或摘要時也一起送給 Jev ---


def test_the_source_title_is_sent_together_with_the_evidence(monkeypatch):
    """有全文時篇名也一起送，讓模型確認這份內容是哪一篇的。"""
    stub = Stub(monkeypatch)
    resolver = resolver_returning(full_text_evidence())

    [row] = run_method_a_pipeline([PAIR], None, OpenRouterJevAdapter("test-placeholder"),
                                  evidence_resolver=resolver)

    state = stub.states[1]
    assert state["verified_source_title"] == "A study of treatment time"
    assert state["evidence_type"] == "全文"
    assert state["evidence"] != state["verified_source_title"], "篇名不可以變成證據本身"
    # 查核紀錄要留下當時送出的篇名
    assert row.judgment.support_source_title == "A study of treatment time"


def test_the_title_is_marked_as_not_evidence_in_the_question(monkeypatch):
    """送篇名就必須同時送「篇名不可當證據」的指示，否則等於把看篇名猜偷渡回來。"""
    sent: list[dict] = []
    original = jev_module.OpenRouterJevAdapter._ask

    def spy(self, state, question_id, question):
        sent.append({"id": question_id, "question": question})
        return original(self, state, question_id, question)

    monkeypatch.setattr(jev_module.OpenRouterJevAdapter, "_ask", spy)
    Stub(monkeypatch)
    run_method_a_pipeline([PAIR], None, OpenRouterJevAdapter("test-placeholder"),
                          evidence_resolver=resolver_returning(full_text_evidence()))

    [support] = [call for call in sent if call["id"] == "citation_support"]
    instructions = support["question"]["instructions"]
    assert "Do not use the title as evidence" in instructions


def test_without_a_title_the_evidence_is_still_judged(monkeypatch):
    """書目解析不出篇名時不可以整筆停掉——內容證據明明就在手上。"""
    stub = Stub(monkeypatch)
    resolver = resolver_returning(full_text_evidence(
        reference=ReferenceLookupResult(1, ""),
    ))

    [row] = run_method_a_pipeline([PAIR], None, OpenRouterJevAdapter("test-placeholder"),
                                  evidence_resolver=resolver)

    assert row.method_a.band is SupportBand.SUPPORTED
    assert stub.states[1]["evidence_type"] == "全文"


def test_turning_title_only_scoring_off_restores_the_supervisors_rule(monkeypatch):
    """關掉開關就回到原本的規則：標「無法查核」、不給分數。

    組內 2026-11-27 採用篇名評分，但原本的要求仍是可選設定，保護留著。
    """
    stub = Stub(monkeypatch)
    no_evidence = full_text_evidence(text="", basis=EvidenceBasis.UNAVAILABLE)

    [row] = run_method_a_pipeline(
        [PAIR], None, OpenRouterJevAdapter("test-placeholder", title_only_scoring=False),
        evidence_resolver=resolver_returning(no_evidence))

    assert row.method_a.band is SupportBand.UNVERIFIABLE
    assert row.method_a.support_score is None
    assert stub.events == ["title_relevance"], "不給分數時不該再花 Q2 的錢"


def test_the_old_lookup_path_also_falls_back_to_the_title(monkeypatch):
    """舊的查摘要路徑同樣適用：有文獻但沒摘要時用篇名評分。"""
    stub = Stub(monkeypatch, score=0.45)
    reference_adapter = Mock()
    reference_adapter.lookup.side_effect = lambda pair: ReferenceLookupResult(
        1, "A study of treatment time", source_status=SourceStatus.NO_ABSTRACT,
    )

    [row] = run_method_a_pipeline(
        [PAIR], reference_adapter, OpenRouterJevAdapter("test-placeholder"))

    assert stub.states[1]["evidence_type"] == "篇名"
    assert stub.states[1]["evidence"] == "A study of treatment time"
    assert row.method_a.band is SupportBand.UNCERTAIN
    assert row.to_record()["方法 A 判斷依據"] == "篇名"


def test_one_broken_pair_does_not_take_the_whole_batch_down(monkeypatch):
    """判斷某一筆時壞掉，其他 ref 本來拿得到的結果不可以跟著消失。

    實測過：使用者上傳一個編碼壞掉的檔案，切句那一步丟 IndexError，整個 run
    沒有任何結果，連另一條 ref 本來拿得到的證據也一起沒了。失敗要記成這一筆的
    錯誤，不是整批沒有輸出。
    """

    import src.pipeline as pipeline_module

    stub = Stub(monkeypatch)
    pairs = [
        CitationPair("p1", "The method reduces treatment time [1].", "[1]", 1,
                     '[1] A. Author, "A study of treatment time," J. X, 2026.'),
        CitationPair("p2", "The method reduces treatment time [2].", "[2]", 2,
                     '[2] B. Author, "Another study," J. Y, 2026.'),
    ]
    resolver = Mock()
    resolver.resolve.side_effect = [
        full_text_evidence(ref_number=1),
        # 巢狀的 reference 也要是第 2 筆，否則 pipeline 會先以「取證層交回別筆 ref」
        # 擋下來，測到的就不是我們要測的那條路
        full_text_evidence(ref_number=2,
                           reference=ReferenceLookupResult(2, "Another study")),
    ]
    original = pipeline_module.select_evidence

    def explode(claim, text, **kwargs):
        if resolver.resolve.call_count == 1:
            raise IndexError("list index out of range")
        return original(claim, text, **kwargs)

    monkeypatch.setattr(pipeline_module, "select_evidence", explode)

    rows = run_method_a_pipeline(pairs, None, OpenRouterJevAdapter("test-placeholder"),
                                 evidence_resolver=resolver)

    assert len(rows) == 2
    assert rows[0].judgment.status is JudgmentStatus.SUPPORT_ERROR
    assert "IndexError" in rows[0].judgment.error_message
    assert rows[0].method_a.support_score is None
    # 第二筆照常有結果
    assert rows[1].judgment.status is not JudgmentStatus.SUPPORT_ERROR
    assert rows[1].method_a.support_score == 0.80
    assert stub.events, "第二筆還是要真的問到 Jev"
