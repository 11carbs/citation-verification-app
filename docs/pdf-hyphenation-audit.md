# PDF 跨行斷字修復與驗證

PDF 的排版斷字可能落在同一文字區塊的兩行，或相鄰的兩個文字區塊。轉換為 Markdown 時，程式會利用原始換行與區塊座標合併疑似斷字，再由同一份轉換結果產生 `claim_text`、`context` 和參考文獻 `raw_text`。`document_id` 仍由原始檔內容決定；修復後的文字與 `pair_id` 可能與舊輸出不同，先前下載的 JSON 應重新產生。

## 規則與例子

| PDF 抽出文字 | 修復後 | 理由 |
| --- | --- | --- |
| `af-` 換行 `fecting` | `affecting` | 排版斷字 |
| `dis-` 換行 `tort` | `distort` | 排版斷字 |
| `Ser-` 換區塊 `hii` | `Serhii` | 人名斷字；不依賴一般字典 |
| `Rad-` 換區塊 `hakrishna` | `Radhakrishna` | 人名斷字 |
| `Mistral-` 換行 `AI` | `Mistral-AI` | 型號原有連字號 |
| `four-` 換行 `label` | `four-label` | 複合詞原有連字號 |
| `Qwen3-` 換行 `8B` | `Qwen3-8B` | 型號原有連字號 |
| `SUP-` 換行 `PORTED` | `SUPPORTED` | 全大寫標籤的排版斷字 |

優先保留文件內其他地方已完整出現的連字詞、已知複合詞和型號／縮寫。其餘跨行碎片合併。相鄰區塊只有在同頁、上下緊貼且下一區塊內縮時合併；參考文獻在確認條目邊界後，還會修復該條目內未合併區塊之間的斷字。位置仍保留頁碼及區塊外框。此法無法保證所有罕見新詞與原有連字詞皆判對，尤其是沒有其他完整出現處的專有名詞。

## 實際論文抽查

使用 7 篇可解析的學術 PDF：CiteScreener、Phantom citations、SciCiteVal、SemanticCite、Valsci、SciFact 2020、L3CV。原始 PDF 文字共有 989 處「行尾連字號接下一行英數字」候選。逐篇重新轉換並建構引用配對後，對 `raw_text`、`claim_text`、`context` 搜尋英數字後接「連字號＋空白＋英數字」的殘留模式，7 篇均為 0。這是**殘留模式檢查**，不是 989 處逐一人工確認，也不是引用配對正確率。

SciCiteVal 的新輸出為 28 筆已辨識參考文獻、30 筆已配對引用、0 筆待核對；抽查 `Serhii`、`Radhakrishna`、`Bordignon`、`four-label`、`Mistral-AI`、`Qwen3-8B` 皆保留預期形式。其他論文仍可能有與斷字無關的引用配對問題；例如 CiteScreener 仍有多筆待核對，不能因斷字殘留數為 0 就視為整篇配對正確。

相關自動化測試涵蓋段內斷字、跨區塊人名、真正複合詞、型號、全大寫標籤，以及 JSON 使用的陳述、上下文與書目欄位。
