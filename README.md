# Citation Verification Prototype

本專案是「未獲來源支持的引用」查核系統之第一階段 Streamlit 原型。

- 公開網站：https://citation-verification-app-jygqm5a6pmbam39td59prx.streamlit.app/
- GitHub：https://github.com/11carbs/citation-verification-app

目前完成：

- 最小 Streamlit 輸入與結果頁。
- IEEE 單篇、多篇並列及連續範圍引用擷取。
- 同一句內有清楚的連接詞或分號，且兩個子句各自以引用結尾時，使用各自的子句送入 Jev。
- 引用編號與使用者貼上的 ref 配對。
- 自動跳過未被引用的 ref。
- 缺少 ref 時保留錯誤狀態，不中斷整批處理。
- 一個引用來源顯示一列的固定輸出表格。
- 方法 A 與方法 B 並排顯示支持分數、證據層級與判斷依據。
- 支持、不確定、不支持的色彩標籤。
- UTF-8 CSV 匯出。
- 第一人的引用配對、第二人的 Semantic Scholar 與 OpenAlex 摘要查詢，以及第三人的 OpenRouter Jev 兩階段判斷已接入同一流程。
- 清楚標示的 mock 摘要與 Jev fixture，可在未串接 API 時測試網站。
- 空白輸入、無引用、缺少 ref 與重複引用的錯誤／警告處理。
- 可供三人共用的資料型別。
- 固定 mock data 與 CSV 匯出示範。
- 暫定支持分數門檻的邊界測試。

目前尚未完成：

- 使用正式金鑰進行三個外部服務的整組聯測，以及由第 1 人部署。

> 本機預設使用 mock 摘要與分數，可在無金鑰時測試配對、表格與 CSV。關閉模擬模式並按「開始比對」會執行 A、B 兩種方法。

## 本機啟動

PowerShell：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

也可以雙擊 `啟動網站.cmd`。若尚未建立虛擬環境，請用 Python 3.11 或以上執行 `python -m venv .venv`，再以 `.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt` 安裝依賴。

### 實際執行方法 A 與方法 B

從本專案的 `.streamlit/secrets.toml` 設定金鑰。部署後由第 1 人在 Streamlit 的 Secrets 設定同名欄位。

```toml
OPENROUTER_API_KEY = ""
S2_API_KEY = ""
OPENALEX_API_KEY = ""
```

將金鑰填入雙引號內，重啟網站，關閉「開發測試模式」，再用公開範例按「開始比對」。A、B 共用 OpenRouter Key。方法 A 每個配對先問篇名，相關或不確定才查摘要並問支持度。來源查詢有設定金鑰時會使用金鑰，缺少時仍可按資料庫的無金鑰額度嘗試。`.streamlit/secrets.toml` 已列入 `.gitignore`。

方法 B 使用已核對的 `openai/gpt-4.1-mini`。每次請求停用 OpenRouter 的搜尋外掛，僅送出貼上的段落、書目與既有配對編號。若 `OPENROUTER_METHOD_B_MODEL` 設定為其他型號，該次暫不評分。模型依照對應的書目直接給分，網站依共用門檻分級。回覆回報外部工具使用、缺列、格式錯誤或服務中斷時，B 分數留空並記下判斷狀態。CSV 的「方法 B 查核紀錄」包含模型、請求編號及原始分數。

同一個網站程序的所有使用階段共用 Semantic Scholar 的請求間隔與冷卻時間。遇到 HTTP 429、查無、逾時或沒有可用摘要時，依書目識別碼使用適用的官方來源、Crossref、Europe PMC 或 OpenAlex 備援。冷卻時間依 `Retry-After`，缺少此標頭時採用八秒。CSV 的「摘要查詢紀錄」保留切換原因與摘要核對結果，「摘要 HTTP 請求紀錄」保留逐次狀態及可公開的回應欄位。所有摘要來源都查詢失敗時，若篇名已通過 Q1，Q2 改用書目中的篇名，並保留摘要查詢失敗的狀態。備援、診斷和驗收方式見 [Semantic Scholar 限流與備援驗收](docs/semantic-scholar-429.md)。

同一網站工作階段再次比對同一筆書目時，會沿用先前由 Semantic Scholar 取得的摘要。OpenAlex 的備援結果只在當次使用，之後仍可向 Semantic Scholar 查詢。結果表和 CSV 都顯示實際使用的摘要來源。

## 測試

```powershell
.\.venv\Scripts\python.exe -m pytest
```

測試涵蓋 IEEE 配對、摘要查詢順序、Jev 回應驗證、分數與三級邊界、Streamlit 入口及 CSV。Jev 測試以模擬 HTTP 回應執行。

## 第二、三人串接介面

串接契約定義於 `src/adapters.py`，網站透過 `src/pipeline.py` 呼叫，不直接依賴組員的實作。

第二人需實作 `ReferenceLookupAdapter.lookup(pair)`，回傳 `ReferenceLookupResult`：

- `ref_number`
- `title`
- `doi`
- `url`
- `abstract`
- `source_status`
- `error_message`

網站在 `app.py` 的 `analyze_pairs()` 建立第 2 人的 `ReferenceLookupAdapter`，再交給同一條流程。

```python
rows = run_method_a_pipeline(
    pairs,
    reference_adapter,
    OpenRouterJevAdapter(api_key),
)
```

- 流程先呼叫第 2 人的 `extract_reference_metadata(pair.ref_raw)` 取得篇名與書目資料，將來源狀態設為待查詢。Q1 選「相關」或「不確定」後才執行 `reference_adapter.lookup(pair)`。
- 查詢回傳相同的 ref 編號、摘要與來源狀態。查無相符資料使用 `SourceStatus.NOT_FOUND`，外部請求失敗使用 `SourceStatus.LOOKUP_FAILED`。篇名通過 Q1 後，摘要查無、缺少或查詢失敗時，都可用書目中的篇名執行 Q2，來源狀態仍如實呈現。
- `OpenRouterJevAdapter.judge(pair, reference)` 也可直接接受已取得的摘要，用於測試單筆。正式網站串接採上方分階段的方式，讓 Q1 停止時省略摘要查詢。
- 依照共用格式，方法 A 的「證據層級」填支持／不確定／不支持，「判斷依據」填篇名／摘要。Q1 判不相關時目前採用空白分數和「Q1 停止」狀態。Q2 使用 Jev 的 `noul` 回應，再按原型門檻分級。CSV 與畫面共用同一份結果。
- 每個結果物件保留書目與實際取得的摘要、Q1 機率和信心值、Q2 原始數值，以及模型與請求識別碼。CSV 最後的「方法 A 查核紀錄」會保留 Q2 送出的證據文字及模型資訊，便於比對兩個資料庫交回的摘要是否有相同內容。

### 被引文獻全文取得（第二人，`src/fulltext_sources.py`）

依汪老師 11/24 第 2 點加入。`FullTextFetcher.fetch(meta)` 依識別碼分三條路取開放存取全文並轉成 Markdown：arXiv ID 走 arXiv 官方 HTML 版（沒有才退 PDF）、PubMed Central 走 Europe PMC 的 JATS XML、其餘用 OpenAlex 的 `best_oa_location` 與 `locations`，**先試出版社頁面的 HTML，再試 PDF**（分工要求優先使用 HTML、XML；HTML 不經過版面還原，比從 PDF 轉準確。頁面只有書目時會被 `TOO_SHORT` 擋下，再往下試 PDF）。手動上傳走 `fetch_file(path)`，支援 `.md`、`.xml`、`.html`、`.pdf`。

回傳的 `FullTextResult` 帶 `source`（哪一條路）與 `converter`（哪個轉檔器），評估時要靠這兩欄分辨「判斷錯」和「證據沒拿到」。狀態分開記，不可混成一種失敗：

- `GOT_FULL_TEXT`：拿到全文
- `NO_OPEN_ACCESS`：查得到這篇，但沒有開放存取全文
- `FETCH_FAILED`：連線、限流或 HTTP 錯誤，**不代表沒有全文**
- `CONVERT_FAILED`：檔案拿到了但轉不出文字
- `NO_CONVERTER`：本機沒裝對應的轉檔工具，**不是文獻的問題**
- `NO_IDENTIFIER`：書目沒有 DOI、arXiv ID 也沒有篇名

轉檔器是選用相依，沒裝也不會讓程式壞掉：HTML 有裝 `trafilatura` 就用它，沒有就用內建的簡化解析；PDF 需要 `docling`，沒裝會回 `NO_CONVERTER`。JATS XML 由本模組自行解析，只用標準函式庫。兩者都不在 `requirements.txt`，要用請裝 `requirements-fulltext.txt`。實測同一篇 arXiv 論文，內建解析會把網頁橫幅（「arXiv is now an independent nonprofit」之類）混進正文，`trafilatura` 不會，建議裝。

只有篇名、沒有 DOI 或 arXiv ID 時，OpenAlex 的 `search` 是模糊比對，一定會回「最接近的」而不是「相符的」。本模組沿用 `reference_lookup` 的 `title_similarity` 與 `TITLE_MATCH` 門檻核對 `display_name`，不符就不採用，避免拿到別篇論文的全文——那會讓判斷出現假的「引用與來源不符」。兩條路（摘要、全文）的證據標準必須一致。

`FullTextCache` 會把轉好的 Markdown 存成檔案，**預設就是開的**（資料夾 `.cache/fulltext`，已列入 `.gitignore`），同一份全文給方法 A 和方法 B 共用，重跑評估也不會重新下載。只快取成功的結果，因為限流是暫時的，存下來會讓下次永遠失敗；快取檔含原始鍵（`doi:`／`arxiv:`／`title:`），只有篇名時鍵會併上第一作者與年份，避免同名不同篇共用。要關掉就明寫 `cache=None`（單元測試用）。OpenAlex 免費金鑰每天 US$1＝10,000 credits，用 DOI 直查 0 credits、用篇名查每次 10 credits，一天只有 1,000 次。

目前這一層尚未接進 `pipeline.py`；把全文證據送進判斷流程是第三人的工作項目。

### 證據解析入口（第二人，`src/source_evidence.py`）

分工寫的是一句話：「自動搜尋並取得被引文獻的全文（找不到則取摘要）」。所以第二人交出去的是**一份證據**，不是兩個模組。`EvidenceResolver.resolve(pair)` 依現行架構第 3 點的順序回傳 `SourceEvidence`：

1. 開放存取全文 → `EvidenceBasis.FULL_TEXT`
2. 拿不到全文就查摘要 → `EvidenceBasis.ABSTRACT`
3. 兩邊都沒有 → `EvidenceBasis.UNAVAILABLE`，`unverifiable` 為真，由判斷端標「無法查核」、不給分數

```python
resolver = EvidenceResolver.from_keys(
    openalex_key=st.secrets.get("OPENALEX_API_KEY"),
    s2_key=st.secrets.get("S2_API_KEY"),
)
evidence = resolver.resolve(pair)   # 方法 A
evidence = resolver.resolve(pair)   # 方法 B 拿到同一份，不會再查一次
```

- **同一份書目只解析一次**，鍵是書目（DOI／arXiv ID／篇名＋第一作者＋年份），**不是 ref 編號**——不同論文的 `[1]` 是不同文獻，用編號當鍵會把別篇的全文拿來比對。方法 A 與方法 B 拿到的證據 `==` 相等。
- 失敗結果也會被記住（否則 A、B 會各查一次，證據就不同了）。限流或連線中斷後要重試，先呼叫 `clear()`；全文的磁碟快取本來就不存失敗結果。
- 正式接線請把 app 的共用物件傳進 `from_keys(http=..., scholar_cache=...)`，否則 Semantic Scholar 的 429 冷卻與已取得的摘要不會跨 rerun 共用。
- 拿到全文就**不會**再呼叫查摘要，省下 Semantic Scholar 的額度（有金鑰也只有 1 RPS）。
- `note` 同時寫出全文與摘要各自為什麼沒拿到，分開記：下載失敗、限流、沒有開放存取、缺轉檔工具、文獻查不到是五件不同的事，評估腳本要分得開。
- `resolve()` 不會拋例外；取全文或查摘要發生未預期錯誤時，回傳的 `note` 會寫出例外型別。
- 本模組不產生 `SupportBand.UNVERIFIABLE`（「無法查核」）這個展示標籤，它在 `fix/unverifiable-shared-evidence` 分支上。這裡只誠實回報有沒有證據。

### 從全文找出與引用句相關的證據（第二人，`src/evidence_retrieval.py`）

做法依**汪老師 11/10 會後補充第 10 點**：全文轉 Markdown 後，列出全文中含引用句「關鍵詞」的句子交給判斷端。11/24 改成允許整篇全文之後要不要沿用屬【組內定】，所以做成獨立一層，用不用由呼叫端決定。

需要這一層的理由：實測一篇全文 26,000–37,000 字，整篇送進去又貴又稀釋重點，Jev 更是只能從選項中選答案。

`find_evidence(claim, source_markdown)` 回傳 `RetrievalResult`，狀態分六種，不可以混在一起：

- `FOUND`：找到含關鍵詞的句子
- `WEAK_MATCH`：只命中 improved、order 這類泛用字詞，主張的核心概念整篇都沒出現。段落照樣附上給人看，但**不算找到證據**。短引用句另有命中數下限，否則兩個關鍵詞命中一個就達到 50%
- `NO_RELEVANT_PASSAGE`：來源有拿到也切得出句子，但一個關鍵詞都沒命中。這要不要算「無法查核」屬【組內定】，本模組不替它下結論
- `UNPARSEABLE_SOURCE`：來源有字但切不出句子，是解析失敗，不是沒有來源
- `NO_SOURCE_TEXT` / `NO_KEYWORDS`：前置條件不足，不是「來源不支持」

幾個刻意的設計：

- **References、致謝、利益衝突這些段落會排除。** 被引文獻自己的參考文獻清單裡全是別人的篇名，那些詞在該文最罕見，idf 會把它推上第一名——等於拿第三篇論文的篇名當來源內容。
- **逐字引號優先，而且找不到會記下來。** 有命中的句子用排序鍵保證排在最前面（固定加分擋不住 idf）。引號在來源完全找不到時填進 `unmatched_quotes`，`has_misquote_signal` 為真——誤引是本研究最好抓的一類錯誤，不可以讓它消失在欄位之間。實測一句捏造的引號，真論文上正確回報誤引訊號。
- **送出去的節錄是來源原文逐字**，包含 `Fig. 3`、`Dr. Smith` 的大小寫。驗證引用忠實度的系統自己改動來源文字無法解釋。
- **表格每一列各自成句。** 整張表併成一句時，「GPT-4 是 18%」那一列會和別列的 55% 黏在一起，反而變成錯誤主張的支持證據。
- `missing_keywords`（關鍵詞整篇都沒出現）是訊號。敘述式引用的作者姓氏、轉述框架（prior work、reports、according to）都不算關鍵詞，否則會污染這一欄並推高誤判。

排序用 idf 加權，整篇都有的詞不會蓋過真正的關鍵詞。只用標準函式庫，沒有額外相依。

⚠️ **接線前要先決定**：`src/jev_adapter.py` 已經有另一套 `_find_evidence_excerpt`（自己的斷句、只吃摘要、由 Jev 從選項中挑一句）。兩套並存會變成「Jev 和一般 LLM 拿到的證據不一樣」，那正是本專案列管的問題。要留哪一套由組內決定。

判斷端用一個方法就好：

```python
evidence, retrieval = resolver.retrieve(pair)
```

回傳 `(證據, 檢索結果)`：前者說證據是全文還是摘要、拿不拿得到；後者說在裡面找到什麼。

### 兩段式證據挑選（第二人，`src/evidence_selection.py`）

把兩套既有做法合起來，不是取代誰：

1. **關鍵詞收斂**（`evidence_retrieval`，汪老師 11/10 第 10 點）：用引用句的關鍵詞把來源縮成候選句。純字串比對，不花錢、可重現、全文或摘要都能吃。
2. **語意挑選**（`jev_adapter._find_evidence_excerpt` 的做法）：讓決策模型從候選句裡挑出真正記錄了主張中具體事實的那一句，或回答 `none`。這是字串比對做不到的。

各自原本的限制，合起來就沒有了：

| 原本的限制 | 合併後 |
|---|---|
| 語意挑選寫死只吃摘要（`evidence_basis is ABSTRACT`），改用全文後永遠不會觸發 | 第一段先收斂，全文 149 句照樣能挑 |
| 語意挑選超過 12 句就放棄 | 候選數由 `top_k` 控制，結構上不會超過 |
| 語意挑選只掛在方法 A，方法 B 沒有 | 候選由第一段產生，A、B 吃同一份 |
| 關鍵詞檢索只有字串層，判不出語意 | 第二段補上語意判斷 |

```python
# 方法 A 傳 Jev 的 _ask，方法 B 傳一般 LLM 的呼叫，候選句仍是同一份
selected = select_evidence(claim, evidence.text, chooser=CallableChooser(ask=jev_ask))
judge(selected.text_for_judgement())
```

- **第二段是選用的。** 不給 `chooser` 就只跑第一段，結果照樣可用。
- **第二段失敗（限流、回傳未定義選項）一律保留第一段的候選並記下原因。** 不可以因為模型沒答就說沒有證據，那是把「模型沒答」寫成「來源不支持」。
- 模型回答 `none`（候選都不構成證據）與第一段找不到候選是兩件事，狀態分開記。
- 候選會先去重：同一句常常同時出現在 Abstract 和 Discussion，送兩個一樣的選項給模型是浪費。
- 弱相符與「逐字引號在來源找不到」的警示，在模型挑完之後仍然保留在 `note` 裡。

實測一篇 33,967 字的真論文：149 句 → 5 個候選 → 模型挑一句，原本的 12 句上限在結構上不會再碰到。

## 已驗收案例

- 單篇 `[79]`
- 多篇 `[47], [107], [109], [110]`
- 連續範圍 `[3]-[6]`
- 跳過未引用的 ref
- 空白輸入、無引用、缺少 ref、重複引用
- 線上 CSV 下載、UTF-8 中文欄名及一個來源一列

## 資料與研究限制

- 第一階段限定人工貼上含 IEEE 引用編號的段落及完整 ref。
- 來源可查證狀態、證據層級與內容支持結果分開保存。
- 三級分數門檻是目前 prototype 的暫定工程設定，並非已驗證的研究分類。
- mock data 僅供介面與串接開發，不代表模型實際輸出。
