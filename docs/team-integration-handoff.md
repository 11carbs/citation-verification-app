# 第一人 → 第二、三人：整篇論文對接資料

## 目前版本與交件內容

- 開發分支：`feature/document-conversion-v1`。本文件隨該分支更新；公開網站的 `main` 可能較舊。
- 輸入：一份 PDF 或 DOCX（30 MiB 以下）。系統內部轉成 Markdown；使用者不能上傳 MD、TXT、HTML、XML 或舊 DOC。
- 第一人已完成：正文／參考文獻分區、引用陳述與文末 ref 配對、原文位置、結果預覽及下載。獨立章節標題不併入引用陳述。
- 第一人尚未完成：自動取得被引全文、來源存在性查證、證據檢索、Jev／LLM 支持判斷。結果表的 A／B 欄位目前保持空白。
- 對接時以網站產出的 `citation-batch.v1` JSON 為主；如需核對原文位置，可另下載 `document.v1` JSON。CSV 供人工檢視，不能代替 JSON 契約。

本次先把**這份對接規格**交給組員確認。不需要提供 SciCiteVal 或其他論文的範例包，也不需要先上傳原始論文。網站的「其他下載」可在實際處理論文後下載交接 JSON。**第二人應以 JSON 為串接依據，不應按畫面表格欄位位置讀取。**

## 第一人的正式輸出：`citation-batch.v1`

| 頂層欄位 | 意義 |
| --- | --- |
| `schema_version` | 固定為 `citation-batch.v1`。不相容變更需升版。 |
| `document_id`、`filename`、`source_format` | 待查核論文 ID、檔名與 `pdf`／`docx`。`document_id` 由檔案內容雜湊產生。 |
| `references` | 已被引用或歧義候選的文末書目；未被引用的條目不在此清單。 |
| `ready_pairs` | `match_status=matched`，每筆已找到唯一文末 ref，可交第二人查指定來源。 |
| `review_pairs` | 缺失、歧義或無法解析，先人工核對，不能直接查來源後做支持判斷。 |
| `issues` | 解析時的警告，含 `code` 與 `message`。 |

`ready_pairs`、`review_pairs` 的每筆均使用 `citation-handoff.v1`，包含：

| 欄位 | 意義與使用方式 |
| --- | --- |
| `pair_id` | **每筆 claim–source 關係的主鍵**。第二、三人回傳結果須原樣帶回。 |
| `claim_id`、`citation_group_id` | 同一引用陳述的一組來源共用這兩個 ID；一篇來源一筆 pair。 |
| `document_id` | 與頂層及 `document.v1.json` 相同。 |
| `claim_text`、`context` | 要查核的帶引用陳述及其所在段落上下文。無引用句不另立查核對象。 |
| `citation_marker` | 論文中的原始引用標記，例如 `[12]` 或 `(Wang, 2024)`。 |
| `block_ids`、`source_locations` | 陳述在待查核論文的區塊與位置。PDF 有頁碼與 bbox；行號從 1 起算。 |
| `paragraph_char_start`、`paragraph_char_end` | 在正規化後 `context` 中，`claim_text` 的 0 起算、結尾不含位置。 |
| `match_status` | `matched`、`missing_reference`、`ambiguous` 或 `unparsed`。此狀態只說明文內引用與書目能否配對，**不表示文獻真實或內容支持**。 |
| `reference` | `matched` 才有的唯一文末書目；其他狀態為 `null`。 |
| `candidate_ref_ids`、`error_message` | 歧義候選 ID 或待核對原因。 |

每筆 `reference` 包含 `ref_id`、`raw_text`、`title`、`authors`、`year`、`doi`、`url`、`source_locations`。`ref_id` 是**系統字串 ID**，作者年份型引用也有，不能假設它是數字。`title`、DOI 或 URL 無法從原文可靠擷取時是空值；第二人應以書目原文進一步查證，不可把空欄位當成不存在。

`source_locations` 的 `block_id` 指向 `document.v1.json` 中同名區塊；`locator` 是區塊原文位置，例如 `page:8/block:11`；`page` 為 PDF 的 1 起算頁碼，DOCX 不虛構頁碼；`bbox` 為 PDF 區塊座標。來源 ref 自身也有一組 `source_locations`。

## 狀態與一對多規則

| 狀態 | 第二人處理 |
| --- | --- |
| `matched` | 只查這筆 `reference` 指定的來源；先查身分與可取得內容，再回傳實際證據層級。 |
| `missing_reference` | 文內有引用，但文末無可對應條目；進人工核對，不自行搜尋替代文獻。 |
| `ambiguous` | 有多個候選 ref；查看 `candidate_ref_ids` 並人工消歧，不任選一筆。 |
| `unparsed` | 標記可能是引用，也可能是公式、註腳或未知格式；先核對原文。 |

同一句引多篇來源時，`claim_id` 相同、`pair_id` 不同；第二人逐篇查，第三人逐篇判斷，不把其中一篇的證據當作另一篇的證據。同一 ref 在不同陳述出現時可共用 `ref_id`，但仍各有 `pair_id`。需要呈現複合引用整體結論時，應另依 `citation_group_id` 彙整，且標明每篇來源支持哪部分。

## 串接步驟與邊界

1. 第二人讀取 `citation-batch.v1.json`，確認 `schema_version`，只遍歷 `ready_pairs`。每筆以 `pair_id`、`reference.ref_id` 與原始書目查指定來源。
2. 第二人回傳時至少保留 `pair_id`、`ref_id`、來源身分核對狀態、取得狀態、實際證據層級（全文／摘要／metadata）、來源網址或 DOI、可追溯的證據文字與位置、錯誤原因。**這是待團隊確認的回傳欄位提案，現有程式尚無此新版回傳 schema。**
3. 第三人只對實際取得的來源證據做支持判斷，回傳同一 `pair_id`、模型與 prompt 版本、分數／暫定分類、證據片段、位置及理由。只有摘要時不得把「摘要未提及」直接當成「全文不支持」。分類規則與標籤仍須團隊定案。
4. `review_pairs` 先由第一人或共同人工核對，再決定是否重跑配對；不得直接視為不存在、未獲支持或模型錯誤。

舊版 `src/adapters.py` 的 `ReferenceLookupAdapter.lookup(CitationPair)` 只接受數字 ref；**不能直接接收**新版作者年份、註腳或結構化 `ref_id`。第二人需新增讀取 `citation-batch.v1` 的介面，或先經團隊確認的轉接層。與第二人確認新版輸入及回傳資料後，才能讓新頁面顯示實際查詢和判斷；現在 A／B 空欄位不代表已串接。

## 產生與驗證

程式入口依序為 `convert_document(data, filename)` → `extract_whole_paper(document)` → `build_handoff_bundle(document, outcome)`。最後一步檢查文件 ID、配對與 ref ID、原文位置、字元範圍及一對多一致性；檢查失敗會拒絕產生交接檔。

實際處理論文時，可從 PDF／DOCX 上傳頁下載交接 JSON。完整自動化測試以 `python -m pytest -q` 執行。測試通過表示已覆蓋的案例運作正常，仍需以人工標註樣本衡量真實論文的引用配對正確率。

原始 PDF／DOCX、取得的來源全文與金標資料不自動放進 GitHub。團隊分享全文時應依各自合法持有與授權範圍處理。
