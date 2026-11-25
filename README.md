# Citation Verification Prototype

## 第二階段開發：文件轉換（開發分支）

新增側欄「Document conversion」頁面，接收 PDF、DOCX、HTML、XML、MD、TXT，提供 Markdown 預覽與文件結構 JSON 下載。這是轉換基礎版，尚未自動抽取整篇引用，也尚未支援 OCR。舊版 DOC 不在本版支援範圍，請先另存為 DOCX。
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
