# Implementation Report: 安聯投信 PCF Adapter v1

## 實際做了什麼

- `src/issuer_pcf/allianz.py`：新增 `AllianzPcfAdapter`，改用安聯投信官網獨立後端 `etf.allianzgi.com.tw/webapi`（而非第五輪判定為無法取得內容的前台 SPA 頁面 `etf-info/{內部代碼}`）：
  - `GET /api/AntiForgery/GetAntiForgeryToken`：先取得 CSRF token（同時透過 Session 帶回 Antiforgery Cookie）
  - `POST /api/Fund/GetFundOverview`：動態查出投信內部 `CFundNo`，不需人工維護對照表；後續兩支端點皆需帶 `x-xsrf-token` Header
  - `POST /api/Fund/GetFundAssets`：查詢成分股持股明細，回應為多張表格（含一張無標題、內容其實是資產總覽的表格），取 `TableTitle` 開頭為「股票」的那張，並依 `Columns` 定義動態找出「股票代號」「股票名稱」「股數」各欄位在 `Rows` 中的位置索引
- `src/issuer_pcf/registry.py`：註冊 `AllianzPcfAdapter` 進 `ADAPTER_REGISTRY`
- `config/issuer_registry.json`：`allianz.isEnabled` 由 `false` 改為 `true`；`pcf_url_template` 更新為 `GetFundAssets` 端點；移除不再需要的 `issuer_internal_codes` 靜態對照（`CFundNo` 已改為執行期動態查詢）
- `config/watchlist.json`：`etfs` 加入 `00984A`
- `tests/test_issuer_pcf_allianz.py`：新增 8 個單元測試（成功映射欄位並略過非股票表格／`x-xsrf-token` 與 `FundID` 正確帶入請求／查詢日與 `PCFDate` 不符回空陣列／查無對應 ticker 報錯／找不到「股票」表格報錯／欄位名稱與預期不符時報錯而非用錯位置組資料／`Entries.Data` 缺漏報錯／以實際查證時擷取的真實回應驗證解析邏輯）
- `tests/fixtures/allianz_overview.json`、`tests/fixtures/allianz_assets_e0001.json`：新增真實查證時擷取（並裁減筆數）的回應片段，供上述最後一個測試使用
- `docs/design/architecture/SD-ETF換倉資料來源方案評估-投信官網爬蟲-系統設計書.md`：補上第十五輪查證紀錄（前次對話已完成，本次接續實作）

## 與計畫的差異

無重大差異。SD 文件第十五輪原僅記錄查證結果、明確標註「本次僅記錄查證結果，尚未實作」，本次依使用者指示「開始實作」，在同一輪把技術可行性驗證直接推進到實作並開通，作法比照國泰／群益／統一／復華等前例（先查證、確認後同一階段實作＋開通）。

實作過程中額外發現並解決了 SD 文件第十五輪標記為待確認的兩個項目：

- **§六 #24（`GetFundAssets` 完整 `Rows` 結構）**：以本機 Python 直接呼叫正式環境端點取得完整真實回應後確認：頂層信封是 `{Entries: {FundID, Data: {FundAsset, Table}}}`（跟 `GetFundOverview` 同一種信封格式，而非文件原猜測的「回應本身就是陣列」），`Table` 是 3 張表格（無標題的資產總覽表、「股票 (95.49%)」、「期貨」），股票表格欄位確認為「序號／股票代號／股票名稱／股數／權重(%)」，`Rows` 為位置索引陣列，與野村 `GetFundAssets` 的 `Table.Columns`/`Table.Rows` 結構幾乎一致。
- **§六 #25（查詢日期參數／資料日期欄位）**：確認 `GetFundAssets` 本身不吃日期參數，但 `Data.FundAsset.PCFDate`（`yyyy/MM/dd` 格式）就是這批持股資料對應的實際交易日，可直接拿來跟 `snapshot_date` 比對，效果等同其餘投信的日期防呆機制，不需要额外處理 token 快取或重用（Antiforgery token 有效期 24 小時，本身也只在單次 `fetch_holdings()` 內用一次，沒有跨次重用的必要）。

## 遵循的慣例

- 沿用既有 `IssuerPcfProvider` 抽象介面，`fetch_holdings()` 回傳格式與其餘 Adapter 一致（`component_stock_id`／`component_name`／`holding_shares`）
- 取 token、查代碼、查明細、找表格、解析列各自拆成獨立的 private method，主流程只讀不算邏輯
- 沿用既有 `_USER_AGENT`（`FinanceTracker-ChipMonitor/1.0`）與逾時秒數慣例
- 找不到對應 ticker、找不到「股票」表格、欄位跟預期不符時，一律拋出例外並比照既有 `FETCH_ISSUER_PCF_PARSE_ERROR` 訊息慣例，寧可失敗也不要用錯的欄位位置組出看似正常、實際錯誤的資料
- 沒有可信賴的查詢日期參數可帶入，比照 `FubonPcfAdapter` 的作法：改用回應內的資料日期欄位（`PCFDate`）跟 `snapshot_date` 比對，不符即回傳空清單交由既有邏輯判定為 `NO_DATA`
- 多張表格需要鎖定正確區塊的解析情境，沿用富邦／群益／野村已有的「用 `TableTitle` 定位，不直接抓第一張表」慣例；本次額外確認：資產總覽表格的列資料裡本身有一格字面值就是「股票」二字，若靠掃描儲存格內容找表格會誤判，必須靠 `TableTitle` 判斷，測試已加入這個陷阱情境鎖住行為

## 整合點與使用方式

- `Fetcher._resolve_issuer_provider()` 會依 `config/issuer_registry.json` 自動查到 `AllianzPcfAdapter`，呼叫端不需要額外改動
- `watchlist.json.etfs` 加入 `00984A` 後，`ConfigLoader._validate_issuer_registry()` 會通過驗證（`allianz.isEnabled=true`），排程主流程即可正常抓取

## 測試結果

- 新增與既有測試：`pytest` 全數 132 個測試通過（含新增 8 個 Allianz 單元測試、既有測試全數迴歸通過）
- 實際打向正式環境驗證（非自動化測試，手動執行確認）：
  - `GetAntiForgeryToken`／`GetFundOverview`／`GetFundAssets` 三支端點皆 `HTTP 200`，`GetFundOverview` 即時查得安聯投信旗下 4 檔基金（`E0001↔00984A`／`E0002↔00993A`／`E0003↔00402A`／`E0004↔00412A`）
  - 直接呼叫 `AllianzPcfAdapter.fetch_holdings("00984A", "2026-08-24")` 取回 119 檔真實持股，`PCFDate` 與查詢日期相符
  - 以 `main.py --date 2026-08-24 --dry-run` 跑過完整排程主流程：`_meta.json` 顯示 `ISSUER_PCF: OK`，`data/snapshots/2026-08-24/etf_holdings/00984A.json` 正確寫入 119 筆持股；因 `00984A` 首次納入監控、無前一日快照可比對且此 Adapter 不支援查詢非當日資料，換倉比對正確略過（`FETCH_ISSUER_PCF_NO_PREVIOUS_DAY`），屬預期行為，非錯誤

## 待辦與已知限制

- [ ] 本次僅實測 `E0001`（`00984A`）一檔；安聯旗下另外 3 檔基金（`00993A`／`00402A`／`00412A`）雖同樣可透過 `GetFundOverview` 動態解析，但成分股表格欄位是否完全一致（例如債券型/海外型基金的表格標題、欄位是否仍是「股票」/「股數」）未經查證，若日後要納入監控需先個別驗證
- [ ] `SUPPORTS_BACKFILL` 維持預設值 `False`（未覆寫為 `True`）：`GetFundAssets` 未觀察到可帶入查詢日期的參數，只能查到最新一期資料，無法像野村／群益／復華一樣補查歷史日期
- [ ] Antiforgery token／Cookie 目前是每次呼叫 `fetch_holdings()` 就重新取一次，未做同一批次執行內的重用；效能影響極小（每交易日僅呼叫一次），暫不處理
- 本次僅開放安聯投信（`allianz`）；`issuer_registry.json` 內其餘尚未實作的投信不受本次變動影響
