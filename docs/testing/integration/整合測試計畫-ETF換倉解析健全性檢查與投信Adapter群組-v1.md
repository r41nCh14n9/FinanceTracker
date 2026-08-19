# 整合測試計畫：ETF 換倉解析健全性檢查與投信 Adapter 群組 v1

## 0. 文件資訊

| 項目 | 內容 |
| :--- | :--- |
| 測試範疇 | SA 文件「解析健全性檢查機制」實作；連帶發現並修正之換倉比對誤報 bug；國泰／群益／統一三個投信 Adapter |
| 設計依據 | [SA-ETF換倉資料來源方案評估-投信官網爬蟲可行性分析.md](../../analysis/requirements/SA-ETF換倉資料來源方案評估-投信官網爬蟲可行性分析.md)、[SD-ETF換倉資料來源方案評估-投信官網爬蟲-系統設計書.md](../../design/architecture/SD-ETF換倉資料來源方案評估-投信官網爬蟲-系統設計書.md)（第十～十三輪） |
| 對應 code review | [CODE-REVIEW-ETF換倉解析健全性檢查與投信Adapter群組-v1.md](../../review/code-reviews/CODE-REVIEW-ETF換倉解析健全性檢查與投信Adapter群組-v1.md) |
| 建立日期 | 2026-08-17 |
| 測試環境 | 本機開發環境（Windows），Python 3.14；未使用 Postman／REST 測試（本專案為排程批次腳本，無對外 HTTP API，不適用本 skill 之 Postman Collection 產出） |
| 測試層級 | 單元測試（`pytest`）＋ 針對投信官網之真實環境整合驗證（手動執行）＋ 端對端排程主流程驗證（`main.py --dry-run`） |

---

## 一、測試策略

由於本專案是排程批次腳本（無 Web UI、無對外 API），測試分三層進行，缺一不可：

1. **單元測試**：驗證每個 Adapter／函式在各種輸入（含邊界情況）下的行為，用 mock 隔離外部網路依賴
2. **真實環境整合驗證**：單元測試的 mock 資料是否貼近真實回應形狀，必須拿正式環境的即時回應交叉驗證，避免「mock 測試全過、實際打正式環境卻不是那麼回事」
3. **端對端排程主流程驗證**：用 `main.py --date {日期} --dry-run` 跑過完整「抓取→分析→推播」流程，確認新功能與既有元件（`Analyzer`／`Notifier`）整合後行為正確，且不引入其他既有 ETF（如 `0050`）的迴歸問題

---

## 二、需求追溯矩陣（對應 SA 文件 §六 待確認事項）

| SA 待確認事項 | 測試案例 | 結果 |
| :--- | :--- | :--- |
| 解析健全性檢查機制（成分股數量驟降/劇烈變化時標記警示） | TC-01～TC-04（單元）、TC-13（端對端） | ✅ 通過 |
| 連續 N 天爬取失敗之額外告警 | — | ⚪ 未涵蓋，SD 文件 §六 #5 仍為待確認、非本次範圍 |
| 各投信網站 robots.txt／服務條款查證 | 已於 SD 文件第八／十一輪完成，非本次測試範圍 | — |

## 三、需求追溯矩陣（對應本次三個 Adapter）

| Adapter | 對應 ETF | 單元測試 | 真實環境驗證 | 端對端驗證 |
| :--- | :--- | :--- | :--- | :--- |
| `CathayPcfAdapter`（國泰，前次會話已完成，本次僅迴歸） | `00878` | ✅ | ✅ | ✅ |
| `CapitalPcfAdapter`（群益，前次會話已完成，本次僅迴歸） | `00919` | ✅ | ✅ | ✅ |
| `UniPcfAdapter`（統一，本次新增） | `00981A` | ✅ TC-09～TC-12 | ✅ TC-14 | ✅ TC-13 |

---

## 四、測試案例明細

### 健全性檢查機制（`Fetcher._is_holding_count_anomaly`）

| # | 測試案例 | 前置條件 | 預期結果 | 狀態 |
| :--- | :--- | :--- | :--- | :--- |
| TC-01 | 持股筆數跌幅超過門檻（40→3 檔，跌幅 92.5%） | 前一交易日已有 40 筆快照 | 判定為異常，不寫入今日快照，`ISSUER_PCF` 狀態為 `ERROR` | ✅ 通過（`test_fetch_etf_holdings_rejects_drastic_drop_from_previous_day`） |
| TC-02 | 持股筆數跌幅低於門檻（40→30 檔，跌幅 25%） | 同上 | 視為正常換倉，照常寫入 | ✅ 通過（`test_fetch_etf_holdings_accepts_drop_below_threshold`） |
| TC-03 | 無前一交易日快照可比對（新加入監控的 ETF） | 首次執行，無歷史快照 | 不誤判為異常，正常寫入 | ✅ 通過（`test_fetch_etf_holdings_no_previous_snapshot_does_not_block_new_etf`） |
| TC-04 | 前一交易日快照檔案本身損毀（JSON 格式異常） | 手動寫入非法 JSON 至前一日快照檔 | 不拋例外、不影響其他資料源，視為無基準、正常寫入今日資料 | ✅ 通過（`test_fetch_etf_holdings_corrupted_previous_snapshot_does_not_crash_whole_fetch`，本次 code review 發現並修正後新增） |
| TC-05 | 門檻可由設定檔調整 | `thresholds.json.default.etf_holding_drop_pct` 未設定／設定為 30 | 未設定時預設 50%；設定時採用設定值 | ✅ 通過（`test_etf_holding_count_drop_pct_threshold_*`） |

### 換倉比對誤報修正（`main.py._classify_rebalance_events`）

| # | 測試案例 | 前置條件 | 預期結果 | 狀態 |
| :--- | :--- | :--- | :--- | :--- |
| TC-06 | 找不到前一交易日快照 | 首次執行 | 回傳空事件清單，不拋例外 | ✅ 通過 |
| TC-07 | 今日該 ETF 無持股資料（檔案不存在） | 前一日有 2 檔持股，今日檔案缺失 | 略過該 ETF 比對，不產生任何事件（**修正前會誤產生 2 筆清倉事件**） | ✅ 通過（`test_classify_rebalance_events_skips_etf_when_todays_holdings_missing`） |
| TC-08 | 今日該 ETF 有持股資料，且與前一日不同 | 前一日 1 檔，今日 2 檔 | 正確產生 1 筆 ADDITION 事件 | ✅ 通過 |

### `UniPcfAdapter`（統一投信）

| # | 測試案例 | 預期結果 | 狀態 |
| :--- | :--- | :--- | :--- |
| TC-09 | 成功解析並映射「股票」區塊欄位 | 回傳正確 `component_stock_id`／`component_name`／`holding_shares` | ✅ 通過 |
| TC-10 | 不誤抓「基金資產」「期貨」等其他區塊 | 只回傳「股票」區塊之後的資料列 | ✅ 通過 |
| TC-11 | Excel 資料日期（民國年格式）與查詢日期不符 | 回傳空清單，視為當日尚未更新 | ✅ 通過 |
| TC-12 | 查無對應 ticker 之內部代碼 | 拋出 `RuntimeError`，訊息含 `FETCH_ISSUER_PCF_PARSE_ERROR` | ✅ 通過 |

### 端對端與真實環境驗證

| # | 測試案例 | 執行方式 | 結果 |
| :--- | :--- | :--- | :--- |
| TC-13 | 完整排程主流程（`main.py --date 2026-08-17 --dry-run`） | 手動執行，觀察日誌與產出簡報 | ✅ 修正前：0050 因元大頁面日期不符無資料，誤產生「完全清倉」50 筆事件並推播；✅ 修正後：正確記錄「0050 今日尚無持股資料，略過本次換倉比對」，簡報不含誤報內容 |
| TC-14 | 統一投信真實 API 串接（`Fund/Index`＋`AssetExcelNPOI`） | 手動執行 `UniPcfAdapter` 內部方法 | ✅ 動態查得 `00981A→fundCode=49YTW`；取回 50 檔真實持股，ROC 日期換算正確（`115/08/14`→`2026-08-14`） |
| TC-15 | 健全性檢查機制不誤擋正常換倉（真實資料 8/13→8/14） | `main.py --date 2026-08-14 --dry-run` | ✅ `ISSUER_PCF` 狀態 `OK`，三檔 ETF（0050/00878/00919）皆正確寫入 50/30/40 筆，無誤判 |

---

## 五、迴歸測試

| 項目 | 結果 |
| :--- | :--- |
| 全量單元測試（`python -m pytest`） | ✅ 102 個測試全數通過 |
| 既有 Adapter（元大／富邦／野村／國泰／群益）是否受影響 | ✅ 無異動，對應測試皆通過 |

---

## 六、未涵蓋範圍與已知限制

- 本專案無對外 HTTP API（純排程批次腳本），不適用本 skill 之 **Postman Collection** 產出項目
- 未使用 Playwright 網頁測試（本專案無 Web UI）
- 連續多個交易日皆發生解析異常時，健全性檢查在第二天起會因「前一天無基準」而失效，此為已知取捨（見 code review Minor 項），未納入本次測試範圍
- 復華、安聯投信因技術面阻塞未實作，無對應測試

---

## 七、結論

本次新增與修正之功能（健全性檢查機制、換倉比對誤報修正、國泰／群益／統一三個 Adapter）皆已完成單元測試、真實環境整合驗證、端對端排程流程驗證三層測試，102 個自動化測試全數通過，且針對本次意外發現的既有 bug（換倉比對誤報）已用真實資料重現問題並驗證修正有效。建議可進入正式排程（cron）驗證階段。
