"""第二人：用整條原始書目去 Crossref 比對，再獨立驗證回傳的是不是同一篇。

**為什麼需要這一層**

``reference_metadata`` 用 regex 抽篇名，但作者年份制沒有統一標準。拿 218 份
開放存取論文全文抽出的 7,294 筆真實書目實測，regex 只抽得到 27%：
APA／Harvard 括號年份（佔 32%）能抽到 34%，Vancouver 那種「年份在句尾」
（佔 23%）只有 1%，而且愈想多抽就愈容易抽錯。

**抽錯比抽不到更糟**：錯的篇名會讓系統去取到別篇論文的全文，產生假的
「引用與來源不符」，那正是本研究要偵測的東西。

所以這一層換個方向：不猜篇名，把**整條原文**丟給 Crossref 的
``query.bibliographic``（官方文件說它就是給 citation look up 用的，
最佳匹配排第一），篇名由權威紀錄提供。

**驗證必須獨立**

只看 Crossref 的分數不夠。驗證用的第一作者姓氏與年份是
``reference_metadata`` 從原文**自己抽出來**的，跟 Crossref 無關；兩邊各自
取得、互相印證才算驗證。只採用 Crossref 的回答等於把判斷外包出去。

**門檻怎麼定的**

校準數據、採用規則與已知限制都寫在 ``CROSSREF_MIN_SCORE`` 與
``REJECTED_CROSSREF_TYPES`` 常數旁邊，以及 ``MatchDecision`` 的 docstring。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from src.reference_lookup import CROSSREF_BASE, HttpClient, title_similarity
from src.reference_metadata import ReferenceMetadata


# Crossref 回傳的相關性分數。校準樣本見模組 docstring：分數低於 60 的那一段
# 在樣本裡**沒有一筆**篇名對得上，所以低分不是「比較不確定」，是「錯的」。
# 門檻來自 298 筆校準（218 筆有 regex 篇名可對照、80 筆 regex 失敗）。
# 語料是 218 份開放存取論文全文裡的真實書目，跨領域跨出版社。
#
# Crossref 分數的分層一致率（200 筆那輪）：
#   0–40 0%、40–60 3%、60–70 31%、70–90 57%、90–120 90%、120–200 93%、200+ 100%
#
# 規則比較（採用率／篇名幾乎相同／判定為同一篇，後者容許副標題完整度不同）：
#   不驗證           100% / 63% / —
#   score>=70        70% / 88% / —
#   score>=70＋作者＋年份  58% / 90% / 97%
#   score>=90＋作者＋年份  53% / 92% / 99%   ← 採用這條
#
# 兩個數字差在哪：不一致的 12 筆裡多數不是配錯論文，是副標題完整度不同
# （`3DTV at Home: Eulerian-Lagrangian...` 對上 Crossref 的 `3DTV at home`）。
# 真正配到不同論文的只有 2 筆（126 筆中），例如 Megatron-LM 配到同一群作者的
# 另一篇 GPU 訓練論文。
#
# ⚠️ 兩個先前被推翻的結論，留著避免有人再走一次：
#   1. 45 筆小樣本看起來「>=120 全數正確、可免核對作者」，200 筆重測是 93%，不成立
#   2. 「作者相符」原本會拒絕掉 100% 需要救援的書目，因為舊版的 first_author
#      只在篇名抽到時才有值。已改成獨立抽取
#
# ⚠️ 對照組是本機 regex 抽的篇名，不是人工標註的黃金標準，所以這是一致率不是精確率。
CROSSREF_MIN_SCORE = 90.0
# 送出去的字串長度上限。整條書目通常 100–300 字元；超長多半是解析時把好幾筆
# 黏在一起了，送出去只會拿到雜訊。
MAX_QUERY_CHARS = 600

# Crossref 收錄的不只論文，還有審稿意見、圖表元件、整本期刊這些附屬紀錄，
# 它們的作者與年份跟正篇一樣，分數還可能更高，但**不是可以比對引用的來源文本**。
#
# 2026-11-28 實測（SciCiteVal 24 筆）：scite 那篇拿到全場最高的 216.2 分，
# 作者、年份、篇名全對，但 DOI 是 `10.1162/qss_a_00146/v2/response1`
# （type=peer-review，正篇是 `10.1162/qss_a_00146`，type=journal-article）。
# 取全文時只抓到 14 字，退回摘要，證據從 55,292 字掉到 1,354 字。
#
# 用排除法而不是允許清單：Crossref 之後新增型別時，寧可放行再補規則，
# 也不要默默把正常的論文全部拒掉。
REJECTED_CROSSREF_TYPES = frozenset({
    "peer-review",      # 審稿意見與作者回覆
    "component",        # 論文裡的單張圖表
    "journal",          # 整份期刊
    "journal-issue",    # 整期
    "journal-volume",   # 整卷
    "book-series",
    "book-set",
    "proceedings-series",
    "grant",
    "report-component",
})

# 姓氏前綴（van、da、de 這些）單獨拿來比對幾乎一定誤中，不當候選。
# 任一候選對上就算相符，所以多一個雜訊候選就等於少一道驗證。
_NAME_PARTICLES = frozenset({
    "af", "al", "av", "bin", "da", "das", "de", "del", "della", "den", "der",
    "des", "di", "do", "dos", "du", "el", "ibn", "la", "le", "ten", "ter",
    "van", "von", "y", "zu",
})


class MatchStatus(StrEnum):
    """比對的結果。四種要分開，不可以混成「查不到」。"""

    CONFIRMED = "比對並驗證通過"
    LOW_SCORE = "有候選但相關性不足"
    AUTHOR_MISMATCH = "有候選但第一作者對不上"
    YEAR_MISMATCH = "有候選但年份對不上"
    WRONG_TYPE = "候選不是可比對的來源文本"
    NO_CANDIDATE = "Crossref 查無候選"
    NOT_ATTEMPTED = "未送出比對"
    LOOKUP_FAILED = "比對查詢失敗"


@dataclass(frozen=True, slots=True)
class MatchDecision:
    """一筆書目的比對結果。

    ``title`` 只有在 ``status is CONFIRMED`` 時才有值。其餘狀態一律留空，
    呼叫端不可以拿沒驗證過的候選來用。
    """

    status: MatchStatus = MatchStatus.NOT_ATTEMPTED
    title: str = ""
    doi: str = ""
    score: float = 0.0
    candidate_title: str = ""   # 沒通過驗證時也留著，給人看為什麼被拒絕
    note: str = ""

    @property
    def confirmed(self) -> bool:
        return self.status is MatchStatus.CONFIRMED


def _surname_candidates(first_author: str) -> set[str]:
    """姓氏在哪一個位置，不同格式不一樣，所以頭尾都當候選。

    - `Abalkina, A.`（逗號制）→ 姓在前
    - `Anna Abalkina`（APA／ACL）→ 姓在後
    - `Rumble SM`（Vancouver）→ 姓在前，後面是縮寫

    任一個對得上就算相符。縮寫（兩個字母以內、或全大寫的短字串）不當候選，
    否則「SM」「A」這種幾乎一定會誤中。
    """

    name = first_author.strip().strip(".,;")
    if not name:
        return set()
    if "," in name:
        name = name.split(",", 1)[0].strip()
    tokens = [t.strip(".,;()[]\u2019\'") for t in name.split()]
    return {
        t.casefold() for t in tokens
        if len(t) >= 2
        and any(ch.isalpha() for ch in t)
        and not _is_initials(t)
        and t.casefold() not in _NAME_PARTICLES
    }


def _is_initials(token: str) -> bool:
    """``SM``、``A``、``JR`` 是縮寫；``Ho``、``Wu`` 是姓氏。

    靠「全大寫且很短」判斷，不靠長度：舊版用 ``len > 2`` 排除縮寫，結果連
    ``Ho``、``Wu``、``Li``、``Ng``、``Xu`` 一起排除，對東亞與東南亞姓氏整類失效
    （2026-11-28 實測：``Xanh Ho`` 的候選 114.6 分、篇名完全正確，卻被判
    「第一作者對不上」）。全大寫的長字串（``ABALKINA``）是把姓氏寫成大寫，要留著。
    """

    return token.isupper() and len(token) <= 3


def _candidate_year(item: dict) -> int | None:
    parts = (item.get("issued") or {}).get("date-parts") or []
    if parts and isinstance(parts[0], list) and parts[0]:
        value = parts[0][0]
        return value if isinstance(value, int) else None
    return None


def _candidate_surnames(item: dict) -> set[str]:
    """候選的作者姓氏，拆成 token 集合。

    **一定要拆 token，不可以用子字串比對。** 兩個字母的姓氏一旦允許，
    ``"ho" in "thomson schneider"`` 就會是 ``True``，驗證等於沒做。
    複姓（``van der Berg``、``da Silva``）本身也要拆開，否則對不上。
    """

    authors = item.get("author")
    if not isinstance(authors, list):
        return set()
    joined = " ".join(
        str(a.get("family") or "") for a in authors if isinstance(a, dict)
    )
    return {t.strip(".,;()[]\u2019\'").casefold()
            for t in joined.split() if any(ch.isalpha() for ch in t)}


def match_reference(
    raw_reference: str,
    meta: ReferenceMetadata,
    http: HttpClient,
    *,
    min_score: float = CROSSREF_MIN_SCORE,
) -> MatchDecision:
    """把整條書目丟給 Crossref，再用 ``meta`` 的作者與年份獨立驗證。

    ``meta`` 必須是從 ``raw_reference`` 本身解析出來的，不能是別處來的，
    否則驗證就不是獨立的。
    """

    query = " ".join(raw_reference.split())[:MAX_QUERY_CHARS]
    if len(query) < 20:
        return MatchDecision(
            status=MatchStatus.NOT_ATTEMPTED, note="書目太短，不送出比對",
        )

    status_code, payload, error = http.get(
        CROSSREF_BASE,
        {"query.bibliographic": query, "rows": 1,
         "select": "DOI,title,author,issued,score,type"},
    )
    if error or status_code != 200 or not isinstance(payload, dict):
        # 查詢失敗跟「查無此文獻」是兩件事，不可以混：前者重跑可能有結果
        return MatchDecision(
            status=MatchStatus.LOOKUP_FAILED,
            note=f"Crossref 比對失敗（HTTP {status_code}{'；' + error if error else ''}）",
        )

    items = ((payload.get("message") or {}).get("items") or [])
    item = items[0] if items and isinstance(items[0], dict) else None
    if not item:
        return MatchDecision(status=MatchStatus.NO_CANDIDATE, note="Crossref 沒有回傳候選")

    titles = item.get("title")
    candidate_title = str(titles[0]) if isinstance(titles, list) and titles else ""
    score = float(item.get("score") or 0.0)
    doi = str(item.get("doi") or item.get("DOI") or "").lower()
    base = MatchDecision(candidate_title=candidate_title, score=score, doi=doi)

    # 型別先篩，再看分數：審稿回覆這類附屬紀錄的作者、年份、篇名都跟正篇一樣，
    # 分數還可能是全場最高，分數與作者核對都擋不住它
    record_type = str(item.get("type") or "").casefold()
    if record_type in REJECTED_CROSSREF_TYPES:
        return replace(
            base, status=MatchStatus.WRONG_TYPE, doi="",
            note=f"候選的 Crossref 型別是 {record_type}，不是論文本體",
        )

    if score < min_score:
        return replace(
            base, status=MatchStatus.LOW_SCORE,
            note=f"相關性 {score:.1f} 低於門檻 {min_score:.0f}",
        )
    if not candidate_title:
        return replace(
            base, status=MatchStatus.NO_CANDIDATE,
            note="候選紀錄沒有篇名",
        )

    year = _candidate_year(item)
    if meta.publication_year is not None and year is not None and abs(year - meta.publication_year) > 1:
        # 差一年放行：線上先行出版與正式出版年份常常差一年
        return replace(
            base, status=MatchStatus.YEAR_MISMATCH,
            note=f"書目寫 {meta.publication_year}，候選是 {year}",
        )

    # 分數再高也要作者相符。分數是 Crossref 自己給的，拿它驗證它自己的答案
    # 不叫驗證；作者是我們從原文獨立抽出來的，那才是另一個證據來源。
    surnames = _surname_candidates(meta.first_author)
    if not surnames:
        return replace(
            base, status=MatchStatus.AUTHOR_MISMATCH,
            note="抽不到第一作者，沒有獨立證據可以核對這個候選",
        )
    found = _candidate_surnames(item)
    if not any(name in found for name in surnames):
        return replace(
            base, status=MatchStatus.AUTHOR_MISMATCH,
            note=f"第一作者「{meta.first_author}」不在候選的作者清單裡",
        )

    note = f"Crossref 相關性 {score:.1f}"
    if meta.title and title_similarity(meta.title, candidate_title) < 0.85:
        # 兩條路給出不同篇名時要留痕跡，之後查得出是哪一邊錯
        note += f"；與本機解析的篇名不同（本機：{meta.title[:60]}）"
    return MatchDecision(
        status=MatchStatus.CONFIRMED, title=candidate_title, doi=doi,
        score=score, candidate_title=candidate_title, note=note,
    )
