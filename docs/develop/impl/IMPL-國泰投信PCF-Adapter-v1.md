# Implementation Report: 國泰投信 PCF Adapter v1

## 實際做了什麼

- `src/issuer_pcf/cathay.py`：新增 `CathayPcfAdapter`，改用官方 API `cwapi.cathaysite.com.tw`（而非會回 403 的舊頁面 `www.cathaysite.com.tw/funds/etf/pcf.aspx`）：
  - `GetETFList?Keyword={市場代碼}`：動態查出投信內部 `fundCode`，不需人工維護對照表
  - `GetETFDetailStockList?FundCode={fundCode}&SearchDate={日期}`：查詢指定日期的成分股持股明細
- `src/issuer_pcf/registry.py`：註冊 `CathayPcfAdapter` 進 `ADAPTER_REGISTRY`
- `config/issuer_registry.json`：`cathay.isEnabled` 由 `false` 改為 `true`；`pcf_url_template` 更新為新 API 端點；移除 `issuer_internal_codes`（不再需要，`fundCode` 已改為執行期動態查詢）
- `config/watchlist.json`：`etfs` 加入 `00878`
- `tests/test_issuer_pcf_cathay.py`：新增 4 個單元測試（成功映射欄位／正確帶入 `FundCode`＋`SearchDate`／查無當日資料回空陣列／查無對應 ticker 時報錯）
- `docs/design/architecture/SD-ETF換倉資料來源方案評估-投信官網爬蟲-系統設計書.md`：補上第十輪查證紀錄（此為前次對話已完成的部分，本次接續實作）

## 與計畫的差異

無重大差異。原設計文件（SD 第十輪）僅記錄查證結果、未逕行調整 Phase 排序；本次依使用者明確指示「開放國泰投信」實作並啟用，屬於在既有 Phase 2 範圍內把已查證可行的項目正式落地，非變更 Phase 劃分本身。

## 遵循的慣例

- 沿用既有 `IssuerPcfProvider` 抽象介面，`fetch_holdings()` 回傳格式與 `YuantaPcfAdapter`／`FubonPcfAdapter`／`NomuraPcfAdapter` 一致（`component_stock_id`／`component_name`／`holding_shares`）
- 查代碼、查明細拆成兩個 private method，主流程只讀不算邏輯
- 沿用既有 `_USER_AGENT`（`FinanceTracker-ChipMonitor/1.0`）與逾時秒數慣例
- 沒有可信賴的交易日期欄位可比對，比照 `FubonPcfAdapter` 的作法（直接採用 API 依 `SearchDate` 回傳的資料，不做日期防呆）
- 找不到對應 ticker／內部代碼時，錯誤訊息比照既有 `FETCH_ISSUER_PCF_PARSE_ERROR` 代碼慣例

## 整合點與使用方式

- `Fetcher._resolve_issuer_provider()` 會依 `config/issuer_registry.json` 自動查到 `CathayPcfAdapter`，呼叫端不需要額外改動
- `watchlist.json.etfs` 加入 `00878` 後，`ConfigLoader._validate_issuer_registry()` 會通過驗證（`cathay.isEnabled=true`），排程主流程即可正常抓取

## 測試結果

- 新增與既有測試：`pytest` 全數 80 個測試通過（含新增 4 個 Cathay 單元測試、既有 `test_config.py`／`test_fetcher.py` 迴歸測試）
- 實際打向正式環境驗證（非自動化測試，手動執行確認）：
  - `GetETFList?Keyword=00878` 即時查得 `fundCode=CN`
  - `GetETFDetailStockList?FundCode=CN&SearchDate=2026-08-11/13/14` 三個日期皆成功取回 30 檔持股，且各日金額/股數確實隨日期變動（非快取假資料）
  - 以 `main.py --date 2026-08-13 --dry-run` 跑過完整排程主流程：`_meta.json` 顯示 `ISSUER_PCF: OK`，`data/snapshots/2026-08-13/etf_holdings/00878.json` 正確寫入 30 筆持股，換倉簡報訊息也正常產生（因 `00878` 首次納入監控、無前一日快照可比對，全數顯示為「新建倉」，屬預期行為）

## 待辦與已知限制

- [x] ~~`GetETFDetailStockList` 回應沒有交易日期欄位可驗證，若非開盤日回傳前一日舊資料會被誤判~~ **已驗證解除**：排程 cron（`0 10 * * 1-5 UTC`）只排除週六日、不排除國定假日，平日遇到假日仍會呼叫此 API；實測以週末日期（`SearchDate=2026-08-15`）查詢，站方回傳 `{"result": null, "returnCode": "4005", "returnMessage": "查無資料"}`，並非回傳前一日舊資料，現有 `payload.get("result", []) or []` 已能正確轉為空陣列、對應 `NO_DATA`，無需額外處理
- [ ] `GetETFList` 目前用 `Keyword=` 精確比對 `stockCode` 是否等於傳入的 ticker；若日後國泰同代碼被其他基金型態共用（目前未觀察到此情況），需要重新檢視比對邏輯
- 本次僅開放 `00878`；`issuer_registry.json` 內群益（`capital`）等其餘 Phase 2/3 投信仍維持 `isEnabled=false`，未受本次變動影響
