# Implementation Report: 凱基投信 PCF Adapter v1

## 實際做了什麼

- `src/issuer_pcf/kgi.py`（新檔案）：新增 `KgiPcfAdapter`
  - `_resolve_fund_id()`：市場代碼 → 投信內部基金代碼（目前僅 `009816→J023` 一筆，靜態表維護，尚未查得動態清單端點）
  - `_fetch_html()`：`GET /Fund/Detail?fundID=...` 取得基金明細頁（SSR，完整持股表格已在原始回應內）
  - `_find_stock_table()`：定位 `<h4>股票</h4>` 標題節點後方的表格，比照富邦用 `<h6>股票</h6>` 定位表格的既有模式
  - `_parse_rows()`：解析 `<tr name="content">` 各列，並依 `component_stock_id` 去重（頁面存在對應行動版/桌面版兩個重複區塊）
- `src/issuer_pcf/registry.py`：註冊 `KgiPcfAdapter`
- `config/issuer_registry.json`：新增 `kgi` 區塊（`isEnabled: true`）
- `config/watchlist.json`：`etfs` 加入 `009816`
- `tests/fixtures/kgi_detail_j023.html`（新檔案）：比照真實頁面結構的 fixture，刻意保留桌面版/行動版兩個重複區塊以驗證去重邏輯
- `tests/test_issuer_pcf_kgi.py`（新檔案）：新增 3 個測試
- `docs/design/architecture/SD-ETF換倉資料來源方案評估-投信官網爬蟲-系統設計書.md`：第十六輪，記錄凱基投信查證與 API 契約
- `docs/analysis/requirements/SA-凱基投信PCF資料來源評估-009816籌碼監控可行性分析.md`：新增文件，記錄前置可行性評估

## 與計畫的差異

- 使用者原先提出「透過前端畫面爬蟲」（暗示可能需要 Headless Browser）的評估方向，實際查證後發現基金明細頁是傳統伺服器端渲染，完整持股表格已存在原始 HTTP 回應中，最終改採跟富邦／野村相同的最輕量 `requests` + `BeautifulSoup` 靜態解析，未新增任何 Headless Browser 或 Node.js 子行程相依
- `issuer_internal_codes` 這個 `config/issuer_registry.json` 既有欄位（`ConfigLoader.get_issuer_mapping()` 會讀取）實際上並未被 `Fetcher._resolve_issuer_provider()` 使用（`adapter_cls()` 呼叫時不帶任何參數），因此凱基的內部代碼對照改為在 `KgiPcfAdapter` 內部自行維護，未使用該設定檔欄位

## 遵循的慣例

- 沿用既有 `IssuerPcfProvider` 介面與回傳格式，跟其餘 8 個已實作 Adapter 一致
- 解析錯誤訊息比照既有 `FETCH_ISSUER_PCF_PARSE_ERROR` 代碼慣例
- `User-Agent`、逾時秒數皆沿用既有常數值，未自創新設定
- `BeautifulSoup` 解析 HTML 數值字元參照編碼的中文時會自動解碼，不需額外處理

## 整合點與使用方式

- `Fetcher._resolve_issuer_provider()` 依 `config/issuer_registry.json` 自動查到 `KgiPcfAdapter`，呼叫端不需額外改動
- `KgiPcfAdapter.SUPPORTS_BACKFILL` 維持基底類別預設值 `False`（頁面日期查詢對持股表格無效，僅能取得當日資料，換倉比對缺前一天快照時會直接略過，不強行回補）

## 測試結果

- 全量單元測試：**169 個測試全數通過**（本輪新增 3 個 Kgi 測試）

## 待辦與已知限制

- [ ] 內部代碼對照為手動維護的靜態表（目前 `009816→J023`、`00407A→J024` 兩筆）；若凱基投信旗下有其他 ETF 需要納入監控，需另外查出對應的 `fundID` 並加進 `_FUND_ID_BY_TICKER`，或後續找到動態查詢端點後改用比照國泰/群益模式的自動解析
- [ ] 尚未查得凱基投信「旗下全部基金清單」的動態對照端點，若日後有多檔標的需求，值得再花一輪查證
- [ ] 服務條款全文之人工審視（`robots.txt` 已查證全站無限制）仍列為上線前確認項，非本次阻塞項

## 後續更新：新增 `00407A`（凱基台灣主動式ETF）

- 查證確認 `00407A`（凱基台灣主動式ETF，2026-06-24 掛牌）對應內部代碼 `J024`（從其活動頁 PDF 連結 `J024_Buy.pdf` 查得線索，再以 `/Fund/Detail?fundID=J024` 實測確認基金名稱與「股票」持股表格皆正確）
- `src/issuer_pcf/kgi.py` 的 `_FUND_ID_BY_TICKER` 新增一筆；`config/issuer_registry.json` 的 `kgi.etfs`、`config/watchlist.json` 皆加入 `00407A`
- `tests/test_issuer_pcf_kgi.py` 新增 1 個測試，驗證第二檔 ticker 能解析到正確的 `fundID`（不會誤用 `009816` 的 `J023`）
- 全量單元測試：**170 個測試全數通過**
- **附帶觀察**：`config/watchlist.json` 在本次改動前，`etfs` 內的 `00984A` 已不在清單中（早於本次變更，推測為使用者自行於 IDE 手動移除），本次未回復該項，僅在既有清單基礎上新增 `00407A`
