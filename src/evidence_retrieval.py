"""第二人：從被引文獻的全文裡找出與引用句相關的證據段落。

做法是全文轉成 Markdown 後，列出全文中含引用句
「關鍵詞」的句子，交給判斷端比對是否為正確引用。11/24 改成允許整篇全文之後要不要
沿用這一步屬【組內定】；這裡做成獨立的一層，要不要用由呼叫端決定。

為什麼需要這一層：實測一篇全文 26,000–37,000 字，整篇送進去又貴又稀釋重點，
Jev 更是只能從選項中選答案，不適合吃整篇。

**狀態分開記，不可以混：**

- ``FOUND``：找到含關鍵詞的句子
- ``WEAK_MATCH``：只命中零星的泛用字詞，主張的核心概念整篇都沒出現。段落照樣附上
  給人看，但**不算找到證據**
- ``NO_RELEVANT_PASSAGE``：來源有拿到、也切得出句子，但一個關鍵詞都沒命中。
  這要不要算「無法查核」屬【組內定】，本模組**不替它下結論**
- ``UNPARSEABLE_SOURCE``：來源有字但切不出句子，是解析失敗，不是沒有來源
- ``NO_SOURCE_TEXT`` / ``NO_KEYWORDS``：前置條件不足，不是「來源不支持」

**幾個刻意的設計：**

- **References、致謝這些段落要排除。** 被引文獻自己的參考文獻清單裡全是別人的篇名，
  那些詞在該文最罕見，idf 會把它推上第一名——等於拿第三篇論文的篇名當來源內容。
- **逐字引號優先。** 引用句裡有引號時，有命中的句子一律排在前面（用排序鍵保證，
  不是靠加分，加分擋不住 idf）。引號在來源完全找不到會記進 ``unmatched_quotes``：
  誤引是本研究最好抓的一類錯誤，不可以讓它消失在欄位之間。
- **送出去的節錄必須是原文逐字。** 這是一個在驗證「引用是否忠實於來源」的系統，
  自己改動來源文字無法解釋。
- **表格每一列各自成句。** 整張表併成一句時，「GPT-4 是 18%」那一列會和別列的 55%
  黏在一起，反而變成錯誤主張的支持證據。

逐字引用（quotation）與轉述（mention）難度差一個數量級：前者是字串比對，後者是語意
蘊含（Santos et al., 2023），實證比例約 20:1。本模組只做字串層的定位，語意判斷留給
判斷端。``MIN_KEYWORD_COVERAGE`` 偏高會讓 mention 大量落在 ``WEAK_MATCH``，這個值
屬【組內定】。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import re
import unicodedata

MIN_SENTENCE_CHARS = 12
MIN_SENTENCE_TOKENS = 3
MIN_TABLE_ROW_CHARS = 5
MIN_TABLE_ROW_TOKENS = 2
DEFAULT_TOP_K = 5
MIN_KEYWORD_CHARS = 3
# 引用句的關鍵詞至少要有這個比例出現在來源裡，才算真的找到相關段落。【組內定】
MIN_KEYWORD_COVERAGE = 0.5
# 命中數也要有下限，否則「Blockchain improved」這種兩個詞的引用句，
# 命中一個 improved 就達到 50%，門檻形同失效
MIN_MATCHED_KEYWORDS = 2

_CITATION_MARKER = re.compile(r"\[\s*\d+(?:\s*[,–—-]\s*\d+)*\s*\]")
# 敘述式引用：Walters and Wilder (2023)、Smith et al. (2023a)。作者姓氏不是主張的內容
_NARRATIVE_CITATION = re.compile(
    r"\b(?:[A-Z][\w'’-]+(?:\s*(?:,|and|&)\s*)?){1,4}(?:\s*et\s+al\.?)?\s*\(\s*(?:19|20)\d{2}[a-z]?\s*\)")
_AUTHOR_YEAR = re.compile(r"\(([^()]*?\b(?:19|20)\d{2}[a-z]?)\)")
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_CODE_FENCE = re.compile(r"^\s*```")
_TABLE_ROW = re.compile(r"^\s*\|")
_TABLE_SEPARATOR = re.compile(r"^[\s|:-]+$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_MARKDOWN_NOISE = re.compile(r"[*_`>]+")
# 模型名稱在這個題目裡就是關鍵詞，不可以被切開：GPT-4、GPT-3.5、BERT-base
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9'’-]*(?:\.\d+)*|\d+(?:[.,]\d+)*%?")
_QUOTED = re.compile(r"[“\"]([^”\"]{8,300})[”\"]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"(])")
_ABBREVIATIONS = (
    "et al.", "e.g.", "i.e.", "cf.", "vs.", "fig.", "figs.", "eq.", "ref.", "refs.",
    "no.", "vol.", "pp.", "approx.", "dr.", "prof.", "mr.", "ms.", "st.", "ca.",
)
# 這些段落不是被引文獻自己的內容，拿來當證據會指向第三篇論文或與主張無關
_SKIP_SECTIONS = re.compile(
    r"^\s*(?:\d+\.?\s*)?(?:references?|bibliography|works\s+cited|literature\s+cited|"
    r"acknowledge?ments?|author\s+contributions?|competing\s+interests?|"
    r"conflicts?\s+of\s+interest|funding|data\s+availability|supplementary|"
    r"abbreviations|about\s+this\s+article|rights\s+and\s+permissions)\b", re.IGNORECASE)

_STOPWORDS = frozenset("""
a an the and or but if then than that this these those there here
is are was were be been being am do does did done doing
have has had having will would shall should can could may might must
of in on at by for with without from into onto over under about across
as it its their his her our your my we they he she you i
not no nor so such own same other another more most much many few
which who whom whose what when where why how all any both each
to too very s t don now also however thus therefore while whereas
between among during before after above below up down out off again further
only just even still yet because since although though well via upon
""".split())
# 轉述框架：描述「誰說了什麼」的字，不是主張本身的內容。
# 領域動詞（improve、increase、reduce、outperform…）刻意留著，它們常常就是主張的核心。
_REPORTING_WORDS = frozenset("""
prior previous earlier recent work works study studies paper papers article articles
research researcher researchers author authors literature review reviews
report reports reported reporting state states stated show shows showed shown
find finds found note notes noted observe observes observed argue argues argued
claim claims claimed suggest suggests suggested demonstrate demonstrates demonstrated
according cite cites cited citing discuss discusses discussed describe describes described
present presents presented examine examines examined investigate investigates investigated
indicate indicates indicated conclude concludes concluded
""".split())
_IGNORED_WORDS = _STOPWORDS | _REPORTING_WORDS


class RetrievalStatus(StrEnum):
    """找證據的結果。前置條件不足、解析失敗、找不到、零星相符是四件不同的事。"""

    FOUND = "找到相關段落"
    WEAK_MATCH = "只有零星字詞相符"
    NO_RELEVANT_PASSAGE = "來源裡找不到相關段落"
    UNPARSEABLE_SOURCE = "來源有內容但切不出句子"
    NO_SOURCE_TEXT = "沒有來源可找"
    NO_KEYWORDS = "引用句抽不出關鍵詞"


@dataclass(frozen=True, slots=True)
class Passage:
    """來源裡的一句候選證據。``text`` 一律是來源原文逐字。"""

    text: str
    section: str = ""
    score: float = 0.0
    matched: tuple[str, ...] = ()
    exact_quote: bool = False


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """一筆（引用句, 被引文獻來源）的證據檢索結果。"""

    status: RetrievalStatus = RetrievalStatus.NO_SOURCE_TEXT
    passages: tuple[Passage, ...] = ()
    keywords: tuple[str, ...] = ()
    matched_keywords: tuple[str, ...] = ()
    missing_keywords: tuple[str, ...] = ()
    quotes: tuple[str, ...] = ()
    unmatched_quotes: tuple[str, ...] = ()
    sentences_searched: int = 0
    fragments_skipped: int = 0
    source_chars: int = 0
    source_label: str = ""      # 搜的是全文還是摘要；摘要上的弱相符意義完全不同
    note: str = ""

    @property
    def found(self) -> bool:
        return self.status is RetrievalStatus.FOUND and bool(self.passages)

    @property
    def has_misquote_signal(self) -> bool:
        """引用句宣稱有逐字引號，但來源裡完全找不到。這是誤引最直接的訊號。"""

        return bool(self.unmatched_quotes)

    def excerpt(self, separator: str = "\n\n") -> str:
        """組成要送給判斷端的文字。弱相符會在開頭標明，呼叫端不會誤讀。"""

        parts = []
        if self.status is RetrievalStatus.WEAK_MATCH:
            parts.append("⚠️ 以下只是零星字詞相符，不等於找到證據")
        if self.unmatched_quotes:
            parts.append(f"⚠️ 引用句的逐字引號在來源裡找不到：「{self.unmatched_quotes[0]}」")
        for passage in self.passages:
            parts.append(f"（{passage.section}）{passage.text}" if passage.section else passage.text)
        return separator.join(parts)


def find_evidence(claim: str, source_markdown: str, *, top_k: int = DEFAULT_TOP_K,
                  source_label: str = "") -> RetrievalResult:
    """從來源裡挑出與引用句相關的句子。"""

    if not isinstance(source_markdown, str):
        source_markdown = "" if source_markdown is None else str(source_markdown)
    if not isinstance(claim, str):
        claim = "" if claim is None else str(claim)
    if not source_markdown.strip():
        return RetrievalResult(status=RetrievalStatus.NO_SOURCE_TEXT, source_label=source_label,
                               note="沒有拿到來源文字，無法檢索")

    keywords = extract_keywords(claim)
    quotes = tuple(dict.fromkeys(_normalize_quote(found) for found in _QUOTED.findall(claim)))
    quotes = tuple(quote for quote in quotes if quote)
    base = dict(keywords=keywords, quotes=quotes, source_chars=len(source_markdown),
                source_label=source_label)
    if not keywords and not quotes:
        return RetrievalResult(status=RetrievalStatus.NO_KEYWORDS,
                               note="引用句只有功能詞、轉述框架或引用標記，抽不出關鍵詞", **base)

    sentences, skipped, parse_notes = _parse_source(source_markdown)
    if not sentences:
        return RetrievalResult(
            status=RetrievalStatus.UNPARSEABLE_SOURCE, fragments_skipped=skipped,
            note="；".join(parse_notes + [f"來源有 {len(source_markdown)} 字，但切不出可用的句子"]),
            **base,
        )

    whole = _normalize_quote(source_markdown)
    unmatched_quotes = tuple(quote for quote in quotes if quote not in whole)

    weights = _keyword_weights(keywords, sentences)
    scored: list[Passage] = []
    matched_any: set[str] = set()
    for section, sentence in sentences:
        tokens = {_stem(token) for token in _TOKEN.findall(sentence.casefold())}
        hits = tuple(word for word in keywords if _stem(word) in tokens)
        quoted_hit = any(quote in _normalize_quote(sentence) for quote in quotes)
        if not hits and not quoted_hit:
            continue
        matched_any.update(hits)
        scored.append(Passage(text=sentence, section=section,
                              score=round(sum(weights.get(word, 1.0) for word in hits), 4),
                              matched=hits, exact_quote=quoted_hit))

    matched = tuple(word for word in keywords if word in matched_any)
    missing = tuple(word for word in keywords if word not in matched_any)
    notes = list(parse_notes)
    if unmatched_quotes:
        notes.append(f"引用句有 {len(unmatched_quotes)} 段逐字引號在來源裡找不到")
    if skipped:
        notes.append(f"略過 {skipped} 段非內文片段（References、致謝、表格分隔線等）")
    if not scored:
        notes.append(f"來源（{len(source_markdown)} 字、{len(sentences)} 句）裡沒有任何句子含"
                     "引用句的關鍵詞；這要不要算無法查核由呼叫端決定")
        return RetrievalResult(
            status=RetrievalStatus.NO_RELEVANT_PASSAGE, matched_keywords=(),
            missing_keywords=keywords, unmatched_quotes=unmatched_quotes,
            sentences_searched=len(sentences), fragments_skipped=skipped,
            note="；".join(notes), **base,
        )

    # 逐字引號命中一律排最前面：用排序鍵保證。加分擋不住 idf——罕見詞多的句子分數會更高
    scored.sort(key=lambda passage: (not passage.exact_quote, -passage.score, len(passage.text)))

    coverage = len(matched) / len(keywords) if keywords else 0.0
    floor = min(MIN_MATCHED_KEYWORDS, len(keywords)) if keywords else 0
    if any(passage.exact_quote for passage in scored) or (
            coverage >= MIN_KEYWORD_COVERAGE and len(matched) >= floor):
        status = RetrievalStatus.FOUND
    else:
        status = RetrievalStatus.WEAK_MATCH
        notes.append(f"引用句 {len(keywords)} 個關鍵詞只命中 {len(matched)} 個"
                     f"（{coverage:.0%}，門檻 {MIN_KEYWORD_COVERAGE:.0%} 且至少 {floor} 個）；"
                     "段落僅供人工檢查")
    return RetrievalResult(
        status=status,
        passages=tuple(scored[:top_k]) if top_k > 0 else (),
        matched_keywords=matched, missing_keywords=missing, unmatched_quotes=unmatched_quotes,
        sentences_searched=len(sentences), fragments_skipped=skipped,
        note="；".join(notes), **base,
    )


def extract_keywords(claim: str) -> tuple[str, ...]:
    """從引用句抽關鍵詞：去掉引用標記、敘述式引用的作者、功能詞與轉述框架。"""

    if not isinstance(claim, str) or not claim:
        return ()
    text = _CITATION_MARKER.sub(" ", claim)
    text = _NARRATIVE_CITATION.sub(" ", text)
    text = _AUTHOR_YEAR.sub(" ", text)
    seen: dict[str, None] = {}
    for token in _TOKEN.findall(text):
        folded = token.casefold().strip("'’-")
        if not folded or folded in _IGNORED_WORDS:
            continue
        is_number = any(char.isdigit() for char in folded)
        is_acronym = token.isupper() and len(token) >= 2
        if not is_number and not is_acronym and len(folded) < MIN_KEYWORD_CHARS:
            continue
        seen.setdefault(folded, None)
    return tuple(seen)


def split_sentences(markdown: str) -> tuple[tuple[str, str], ...]:
    """把 Markdown 來源切成 (章節標題, 句子)。"""

    return _parse_source(markdown)[0]


# --------------------------------------------------------------------------
# 內部
# --------------------------------------------------------------------------

def _parse_source(markdown: str) -> tuple[tuple[tuple[str, str], ...], int, list[str]]:
    """回傳 (句子, 略過的片段數, 解析備註)。"""

    notes: list[str] = []
    # ``` 數量不成對時不要吃掉後面整份文件，寧可把它當普通文字
    honour_fences = markdown.count("```") % 2 == 0
    if not honour_fences:
        notes.append("來源的程式碼區塊標記不成對，改為全部當成內文處理")

    section = ""
    in_code = False
    skipped = 0
    sentences: list[tuple[str, str]] = []
    for block, is_table_row in _blocks(markdown):
        if honour_fences and _CODE_FENCE.match(block):
            in_code = not in_code
            continue
        if in_code:
            continue
        heading = _MARKDOWN_HEADING.match(block)
        if heading:
            title = _clean(heading.group(2))
            if len(heading.group(1)) <= 2 or not section:
                section = title     # 小標題不要蓋掉 Results 這種主要章節
            continue
        if section and _SKIP_SECTIONS.match(section):
            skipped += 1            # References、致謝不是被引文獻自己的內容
            continue
        if is_table_row and _TABLE_SEPARATOR.match(block):
            skipped += 1
            continue
        body = _clean(block)
        if not body:
            continue
        for sentence in _split_block(body):
            if _usable(sentence, table_row=is_table_row):
                sentences.append((section, sentence))
            else:
                skipped += 1
    return tuple(sentences), skipped, notes


def _blocks(markdown: str) -> list[tuple[str, bool]]:
    """把文字併成段落，但標題、表格每一列、清單項目各自獨立。

    表格整張併成一句時，「GPT-4 是 18%」那一列會和別列的 55% 黏在一起，
    一張反駁主張的表格會變成支持證據。
    """

    blocks: list[tuple[str, bool]] = []
    for raw in markdown.split("\n"):
        line = raw.strip()
        if not line:
            blocks.append(("", False))
            continue
        standalone = bool(_CODE_FENCE.match(line) or _MARKDOWN_HEADING.match(line)
                          or _TABLE_ROW.match(line) or _LIST_ITEM.match(line))
        if standalone:
            blocks.append((line, bool(_TABLE_ROW.match(line))))
            continue
        if blocks and blocks[-1][0] and not blocks[-1][1]:
            previous = blocks[-1][0]
            if not (_CODE_FENCE.match(previous) or _MARKDOWN_HEADING.match(previous)
                    or _LIST_ITEM.match(previous)):
                blocks[-1] = (f"{previous} {line}", False)
                continue
        blocks.append((line, False))
    return [(text, table) for text, table in blocks if text]


def _clean(text: str) -> str:
    return " ".join(_MARKDOWN_NOISE.sub(" ", text).replace("|", " ").split())


def _usable(sentence: str, *, table_row: bool = False) -> bool:
    """短句不一定是雜訊：「GPT-4 fabricated 18%.」只有 21 字卻是關鍵證據。

    表格列是結構化資料（「GPT-4 18%」只有 9 字 2 個詞），門檻要另外放寬，
    否則被引文獻放在表格裡的數據會整批消失。
    """

    tokens = len(_TOKEN.findall(sentence))
    if table_row:
        return len(sentence) >= MIN_TABLE_ROW_CHARS and tokens >= MIN_TABLE_ROW_TOKENS
    return len(sentence) >= MIN_SENTENCE_CHARS and tokens >= MIN_SENTENCE_TOKENS


def _split_block(text: str) -> list[str]:
    """切句，避開常見縮寫的句點。還原時寫回**原文**，不可以改動來源文字。"""

    originals: list[str] = []

    def hide(match: re.Match[str]) -> str:
        originals.append(match.group(0))
        return f"\x00{len(originals) - 1}\x00"

    # 要詞界，否則 systems.、lowest.、test. 的字尾會被當成 ms.、st. 保護起來
    pattern = re.compile(
        "|".join(rf"(?<![A-Za-z]){re.escape(abbreviation)}" for abbreviation in _ABBREVIATIONS),
        re.IGNORECASE)
    guarded = pattern.sub(hide, text)
    restored = []
    for piece in _SENTENCE_END.split(guarded):
        rebuilt = re.sub(r"\x00(\d+)\x00", lambda m: originals[int(m.group(1))], piece)
        cleaned = rebuilt.strip()
        if cleaned:
            restored.append(cleaned)
    return restored


def _stem(word: str) -> str:
    """保守的字尾正規化。只處理最常見的複數與時態，寧可漏配也不要錯併。"""

    word = word.casefold()
    if len(word) >= 6 and word.endswith("sis"):
        return word[:-3] + "s"          # analysis/analyses、hypothesis/hypotheses
    if word.endswith("es") and len(word) >= 6:
        trimmed = word[:-2]
        if trimmed.endswith(("s", "x", "z", "ch", "sh")):
            return trimmed              # processes/process、accesses/access
    if word.endswith("ss"):
        return word                     # process、access 的字尾 s 不是複數
    for suffix, keep, floor in (("ies", 3, 4), ("ing", 3, 3), ("ed", 2, 3), ("s", 1, 4)):
        if word.endswith(suffix) and len(word) - keep >= floor:
            trimmed = word[: len(word) - keep]
            if suffix == "ies":
                return trimmed + "y"
            stripped = trimmed.rstrip("e")
            return stripped if len(stripped) >= 3 else trimmed
    if len(word) >= 4 and word.endswith("e"):
        return word[:-1]                # base/based、rate/rating
    return word


def _keyword_weights(keywords: tuple[str, ...], sentences: tuple[tuple[str, str], ...]) -> dict[str, float]:
    """越少句子出現的詞權重越高，避免整篇都有的詞主導排序。"""

    total = len(sentences) or 1
    counts: dict[str, int] = {}
    stems = {word: _stem(word) for word in keywords}
    for _, sentence in sentences:
        tokens = {_stem(token) for token in _TOKEN.findall(sentence.casefold())}
        for word, stem in stems.items():
            if stem in tokens:
                counts[word] = counts.get(word, 0) + 1
    return {word: math.log(1 + total / (1 + counts.get(word, 0))) for word in keywords}


def _normalize_quote(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", folded).split())
