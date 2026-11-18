# Citation Verification Prototype

本專案是「未獲來源支持的引用」查核系統之第一階段 Streamlit 原型。

目前完成：

- 最小 Streamlit 輸入與結果頁。
- IEEE 單篇、多篇並列及連續範圍引用擷取。
- 引用編號與使用者貼上的 ref 配對。
- 自動跳過未被引用的 ref。
- 缺少 ref 時保留錯誤狀態，不中斷整批處理。
- 一個引用來源顯示一列的固定輸出表格。
- 方法 A 欄位與暫時留白的方法 B 欄位。
- 支持、不確定、不支持的色彩標籤。
- UTF-8 CSV 匯出。
- 第二人書目／摘要查詢 adapter 與第三人 Jev adapter 介面。
- 清楚標示的 mock 摘要與 Jev fixture，可在未串接 API 時測試網站。
- 空白輸入、無引用、缺少 ref 與重複引用的錯誤／警告處理。
- 可供三人共用的資料型別。
- 固定 mock data 與 CSV 匯出示範。
- 暫定支持分數門檻的邊界測試。

目前尚未完成：

- 文獻查詢與摘要取得。
- Jev 判斷。
- 方法 B。

## 本機啟動

PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m streamlit run app.py
```

## 測試

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest
```

## 資料與研究限制

- 第一階段限定人工貼上含 IEEE 引用編號的段落及完整 ref。
- 來源可查證狀態、證據層級與內容支持結果分開保存。
- 三級分數門檻是目前 prototype 的暫定工程設定，並非已驗證的研究分類。
- mock data 僅供介面與串接開發，不代表模型實際輸出。
