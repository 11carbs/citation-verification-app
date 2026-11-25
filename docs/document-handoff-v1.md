# 文件轉換與第二人交接契約 v1（待團隊確認）

基準：origin/main 的 4a755d4；開發分支 feature/document-conversion-v1。
本文件是第一人的實作提案，尚未表示第二人已確認。既有 CitationPair、ReferenceLookupResult 介面不變。

## 文件轉換

共用入口：`convert_document(data: bytes, filename: str) -> ConvertedDocument`。
支援副檔名 PDF、DOCX、DOC、HTML、XML、MD、TXT，單檔上限 30 MiB。
來源論文與被引全文可共用本入口；第二人另負責來源取得與證據檢索。

`ConvertedDocument.to_dict()` 輸出 schema_version=document.v1，包含 document_id、filename、source_format、markdown、blocks、warnings、converter。
每個 block 有 block_id、markdown、locator、page、bbox。非 PDF 不虛構頁碼。
document_id 依內容雜湊固定；引用配對的 block_ids 連回本結構。原始檔需由呼叫端保存。

## 引用交接（尚未實作整篇抽取器）

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

## DOC 與部署

DOC 使用 LibreOffice headless 轉 DOCX，再讀取本文及表格；每次以獨立暫存目錄及 user profile 執行，逾時 60 秒。
Community Cloud 的 packages.txt 已加入 libreoffice-writer；系統套件安裝方式參考：
https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies

本機未安裝 LibreOffice 時會回報 DOC_DEPENDENCY_MISSING。部署設定存在不代表雲端已安裝成功。
部署驗收需：doc_ready=true、實際二進位 DOC 成功轉換、確認文字與引用標記保留、連續／並行轉換互不干擾。

## 本版限制

- 本次僅完成文件轉換入口與契約，未做整篇引用／ref 抽取、作者年份配對或 OCR。
- PDF 使用文字區塊抽取，保留 bbox／頁碼；多欄、表格、公式與掃描頁須檢查。沒有文字會回報錯誤，部分空白頁會警告。
- DOCX/DOC 的腳註、尾註、文字方塊及精細字體標記尚未完整支援，不能宣稱已支援所有引用樣式。
- HTML 轉 Markdown；XML 採結構化文字路徑，尚未針對各出版商 schema 完整轉換引用屬性。
- TXT/MD 保留整份內容，尚未細分定位；Markdown 是統一文字表示，不保證版面重現。
- DOC 目前含模擬程序測試；若未執行真實 LibreOffice 測試，測試結果不得記為真實 DOC 驗收。
