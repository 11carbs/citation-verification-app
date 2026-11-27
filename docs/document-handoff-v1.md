# 文件轉換與第二人交接契約 v1（待團隊確認）

基準：origin/main 的 4a755d4；開發分支 feature/document-conversion-v1。
本文件是第一人的實作提案，尚未表示第二人已確認。既有 CitationPair、ReferenceLookupResult 介面不變。

## 文件轉換

共用入口：`convert_document(data: bytes, filename: str) -> ConvertedDocument`。
上傳入口只支援 PDF、DOCX，單檔上限 30 MiB。HTML、XML、MD、TXT 與舊版 DOC 均不支援上傳；舊版 DOC 請另存為 DOCX。Markdown 是轉換後的內部資料與下載格式，不是上傳格式。
來源論文與被引全文可共用本入口；第二人另負責來源取得與證據檢索。
第二階段頁面僅供上傳 PDF／DOCX。`convert_pasted_paper(text)` 只供程式內部解析測試建立文字樣本，不是網站入口。現有首頁的 IEEE 段落＋手貼 ref 仍屬第一階段，不表示整篇自動配對或所有引用樣式已完成。

`ConvertedDocument.to_dict()` 輸出 schema_version=document.v1，包含 document_id、filename、source_format、markdown、blocks、warnings、converter。
每個 block 有 block_id、markdown、locator、page、bbox。非 PDF 不虛構頁碼。
document_id 依內容雜湊固定；引用配對的 block_ids 連回本結構。原始檔需由呼叫端保存。

## 引用交接（代表性數字型與作者年份型）

`src/whole_paper.py` 的 `extract_whole_paper(document)` 使用轉換後的文字區塊，找出獨立的 References／Bibliography／Works Cited／參考文獻標題，區分正文與 ref 區。支援 `[1]`、`[1, 3]`、`[1]-[3]` 等數字型，以及 `(Wang, 2024)`、`Wang (2024)`、`(Li & Chen, 2023; Wang, 2024)` 等代表性作者年份型。結果在側欄頁面預覽及下載 JSON；找不到參考文獻區會回報狀態。
另支援代表性的數字上標 `^{1}`、Vancouver 式 `(1, 2)`、DOCX 註腳／尾註 `[^fn2]`／`[^en3]`。註腳配對到的是原文件中的註文，不等於已查證獨立來源全文。PDF／DOCX 的實際覆蓋與限制見[文件結構檢查](format-structure-audit.md)。

`src/document_handoff.py` 定義 CitationHandoff 與 ReferenceCandidate。
schema_version=citation-handoff.v1，每筆包含：

- document_id、claim_id、pair_id、citation_group_id
- claim_text、context、citation_marker、block_ids
- source_locations：陳述原文的 block_id、locator、區塊內行號；PDF 另有 page、bbox。paragraph_char_start／paragraph_char_end 是正規化後段落內的零起算字元位置。
- match_status：matched / missing_reference / ambiguous / unparsed
- reference：ref_id、raw_text、title、authors、year、doi、url、source_locations（ref 原文位置）
- candidate_ref_ids、error_message

ref_id 為字串，不要求數字編號，以容納作者年份、註釋等引用。
新頁面的「ref ID（系統）」是內部識別碼；「原文編號」只用於論文本身有 `[1]` 等數字編號的格式。作者年份型的原文編號空白是正常情況。未配對項目的 ref ID 與被引文獻空白，需看 `match_status` 與 `error_message`。
同一主張的多來源共用 claim_id 和 citation_group_id，各有獨立 pair_id。
同一來源出現在不同主張時，共用 ref_id、保留各自主張配對。
只有 matched 的項目送入來源查詢；ambiguous 保留至少兩個候選，不任選其一。
缺少 ref 使用 missing_reference；重複編號 ref 產生不同候選 ID，標記 ambiguous；能看出像引用但無法安全解析時標記 unparsed。同一主張重複引用同一來源會合併一筆，另給警告。
沒有取得 metadata 時使用空欄位，不填造假的 DOI 或篇名。

第二人需要確認：欄位命名、ref_id／block_id 的保存方式、缺失及歧義狀態；確認後才將此契約視為定案。

### 可下載的批次交接檔

`src/handoff_bundle.py` 的 `build_handoff_bundle(document, outcome)` 會驗證欄位與原文位置，再產生 `citation-batch.v1` JSON。側欄頁面另有同一批配對的預覽表與 UTF-8 BOM CSV，供人工檢查。

| 欄位 | 用途 |
| --- | --- |
| `document_id`、`filename`、`source_format` | 對應輸入的待查核論文；網站上傳產生的 `source_format` 僅為 `pdf` 或 `docx` |
| `ready_pairs` | 僅含 `matched`；第二人可依每筆 `reference.raw_text`／DOI 查指定來源 |
| `review_pairs` | `missing_reference`、`ambiguous`、`unparsed`；先供人工核對，不進來源查詢 |
| `references` | 只保留被 ready pair 或歧義候選引用的 ref；未引用條目不交給第二人 |
| `issues` | 擷取階段的警告，例如重複引用或無法解析標記 |

每筆 pair 都有 `pair_id`、`claim_id`、`citation_group_id`、原引用陳述、引用標記、配對狀態、原文位置及 ref。相同 `claim_id` 可有多個 pair，但原陳述與原文區塊必須一致。相同 `ref_id` 可被不同 `claim_id` 重複引用。交接前檢查文件 ID、唯一 pair/ref ID、來源區塊、文字範圍、候選 ID 與狀態的一致性；驗證失敗時頁面不提供交接檔。
`matched` 只表示文內引用找到一筆文末書目，並不保證該書目真實存在、全文可取得或內容支持主張。第二人須回傳其實際查到的來源身分、全文／摘要／metadata 證據層級與查詢狀態；第三人再依同一筆 `pair_id` 與實際證據判斷支持程度。兩人的新版輸出格式仍待團隊共同確認。

現有 `src/adapters.py` 的 `ReferenceLookupAdapter.lookup(CitationPair)` 與 `ReferenceLookupResult.ref_number` 仍只支援數字編號。它們屬第一階段原型，**不能直接接收** `citation-batch.v1` 的作者年份、註腳或結構化 ID。第二人需確認新版輸入及全文／摘要／證據回傳格式後，才能將來源查詢接進新頁面；本次只交付可核對的輸入資料。

## 本版限制

- 本版已加入部分上標、腳註／尾註及期刊變體，仍未處理所有期刊版式或 OCR。無法辨識的引用樣式可能不產生配對，需擴充真實論文測例與解析器。
- PDF 使用文字區塊抽取，保留 bbox／頁碼；多欄、表格、公式與掃描頁須檢查。沒有文字會回報錯誤，部分空白頁會警告。
- DOCX 的註腳與尾註文字可由 Word XML 取得，但頁首頁尾、文字方塊、複雜欄位及精細字體標記尚未完整支援。
- Markdown 是統一文字表示，不保證版面重現；本版不接受 Markdown 檔案上傳。
