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
另支援代表性的數字上標 `^{1}`、Vancouver 式 `(1, 2)`、DOCX 註腳／尾註 `[^fn2]`／`[^en3]`、HTML 註腳連結及 JATS XML 的 `xref`／`rid`。註腳配對到的是原文件中的註文，不等於已查證獨立來源全文。PDF 等格式的實際覆蓋與限制見[文件結構檢查](format-structure-audit.md)。

`src/document_handoff.py` 定義 CitationHandoff 與 ReferenceCandidate。
schema_version=citation-handoff.v1，每筆包含：

- document_id、claim_id、pair_id、citation_group_id
- claim_text、context、citation_marker、block_ids
- source_locations：陳述原文的 block_id、locator、區塊內行號；PDF 另有 page、bbox。paragraph_char_start／paragraph_char_end 是正規化後段落內的零起算字元位置。
- match_status：matched / missing_reference / ambiguous / unparsed
- reference：ref_id、raw_text、title、authors、year、doi、url、source_locations（ref 原文位置）
- candidate_ref_ids、error_message

ref_id 為字串，不要求數字編號，以容納作者年份、註釋等引用。
同一主張的多來源共用 claim_id 和 citation_group_id，各有獨立 pair_id。
同一來源出現在不同主張時，共用 ref_id、保留各自主張配對。
只有 matched 的項目送入來源查詢；ambiguous 保留至少兩個候選，不任選其一。
缺少 ref 使用 missing_reference；重複編號 ref 產生不同候選 ID，標記 ambiguous；能看出像引用但無法安全解析時標記 unparsed。同一主張重複引用同一來源會合併一筆，另給警告。
沒有取得 metadata 時使用空欄位，不填造假的 DOI 或篇名。

第二人需要確認：欄位命名、ref_id／block_id 的保存方式、缺失及歧義狀態；確認後才將此契約視為定案。

## 本版限制

- 本版已加入部分上標、腳註／尾註及期刊變體，仍未處理所有期刊版式或 OCR。無法辨識的引用樣式可能不產生配對，需擴充真實論文測例與解析器。
- PDF 使用文字區塊抽取，保留 bbox／頁碼；多欄、表格、公式與掃描頁須檢查。沒有文字會回報錯誤，部分空白頁會警告。
- DOCX 的註腳與尾註文字可由 Word XML 取得，但頁首頁尾、文字方塊、複雜欄位及精細字體標記尚未完整支援。
- HTML 轉 Markdown 並保留常見數字上標連結；XML 採結構化文字路徑及部分 JATS `xref` 屬性，尚未涵蓋各出版商 schema。
- TXT/MD 保留整份內容，尚未細分定位；Markdown 是統一文字表示，不保證版面重現。
