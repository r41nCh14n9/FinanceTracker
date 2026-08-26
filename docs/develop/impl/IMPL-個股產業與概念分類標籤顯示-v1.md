# Implementation Report: 個股產業與概念分類標籤顯示 v1

設計依據：[SD-個股產業與概念分類標籤顯示-系統設計書.md](../../design/architecture/SD-個股產業與概念分類標籤顯示-系統設計書.md)

## 實際做了什麼

- **`src/classification.py`（新增）**：`invert_category_table()` 共用反查工具（「分類→成員」轉「股票→分類清單」），`ClassificationService.ensure_industry_categories()` 負責 cache-aside 查詢並累積寫入產業分類表。
- **`src/fetcher.py`**：`FinMindClient` 新增 `fetch_stock_industry(stock_id)`，呼叫 FinMind `TaiwanStockInfo`，查無資料回傳 `None`、呼叫失敗直接讓例外往外拋（比照 `fetch_capital_stock` 風格，不在單股方法內吞例外，由呼叫迴圈層級處理）。
- **`src/storage.py`**：`SnapshotRepository` 新增 `read_industry_tags()` / `write_industry_tags()`，落地於 `data/tags/industry_tags.json`，整份檔案覆寫、不分日期。原實作曾放在 `data/reference/`（與既有 `capital_stock/` 股本快取同層），依使用者指示改為獨立的 `data/tags/` 資料夾（`capital_stock/` 是既有機制、仍在使用中，未變動）。
- **`src/config.py`**：新增 `_load_json_optional()`（檔案不存在或格式錯誤皆回傳 `{}`，只記 WARNING 不拋例外）與 `get_concept_tags()`，載入選填的 `config/concept_tags.json`。
- **`src/notifier.py`**：
  - `MessageFormatter.format()` / `Notifier.notify()` 新增 `industry_map`、`concept_map` 兩個選填參數（預設 `None` 視為 `{}`），既有呼叫端不帶這兩個參數仍可正常運作。
  - 新增 `_classification_tags()` 共用方法，組出 `[市值分級, 產業別, 概念標籤...]`（產業別經 `_display_industry()` 去除字尾「業」），三者皆無時回傳空陣列，呼叫端據此省略整個 `[]`。
  - `_format_stock_alert_line()` 改版：觸發原因標籤（量能/大額）從 `[]` 移入 `()`，與外資/投信/自營商明細以「，」相接。
  - 新增 `_group_and_format_events()` / `_format_single_event()` 取代原 `_format_events()`：ETF 換倉項目依產業別分組相鄰顯示（組間順序＝清單中各產業第一次出現的順序，查無產業別排最後），原本的加減倉文字說明改放進 `()`。
- **`main.py`**：新增 `_resolve_classification_tags()`，於 `run()` 分析完成、格式化/推播前呼叫——彙整 `stock_alerts` 與 `rebalance_events` 涉及的股票代碼、透過 `ClassificationService` 補齊產業分類、讀取並反轉 `concept_tags.json`，回傳 `(industry_map, concept_map)` 一併傳入 `MessageFormatter.format()` / `Notifier.notify()`。
- **測試**：新增 `tests/test_classification.py`；`test_fetcher.py`／`test_storage.py`／`test_config.py`／`test_main.py` 各補上對應新方法的測試；`test_notifier.py` 更新既有格式相關斷言（因觸發標籤搬位置、ETF 加減倉描述改放括號）並新增分類標籤／分組排序的測試。
- **README.md**：目錄結構補上 `src/classification.py`、`config/concept_tags.json`、`data/reference/industry_tags.json` 三個新項目。

## 與 SD 的差異

無實質差異。以下是 SD 未明講、實作時依專案既有慣例補上的細節：

- `MessageFormatter.format()` / `Notifier.notify()` 的 `industry_map`／`concept_map` 採**選填參數、預設 `None`**（內部視為 `{}`），而非強制參數，讓既有測試與未來只想拿到簡報而不關心分類的呼叫端不必每次都帶這兩個參數。
- `_resolve_classification_tags()` 抽成 `main.py` 的獨立 private function，比照既有 `_evaluate_institutional_alerts()` / `_classify_rebalance_events()` 的寫法風格（拆小、單一職責），SD 時序圖有畫出這段邏輯但沒有明講要不要獨立成函式。

## 整合點與使用方式

- **產業分類表**：`data/tags/industry_tags.json`，程式自動維護，不需手動建立；首次執行前不存在為正常狀態。此路徑受本機 `.git/info/exclude` 的 `data/` 排除規則影響，需 `git add -f` 才能加入版控（與既有 `data/reference/capital_stock/*.json` 待遇一致）。
- **概念標籤**：`config/concept_tags.json`（選填），維運人員依下列結構手動建立與維護：
  ```json
  {
    "IC 設計": { "members": [{ "stock_id": "3529", "stock_name": "力旺" }] }
  }
  ```
  不建立此檔案時，概念標籤功能形同未啟用，不影響其餘功能。
- **`--dry-run` 預覽**：分類標籤與分組排序在 `--dry-run` 模式下同樣會執行並顯示在預覽輸出中，方便維運人員在正式推播前確認效果。

## 待辦與已知限制

- FinMind 產業別查詢失敗或查無資料時**不會快取負面結果**，代表同一檔查不到分類的股票，只要之後持續出現在通知內容中，就會每天重新嘗試呼叫 FinMind——這是 SD 明確定案的設計（避免暫時性失敗被誤判為永久無分類），非本次遺漏。
- `concept_tags.json` 目前僅支援直接編輯 JSON 檔並 commit，尚無維護輔助指令（SD §六已列為非阻塞待確認事項）。
- 產業別顯示規則固定為「FinMind 原始值去尾字『業』」，未做更細緻的分類重新命名或歸併（如把多個電子相關細產業合併顯示為單一「電子」標籤）。
