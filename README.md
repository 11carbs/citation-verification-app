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
