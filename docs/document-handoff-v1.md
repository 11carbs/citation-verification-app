# 文件轉換與第二人交接契約 v1（待團隊確認）

基準：origin/main 的 4a755d4；開發分支 feature/document-conversion-v1。
本文件是第一人的實作提案，尚未表示第二人已確認。既有 CitationPair、ReferenceLookupResult 介面不變。

## 文件轉換

共用入口：`convert_document(data: bytes, filename: str) -> ConvertedDocument`。
支援副檔名 PDF、DOCX、HTML、XML、MD、TXT，單檔上限 30 MiB。舊版 DOC 不支援；請另存為 DOCX。
來源論文與被引全文可共用本入口；第二人另負責來源取得與證據檢索。
第二階段也有「貼上論文全文」入口；它呼叫 `convert_pasted_paper(text)`，再由同一個 `convert_document` 產生 Markdown／結構資料。文字輸入採 Markdown 文字處理，需包含正文和文末參考文獻。現有首頁的 IEEE 段落＋手貼 ref 仍屬第一階段，不表示整篇自動配對或所有引用樣式已完成。

`ConvertedDocument.to_dict()` 輸出 schema_version=document.v1，包含 document_id、filename、source_format、markdown、blocks、warnings、converter。
每個 block 有 block_id、markdown、locator、page、bbox。非 PDF 不虛構頁碼。
document_id 依內容雜湊固定；引用配對的 block_ids 連回本結構。原始檔需由呼叫端保存。

## 引用交接（代表性數字型與作者年份型）

`src/whole_paper.py` 的 `extract_whole_paper(document)` 使用轉換後的文字區塊，找出獨立的 References／Bibliography／Works Cited／參考文獻標題，區分正文與 ref 區。支援 `[1]`、`[1, 3]`、`[1]-[3]` 等數字型，以及 `(Wang, 2024)`、`Wang (2024)`、`(Li & Chen, 2023; Wang, 2024)` 等代表性作者年份型。結果在側欄頁面預覽及下載 JSON；找不到參考文獻區會回報狀態。

`src/document_handoff.py` 定義 CitationHandoff 與 ReferenceCandidate。
schema_version=citation-handoff.v1，每筆包含：

- document_id、claim_id、pair_id、citation_group_id
- claim_text、context、citation_marker、block_ids
- match_status：matched / missing_reference / ambiguous / unparsed
- reference：ref_id、raw_text、title、authors、year、doi、url
- candidate_ref_ids、error_message

ref_id 為字串，不要求數字編號，以容納作者年份、註釋等引用。
同一主張的多來源共用 claim_id 和 citation_group_id，各有獨立 pair_id。
同一來源出現在不同主張時，共用 ref_id、保留各自主張配對。
只有 matched 的項目送入來源查詢；ambiguous 保留至少兩個候選，不任選其一。
沒有取得 metadata 時使用空欄位，不填造假的 DOI 或篇名。

第二人需要確認：欄位命名、ref_id／block_id 的保存方式、缺失及歧義狀態；確認後才將此契約視為定案。

## 本版限制

- 本版只完成代表性數字型與作者年份型的整篇引用／ref 抽取，尚未處理上標、腳註／尾註、所有期刊變體或 OCR。無法辨識的引用樣式可能不產生配對，需擴充測例與解析器。
- PDF 使用文字區塊抽取，保留 bbox／頁碼；多欄、表格、公式與掃描頁須檢查。沒有文字會回報錯誤，部分空白頁會警告。
- DOCX 的腳註、尾註、文字方塊及精細字體標記尚未完整支援，不能宣稱已支援所有引用樣式。
- HTML 轉 Markdown；XML 採結構化文字路徑，尚未針對各出版商 schema 完整轉換引用屬性。
- TXT/MD 保留整份內容，尚未細分定位；Markdown 是統一文字表示，不保證版面重現。
