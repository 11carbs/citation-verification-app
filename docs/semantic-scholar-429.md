# Semantic Scholar 限流與備援驗收

## 受控診斷

一次執行只傳一個 GET。程式記錄本機或已知網路出口的標籤、UTC、端點、是否帶金鑰、實際準備請求中的金鑰是否和設定一致、狀態碼、延遲、限流標頭、閘道 Request ID 和 429 回應正文的去敏節錄。`sent` 表示發起 HTTP 請求，連線失敗時仍要看錯誤欄才能知道是否收到服務端回應。

```powershell
.\.venv\Scripts\python.exe -m scripts.diagnose_scholar --source windows-local-fixed-network --endpoint paper --output output/diagnostics.jsonl
```

- `--endpoint protected` 查官方要求金鑰的資料集資訊，只記錄狀態，不會下載資料集。回 `200` 表示金鑰在該時間通過驗證。公開論文端點的 `200` 不能單獨證明金鑰有效。
- `--anonymous-control` 是公開論文端點的單筆對照。限流期間停手，遵守 `Retry-After`，不要改成匿名請求繞過限制。沒有標頭時，隔開時間再做獨立診斷。
- 比較金鑰方案時固定出口、端點與請求內容。若有經授權的另一個測試金鑰，需確認兩把金鑰的用量方案可比。
- 比較 IP 或路徑時固定金鑰、端點與請求內容，只改網路，用不同 `--source`。需要另一個已獲授權、出口可辨識的環境。不同網路的結果差異仍可能受時間與閘道路由影響。
- 若連需要金鑰的端點也回 `429`，保存 Request ID 與時間交給 Semantic Scholar 核對。外部回應沒有指出觸發規則時，將原因記為未確認。

## 摘要備援與評分

1. 先用 Semantic Scholar。`429`、冷卻、網路錯誤或服務暫停時停止該筆的 Semantic Scholar 請求。`404`、沒有摘要、拒收候選與其他查詢失敗也進入同一套備援，保留逐次紀錄。
2. ACL DOI 可辨識 2020 年起以四位年份開頭的格式，也可辨識舊式一個字母、兩位年份和四位編碼的 ID。對應到 ACL Anthology 頁面後，核對頁面實際 DOI 和篇名，從官方摘要欄讀取。arXiv 路徑核對 ID、版本、篇名和適用的 arXiv DOI。正式出版 DOI 不會直接用不同版本的 arXiv 摘要代替。
3. 備援優先用適用的 ACL Anthology 或 arXiv 官方欄位。DOI 可用時，接著查 Crossref 的提交摘要與 Europe PMC 的書目摘要。前述來源都沒有可採用摘要時查 OpenAlex。有 DOI 或 arXiv ID 的 OpenAlex 候選須符合 DOI 版本及篇名；僅有篇名時須唯一且正規化後完全相符。
4. 摘要另外檢查狀態提示、明顯截斷及 OpenAlex 詞元位置缺漏。Semantic Scholar 摘要若可用同版本的 ACL Anthology 或 arXiv 官方文字核對，會記錄結果；不一致時採官方摘要。可用的索引摘要若缺少官方對照，紀錄會標示「完整性未經官方摘要核對」。摘要查詢失敗時保留來源狀態。**2026-11-27 起拿不到摘要就不執行 Q2**，列為「無法查核」、不給分數（汪老師 11/10 會後補充第 4 點）。舊的「摘要查詢失敗，依篇名評分」只在建 adapter 時傳 `title_only_scoring=True` 才會出現，正式流程不會。
5. CSV 保存摘要來源、來源網址、來源選擇原因及 HTTP 逐次紀錄。快取命中不重複記為網路請求。HTTP 紀錄不含金鑰值、URL 查詢字串或完整回應內容。

附有 W3C `/TR/` 官方網址的標準可直接查公告頁的 Abstract 區塊。書目包含英文 IEEE `Accessed` 日期時，使用 W3C 發布歷史中該日期或之前最近的公開版本，並核對篇名和明載的版號。無法確認版本或摘要時保持未評分。這條路不需要向學術索引查詢，也不採用規格全文作為證據。網站下方的「匯出 CSV」包含完整欄位。

## 固定論文驗收

```powershell
.\.venv\Scripts\python.exe -m scripts.acceptance_abstracts --output-dir output/abstract-acceptance
```

固定 22 筆，包含 10 篇 ACL Anthology 論文、10 篇 arXiv 預印本及 2 篇以 PubMed 為官方對照的期刊論文。只有 Semantic Scholar 的 429 在本機注入，備援來源均實際發送 HTTP。ACL 對照其官方 XML，arXiv 對照頁面可見摘要，期刊論文對照 NCBI 的 PubMed XML。比對前統一大小寫、標點、連結和等義的乘號表示。保存文字指紋而非複製全文。

`acceptance.csv` 逐筆列出官方紀錄、備援來源、摘要比對、失敗原因、發起請求數和時間。`acceptance_trace.jsonl` 保留逐次請求，`summary.json` 保存取得率與耗時。單筆耗時涵蓋備援查詢與程序內等待，整批耗時另包含官方對照資料的下載。這批資料衡量有官方摘要且可識別的文獻在限流時的取得能力。

### 備援流程矩陣

```powershell
.\.venv\Scripts\python.exe -m scripts.acceptance_fallback_matrix --output-dir output/fallback-matrix
```

六篇書目各測正常回應、注入 429、注入無摘要、以及注入查無／書目不符。正常情境優先重播一次 Semantic Scholar 批次請求取得的原始回應。批次無法取得個別文獻資料時，使用該文獻已實際找回的備援摘要測受控 200，並個別標記。429 和無摘要情境的備援走真實 HTTP。負向對照的回應皆由本機提供。每個情境獨立建立查詢實例與空快取；JSONL 以 `sent` 與 `transport` 區分網路請求和重播或注入。負向對照的通過條件是拒絕錯誤候選、沒有取得摘要。
