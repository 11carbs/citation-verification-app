"""真實 IEEE 段落的配對回歸測試。

這支要放進 repo 的 `tests/`，並把 `test_cases.json` 一起複製過去（放在
`tests/` 底下或 repo 根目錄都找得到，也可以用環境變數 CITATION_TEST_CASES 指定）。

    cp test_real_cases.py test_cases.json <repo>/tests/
    cd <repo> && python -m pytest tests/test_real_cases.py -v

驗的是解析層：ref 編號、配對數、缺少 ref、未被引用的 ref、引用句有沒有被硬換行截斷。
「支持／不確定／不支持」不在這裡驗——那要人工讀過被引文獻才有正確答案。

注意：case1-fido2 與 case6-pdfnoise 在未套 patch 0001 的版本上會失敗，
那是刻意的回歸測試，不是測試寫錯。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.pairing import build_pairing_outcome
from src.reference_parser import parse_reference_list
from src.schemas import ReferenceMatchStatus


def _locate_cases() -> Path:
    """依序找 test_cases.json：環境變數 → 本檔旁邊 → repo 根目錄。"""
    override = os.environ.get("CITATION_TEST_CASES")
    if override:
        return Path(override)
    here = Path(__file__).resolve().parent
    for candidate in (here / "test_cases.json", here.parent / "test_cases.json"):
        if candidate.exists():
            return candidate
    return here / "test_cases.json"


_CASES_PATH = _locate_cases()

if not _CASES_PATH.exists():
    pytest.skip(
        f"找不到測試資料 {_CASES_PATH}，請把 test_cases.json 複製到 tests/ 底下，"
        "或用環境變數 CITATION_TEST_CASES 指定路徑。",
        allow_module_level=True,
    )

_CASES = json.loads(_CASES_PATH.read_text(encoding="utf-8"))["cases"]
_IDS = [case["id"] for case in _CASES]


@pytest.fixture(scope="module", params=_CASES, ids=_IDS)
def case(request):
    """逐筆提供測試資料，連同跑完的配對結果。"""
    data = request.param
    outcome = build_pairing_outcome(data["paragraph"], data["references"])
    return data, outcome


def test_pair_count(case):
    """配對數要等於該段落裡的引用次數。"""
    data, outcome = case
    assert len(outcome.pairs) == data["expected"]["pair_count"]


def test_ref_numbers(case):
    """抽出的 ref 編號要完全吻合，順序不計。"""
    data, outcome = case
    got = sorted(pair.ref_number for pair in outcome.pairs)
    assert got == sorted(data["expected"]["ref_numbers"])


def test_missing_references(case):
    """內文有、ref 清單沒有的編號要標成 MISSING_REFERENCE。

    這條不可以和「不支持」混在一起算：取不到來源與內容不支持是兩回事。
    """
    data, outcome = case
    got = sorted(
        {
            pair.ref_number
            for pair in outcome.pairs
            if pair.match_status is ReferenceMatchStatus.MISSING_REFERENCE
        }
    )
    assert got == sorted(data["expected"]["missing_reference"])


def test_uncited_references_are_not_paired(case):
    """ref 清單裡沒被引用的項目不可以被配對給任何引用。"""
    data, outcome = case
    all_refs = set(parse_reference_list(data["references"]).keys())
    cited = {pair.ref_number for pair in outcome.pairs}
    assert sorted(all_refs - cited) == sorted(data["expected"]["uncited_refs"])


def test_compound_flag(case):
    """is_compound 是「同一引用陳述句含一個以上引用」，以句子為單位。

    所以同一段落裡不同句子的值可能不同（case3 第一句是複合、後兩句不是），
    不能用單一布林值描述整筆，要逐編號比對。
    """
    data, outcome = case
    expected = data["expected"].get("compound_ref_numbers")
    if expected is None:
        pytest.skip("這筆沒有指定 compound_ref_numbers")
    got = sorted({pair.ref_number for pair in outcome.pairs if pair.is_compound})
    assert got == sorted(expected)


def test_claim_not_truncated(case):
    """引用句不可以被硬換行截斷。

    case1-fido2 與 case6-pdfnoise 在未套 patch 0001 的版本上會失敗，這是刻意的。
    """
    data, outcome = case
    needle = data["expected"].get("claim_must_contain")
    if needle is None:
        pytest.skip("這筆沒有指定 claim_must_contain")
    sample = outcome.pairs[0].claim_text if outcome.pairs else "(無配對)"
    assert any(needle in pair.claim_text for pair in outcome.pairs), (
        f"引用句被截斷，找不到 {needle!r}；第一筆實際為 {sample[:80]!r}"
    )
