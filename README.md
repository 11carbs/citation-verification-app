# Citation Verification Prototype

## 第二階段開發：文件轉換（開發分支）

新增側欄「Document conversion」頁面，只接受 PDF、DOCX 論文上傳（單檔 30 MiB 以下）。兩種檔案都轉成系統內部的 Markdown，並可下載 Markdown 與文件結構 JSON；空檔、無文字的掃描 PDF 與損壞檔案會顯示錯誤。HTML、XML、MD、TXT 與舊版 DOC 均不接受上傳。
此頁已能從整篇內容找出獨立的參考文獻區，擷取正文中的數字型與作者年份型引用，並逐篇配對至 ref；結果可預覽及下載 JSON。找不到、重複或有歧義的 ref 會保留狀態，不任意選一篇。此版只驗證了代表性寫法，尚未支援 OCR、來源全文檢索或支持程度判斷。
已加入可辨識的數字上標、註腳／尾註與部分期刊變體；PDF、DOCX 的代表性結構測例與限制見[文件結構檢查](docs/format-structure-audit.md)。這些測例不代表所有引用樣式或真實論文版面已完成驗收。
同一句多來源會建立各自的配對並共用主張 ID；同一 ref 出現在不同主張時保留每一次配對。輸出同時記錄引用陳述及 ref 的原文位置，文字檔含區塊內行號，PDF 另含頁碼與區塊座標；無法安全解析的標記會列為 unparsed，供人工確認。
新頁面可預覽配對，下載完整擷取結果 JSON、給第二人的 `citation-batch.v1` 交接 JSON，以及供人工核對的 CSV。交接 JSON 只把已配對項目放進待查詢清單；缺少 ref、歧義與無法解析的項目另列待核對，未引用的 ref 不送出。這是交接資料格式，尚未直接呼叫第二人的新版全文服務。
轉換後直接顯示引用配對結果；整篇 Markdown 不在畫面展開，可在頁面底部的「其他下載」取得。結果表保留原介面的方法 A／B 欄位，將原本誤稱為「證據層級」的欄名改為「支持分類（暫定）」；有實際分類時沿用綠／橙／紅顏色。新頁尚未執行來源查詢與支持判斷，分數及分類欄位保持空白，狀態標為「尚未執行」。參考文獻數是文末條目數；配對數是一個引用陳述對一篇來源的一筆關係，同一來源多次引用或一句引用多篇都會增加配對數。第二人交接 JSON 收在底部「其他下載」。
原首頁仍是第一階段的 IEEE 段落＋手貼 ref 介面；第二階段新流程在側欄頁面，不能把原首頁當成所有引用樣式已完成的證明。
與第二人的交接欄位提案及限制詳見 [文件交接契約](docs/document-handoff-v1.md)，尚待第二人確認。

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
Set-Location 'C:\Users\user\Desktop\研究方法\citation-verification-app'
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

依汪老師 11/24 第 2 點加入。`FullTextFetcher.fetch(meta)` 依識別碼分三條路取開放存取全文並轉成 Markdown：arXiv ID 走 arXiv 官方 HTML 版（沒有才退 PDF）、PubMed Central 走 Europe PMC 的 JATS XML、其餘用 OpenAlex 的 `best_oa_location` 與 `locations` 找 PDF 或出版社頁面。手動上傳走 `fetch_file(path)`，支援 `.md`、`.xml`、`.html`、`.pdf`。

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
