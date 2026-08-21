# Implementation Report: ETF 換倉解析健全性檢查與統一投信 Adapter v1

## 實際做了什麼

### 1. SA 文件「解析健全性檢查機制」正式實作
- `src/fetcher.py`：`Fetcher._fetch_etf_holdings()` 新增 `_is_holding_count_anomaly()`，寫入快照前先跟前一交易日持股筆數比對，跌幅達門檻視為投信網站改版造成的解析異常，本次不採用（不寫入、不計入成功）
- `src/config.py`：新增 `ConfigLoader.get_etf_holding_count_drop_pct_threshold()`，讀 `thresholds.json.default.etf_holding_drop_pct`，選填欄位，未設定時預設 50%
- `config/thresholds.json`：`default` 區塊補上 `etf_holding_drop_pct: 50.0`
- `tests/test_fetcher.py`：新增 3 個測試（跌幅超標拒絕寫入／跌幅未超標正常寫入／無前一天快照時不誤判新 ETF）
- `tests/test_config.py`：新增 2 個測試（預設值／讀取自訂值）

### 2. 意外發現並修正的既有 bug：換倉比對誤把「查無資料」當成「持股歸零」
- **問題**：`main.py._classify_rebalance_events()` 直接讀 `storage.read_etf_holdings(target_date, etf_id)`，檔案不存在時回傳 `[]`，跟前一天比對後會把「今天完全沒抓到資料」跟「今天持股真的歸零」混為一談，導致某 ETF 當天 fetch 失敗時，把它原本所有持股都判定成「完全清倉」推播出去
- **如何發現**：驗證健全性檢查機制時，用 8/17 真實資料跑 `main.py --dry-run`，親眼看到 0050 因元大頁面「只顯示最新一天」的已知限制（查詢日期跟頁面當下日期不符）沒抓到資料，結果推播訊息出現「0050 完全清倉」50 筆事件
- **修正**：`main.py._classify_rebalance_events()` 改為 `curr_holdings` 為空時直接 `continue` 跳過該 ETF，不產生任何事件
- `tests/test_main.py`（新檔案）：新增 3 個測試鎖住這個行為（無前一交易日快照／今天無資料時跳過／今天有資料時正常產生事件）

### 3. 統一投信（Phase 3）正式實作開通
- `src/issuer_pcf/uni.py`：新增 `UniPcfAdapter`
  - `_resolve_fund_code()`：`GET /ETF/Fund/Index` 用 `BeautifulSoup` 解析 ticker↔`fundCode` 對照（連結格式 `href="/ETF/Fund/Info?fundCode=XXX"`），動態查詢不維護靜態對照表
  - `_fetch_asset_sheet()`：`GET /ETF/Fund/AssetExcelNPOI?fundCode=...` 回傳 Excel，用 `openpyxl` 解析
  - `_extract_stock_rows()`：同一張工作表混了基金概況／期貨／股票好幾個區塊，定位「股票」標題後的資料列
  - `_parse_roc_date()`：資料日期是民國年格式（如 `115/08/14`），換算成西元年後跟 `snapshot_date` 比對
  - `_new_session()`：`requests.Session()` 先訪首頁取得 `__nxquid` 等工作階段 Cookie，才能正常打 Excel 匯出端點
- `requirements.txt`：新增 `openpyxl>=3.1,<4`
- `src/issuer_pcf/registry.py`：註冊 `UniPcfAdapter`
- `config/issuer_registry.json`：`uni.isEnabled` 改為 `true`，`pcf_url_template` 更新，移除靜態 `issuer_internal_codes`
- `config/watchlist.json`：`etfs` 加入 `00981A`
- `tests/test_issuer_pcf_uni.py`（新檔案）：新增 6 個測試

### 4. 復華投信：查證後確認阻塞，本次未實作
- 官網已改版為 Vue.js SPA，原本記錄可行的 `GET /api/assets?fundID=...` JSON 端點已失效（回傳首頁 HTML）
- 持股明細頁未見任何 SSR 內嵌狀態可取巧解析（不像元大的 `__NUXT__`），純前端渲染，靜態請求看不到資料
- 經 Roy Chiang 確認擱置，待有人用瀏覽器開發者工具重新查得新版底層 API 才能重啟評估

## 與計畫的差異
- 原本只打算處理「健全性檢查機制」與「統一／復華兩家 Phase 3 投信」，過程中意外發現並修正了 `main.py` 的換倉比對 bug——這不在原計畫範圍內，但因為是驗證健全性檢查時直接用真實資料撞見的實際故障（不是理論推演），且修正成本低（一段 early-continue），當場一併處理
- 復華投信原計畫要實作，因網站已改版查無可用端點而改為擱置，如實記錄查證過程而非強行湊一個不可靠的實作

## 遵循的慣例
- `UniPcfAdapter` 沿用既有 `IssuerPcfProvider` 介面與回傳格式，跟其餘四個 Adapter 一致
- 健全性檢查門檻走既有 `thresholds.json.default` 慣例（選填欄位＋合理預設值，比照 `overrides` 的寫法），不強制所有既有設定檔都要補這個欄位
- 錯誤訊息延續既有 `FETCH_ISSUER_PCF_PARSE_ERROR` 代碼慣例

## 整合點與使用方式
- `Fetcher._resolve_issuer_provider()` 依 `config/issuer_registry.json` 自動查到 `UniPcfAdapter`，呼叫端不需額外改動
- 健全性檢查是 `Fetcher` 內部行為，呼叫端無感；門檻可透過 `thresholds.json.default.etf_holding_drop_pct` 調整，不改則沿用 50% 預設值
- `main.py._classify_rebalance_events()` 的修正影響所有 ETF（不只本次新增的），屬於既有籌碼監控推播引擎（`SD-籌碼監控推播引擎-系統設計書.md`）範疇內的修正，本次因關聯發現一併處理，未另外走該文件的異動流程

## 測試結果
- 全量單元測試：**101 個測試全數通過**（本輪新增 14 個：健全性檢查 3 個、config 門檻 2 個、換倉比對修正 3 個、統一 Adapter 6 個）
- 統一投信真實 API 驗證：`Fund/Index` 動態查得 `00981A→49YTW`；`AssetExcelNPOI` 取回 50 檔持股，欄位與 ROC 日期換算皆正確（`115/08/14`→`2026-08-14`）
- 換倉誤報 bug 重現與修正驗證：`main.py --date 2026-08-17 --dry-run` 修正前出現「0050 完全清倉」50 筆假事件，修正後正確顯示「0050 今日尚無持股資料，略過本次換倉比對」，不再誤報
- 健全性檢查機制：以真實 8/13→8/14 資料跑過，確認正常換倉不會被誤擋（無 false positive）

## 待辦與已知限制
- [ ] 復華投信：待有新的底層 API 線索（瀏覽器開發者工具重新查證）才能重啟評估，見 SD 文件 §六 #22
- [ ] 安聯投信：技術可行性仍未解決（SPA，無底層 API），非本次範圍
- [ ] 健全性檢查門檻（50%）與換倉比對修正皆未針對「連續多日皆無資料」設計加強告警（SD 文件 §六 #5，非本次範圍，維持 Log 記錄）
- [ ] `main.py` 目前沒有正式的測試涵蓋率报告工具，`tests/test_main.py` 是本次新建的第一個 main.py 測試檔案，僅涵蓋本次修正的函式，其餘 `run()`／`main()` 等進入點邏輯仍無自動化測試
