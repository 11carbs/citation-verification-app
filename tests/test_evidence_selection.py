"""第二人：兩段式證據挑選（關鍵詞收斂 ＋ 語意挑選），不連網。

重點：第二段失敗不可以讓整筆變成「沒有證據」，而且模型回答 none 與第一段
找不到候選是兩件不同的事。
"""

import pytest

from src.evidence_retrieval import RetrievalStatus
from src.evidence_selection import (
    MAX_CANDIDATES,
    Choice,
    SelectionStatus,
    build_choice_question,
    CallableChooser,
    select_evidence,
)

SOURCE = """# Fabrication and errors in bibliographic citations

## Results

GPT-3.5 produced 55% fabricated citations, while GPT-4 produced 18%.
Citations that were not fabricated often contained substantive errors.
The reviewers resolved every disagreement about fabricated citations by discussion.
"""

CLAIM = "GPT-3.5 fabricated 55% of the citations it generated [1]."


class FakeChooser:
    def __init__(self, key: str = "excerpt_1", model: str = "jev-1.14") -> None:
        self.key = key
        self.model = model
        self.seen: list[dict[str, str]] = []

    def choose(self, claim: str, candidates: dict[str, str]) -> Choice:
        self.seen.append(candidates)
        return Choice(key=self.key, model=self.model, request_id="req-1")


class ExplodingChooser:
    def choose(self, claim: str, candidates: dict[str, str]) -> Choice:
        raise RuntimeError("rate limited")


def test_without_a_chooser_only_the_keyword_stage_runs():
    result = select_evidence(CLAIM, SOURCE)

    assert result.status is SelectionStatus.NOT_ATTEMPTED
    assert result.candidates          # 第一段的候選仍然在
    assert result.has_evidence is True
    assert "沒有提供語意挑選器" in result.note


def test_the_chooser_picks_one_of_the_keyword_candidates():
    chooser = FakeChooser()

    result = select_evidence(CLAIM, SOURCE, chooser=chooser)

    assert result.status is SelectionStatus.PICKED
    assert "55% fabricated citations" in result.excerpt
    assert result.section == "Results"
    assert result.model == "jev-1.14"
    assert result.request_id == "req-1"


def test_the_chooser_only_ever_sees_the_narrowed_candidates():
    """原本的做法是把整份摘要的句子全丟進選項，全文會直接撞上 12 句上限。"""

    long_source = "# P\n\n## Results\n\n" + "\n\n".join(
        f"Sentence {i} about fabricated citations in the evaluated sample." for i in range(200))
    chooser = FakeChooser()

    select_evidence(CLAIM, long_source, chooser=chooser, top_k=5)

    assert len(chooser.seen[0]) == 5


def test_candidates_never_exceed_the_model_limit():
    chooser = FakeChooser()
    long_source = "# P\n\n## Results\n\n" + "\n\n".join(
        f"Sentence {i} about fabricated citations in the evaluated sample." for i in range(200))

    select_evidence(CLAIM, long_source, chooser=chooser, top_k=99)

    assert len(chooser.seen[0]) <= MAX_CANDIDATES


def test_a_chooser_failure_keeps_the_keyword_candidates():
    """限流不可以把「模型沒答」寫成「來源不支持」。"""

    result = select_evidence(CLAIM, SOURCE, chooser=ExplodingChooser())

    assert result.status is SelectionStatus.CHOOSER_FAILED
    assert result.has_evidence is True
    assert result.candidates
    assert "RuntimeError" in result.note
    assert result.text_for_judgement()      # 仍然送得出東西去判斷


def test_none_is_not_the_same_as_no_candidates():
    picked_none = select_evidence(CLAIM, SOURCE, chooser=FakeChooser(key="none"))
    nothing_found = select_evidence(
        "Quantum annealing outperformed simulated annealing on portfolios [1].",
        SOURCE, chooser=FakeChooser())

    assert picked_none.status is SelectionStatus.NONE_OF_THEM
    assert picked_none.candidates           # 候選存在，是模型說都不算
    assert nothing_found.status is SelectionStatus.NOT_ATTEMPTED
    assert nothing_found.candidates == ()


def test_an_undefined_choice_is_a_failure_not_a_pick():
    result = select_evidence(CLAIM, SOURCE, chooser=FakeChooser(key="excerpt_99"))

    assert result.status is SelectionStatus.CHOOSER_FAILED
    assert result.excerpt == ""
    assert "未定義的選項" in result.note


def test_a_weak_match_is_flagged_even_after_the_model_picks():
    generic = ("# A paper about citation checking\n\n## Results\n\n"
               "The improved pipeline processed the records in a different order than before.\n")
    claim = "Sharding improved blockchain transaction throughput by an order of magnitude [1]."

    result = select_evidence(claim, generic, chooser=FakeChooser())

    assert result.retrieval.status is RetrievalStatus.WEAK_MATCH
    assert result.status is SelectionStatus.PICKED
    assert "零星字詞相符" in result.note


def test_a_missing_quote_is_still_flagged_after_the_model_picks():
    source = ("# P\n\n## Results\n\nThe fabrication rate was similar across the two model "
              "versions we evaluated in this study.\n")
    claim = 'They write that "the fabrication rate differs sharply between models" [1].'

    result = select_evidence(claim, source, chooser=FakeChooser())

    assert result.retrieval.has_misquote_signal is True
    assert "逐字引號" in result.note


def test_both_methods_get_the_same_candidates():
    """A 和 B 各自用自己的模型挑，但候選句必須是同一份。"""

    jev, llm = FakeChooser(model="jev-1.14"), FakeChooser(model="gpt-4.1-mini")

    for_a = select_evidence(CLAIM, SOURCE, chooser=jev)
    for_b = select_evidence(CLAIM, SOURCE, chooser=llm)

    assert [p.text for p in for_a.candidates] == [p.text for p in for_b.candidates]
    assert jev.seen[0] == llm.seen[0]


def test_text_for_judgement_falls_back_to_the_keyword_candidates():
    picked = select_evidence(CLAIM, SOURCE, chooser=FakeChooser())
    failed = select_evidence(CLAIM, SOURCE, chooser=ExplodingChooser())

    assert picked.text_for_judgement().startswith("（Results）")
    assert "55%" in failed.text_for_judgement()


def test_no_source_text_reports_the_first_stage_reason():
    result = select_evidence(CLAIM, "", chooser=FakeChooser())

    assert result.status is SelectionStatus.NOT_ATTEMPTED
    assert result.retrieval.status is RetrievalStatus.NO_SOURCE_TEXT
    assert "沒有候選" in result.note


def test_the_question_keeps_the_none_option():
    question = build_choice_question({"excerpt_1": "a", "excerpt_2": "b"})

    assert question["type"] == "choice"
    assert "none" in question["criteria"]
    assert question["instructions"]


def test_callable_chooser_passes_the_claim_and_candidates():
    captured = {}

    def ask(state, name, question):
        captured["state"] = state
        captured["name"] = name
        captured["question"] = question
        return "excerpt_1", "jev-1.14", "req-9"

    result = select_evidence(CLAIM, SOURCE, chooser=CallableChooser(ask=ask))

    assert result.status is SelectionStatus.PICKED
    assert captured["state"]["claim"] == CLAIM
    assert captured["name"] == "evidence_excerpt"
    assert "none" in captured["question"]["criteria"]


@pytest.mark.parametrize("top_k", [0, -3])
def test_top_k_is_clamped_to_at_least_one_candidate(top_k: int):
    chooser = FakeChooser()

    result = select_evidence(CLAIM, SOURCE, chooser=chooser, top_k=top_k)

    assert len(result.candidates) == 1


def test_duplicate_candidates_are_merged_before_the_model_sees_them():
    """同一句常常同時在 Abstract 和 Discussion，送兩個一樣的選項是浪費。"""

    repeated = "GPT-3.5 produced 55% fabricated citations in the evaluated reviews."
    source = f"# P\n\n## Abstract\n\n{repeated}\n\n## Discussion\n\n{repeated}\n"
    chooser = FakeChooser()

    result = select_evidence(CLAIM, source, chooser=chooser, top_k=5)

    assert len(chooser.seen[0]) == 1
    assert len(result.candidates) == 1
    assert "重複" in result.note
