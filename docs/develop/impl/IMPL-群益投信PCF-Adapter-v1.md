# Implementation Report: 群益投信 PCF Adapter v1

## 實際做了什麼

- `src/issuer_pcf/capital.py`：新增 `CapitalPcfAdapter`，改用官方 JSON API（取代原設計的 HTML 頁面表格解析）：
  - `POST /CFWeb/api/etf/list`：動態查出投信內部 `fundNo`，不需人工維護對照表
  - `POST /CFWeb/api/etf/buyback`（`{fundId, date}`）：查詢指定日期的成分股持股明細，資料在回應的 `data.stocks[]`（不是 `data.pcf`，`pcf` 只是基金層級概況）
- `src/issuer_pcf/registry.py`：註冊 `CapitalPcfAdapter` 進 `ADAPTER_REGISTRY`
- `config/issuer_registry.json`：`capital.isEnabled` 由 `false` 改為 `true`；`pcf_url_template` 更新為新 API 端點；移除 `issuer_internal_codes`（不再需要，`fundNo` 已改為執行期動態查詢）
- `config/watchlist.json`：`etfs` 加入 `00919`
- `tests/test_issuer_pcf_capital.py`：新增 6 個單元測試（成功映射欄位／不誤抓 `pcf` 區塊／正確帶入 `fundId`＋`date`／非交易日回空陣列／日期對不上時回空陣列／查無對應 ticker 時報錯）

## 與計畫的差異

無重大差異。與國泰的實作路徑一致，皆為在既有 Phase 2 範圍內把已查證可行的項目正式落地。

## 遵循的慣例

- 沿用既有 `IssuerPcfProvider` 抽象介面，回傳格式與其餘四個 Adapter 一致
- 查代碼、查明細拆成兩個 private method，主流程只讀不算邏輯
- 找不到對應 ticker 時的錯誤訊息比照既有 `FETCH_ISSUER_PCF_PARSE_ERROR` 代碼慣例

## 與 CathayPcfAdapter 的差異點

- 群益的持股明細 API 回應本身就帶了可信賴的日期欄位（`data.pcf.date1`），所以比照 `YuantaPcfAdapter`／`NomuraPcfAdapter` 的作法加了日期比對防呆；國泰的回應沒有日期欄位，當初只能依賴 API 對非交易日回傳空陣列的行為
- 群益對非交易日的回應是 `HTTP 200` 但 `code: 400`／`data: null`（有獨立欄位明確標示失敗），比國泰的 `result: null`（`success: false` 但頂層看起來像正常回應）語意更清楚，程式判斷邏輯也對應調整為先看 `code`／`data` 再往下解析

## 整合點與使用方式

- `Fetcher._resolve_issuer_provider()` 依 `config/issuer_registry.json` 自動查到 `CapitalPcfAdapter`，呼叫端不需要額外改動
- `watchlist.json.etfs` 加入 `00919` 後，`ConfigLoader._validate_issuer_registry()` 會通過驗證（`capital.isEnabled=true`）
- `issuer_registry.json` 的 `capital.etfs` 本來就同時登記了 `00919`／`00982A`（Phase 3 觀察名單「主動群益台灣強棒」，因同屬群益投信一併涵蓋），本次開通後兩者都可加入 watchlist，本次僅實際加入 `00919` 測試

## 測試結果

- 新增與既有測試：`pytest` 全數 **87 個測試通過**（含新增 6 個 Capital 單元測試、既有測試迴歸）
- 實際打向正式環境驗證（手動執行，非自動化測試）：
  - `POST /CFWeb/api/etf/list` 即時查得 `00919→fundNo=195`
  - `POST /CFWeb/api/etf/buyback` 用 8/11、8/13、8/14 三個日期各自查得 **40 檔持股**，且個股股數確實隨日期變動（非快取假資料，例：`2891 中信金` 三天分別為 905,277,000／909,957,000／913,027,000 股）
  - 以 `main.py --date 2026-08-13 --dry-run` 跑過完整排程主流程：`_meta.json` 顯示 `ISSUER_PCF: OK`，`data/snapshots/2026-08-13/etf_holdings/00919.json` 正確寫入 40 筆持股，換倉簡報也正常產生第 3 則訊息（因首次納入監控無前一日快照可比對，全數顯示「新建倉」，屬預期行為）

## 待辦與已知限制

- [ ] `data.pcf` 底下還有 `bonds`／`futures`／`assets`／`rps`／`characteristics` 等區塊本次未使用，若日後想擴充「期貨／債券部位」類的監控需求，可以參考這裡的欄位
- 本次僅開通群益投信本身；`issuer_registry.json` 內 Phase 3 剩餘投信（野村已開、統一／安聯／復華）仍維持 `isEnabled=false`，未受本次變動影響
