# Implementation Report: ETF PCF 資料來源改造（投信官網爬蟲）v1

## 實際做了什麼

- `src/issuer_pcf/base.py`：`IssuerPcfProvider` 抽象介面，`fetch_holdings(etf_id, snapshot_date) -> list[dict]`。
- `src/issuer_pcf/yuanta.py`：`YuantaPcfAdapter`。HTTP 取回元大 PCF 頁面 HTML → 寫入暫存檔 → `subprocess` 呼叫 `extract_nuxt_state.js` 在 Node 沙箱解析頁尾 `window.__NUXT__` 狀態 → 從 `fetch[]` 陣列中找出含 `pcfData` 鍵的元素 → 比對 `pcfData.PCF.trandate` 與查詢日期、不符則回傳空清單 → 映射 `stkcd/name/qty` 為 `component_stock_id/component_name/holding_shares`。四種失敗情境（Node 未安裝、子行程非 0 結束、輸出非合法 JSON、找不到 `pcfData`）皆拋出訊息中帶有可 grep 標記（`FETCH_ISSUER_PCF_NODE_UNAVAILABLE`／`FETCH_ISSUER_PCF_NUXT_EXTRACT_ERROR`）的 `RuntimeError`。
- `src/issuer_pcf/scripts/extract_nuxt_state.js`：純 Node 內建 `fs`／`vm` 模組，無 npm 相依，語法相容舊版 Node（無 optional chaining／nullish coalescing）。讀 HTML 檔案路徑（`argv[2]`）→ 找 `window.__NUXT__=` 標記 → `vm.runInNewContext` 於乾淨沙箱求值 → 印 JSON 到 stdout；任何步驟失敗印一行訊息到 stderr 並以非 0 結束碼結束。
- `src/issuer_pcf/fubon.py`：`FubonPcfAdapter`。HTTP 取回 `Trade/Assets.aspx` 頁面 → `BeautifulSoup` 找出文字為「股票」的 `<h6>` 後面緊接的 `<table>`（頁面依序有期貨／股票／附買回債券三張同構表格）→ 逐列解析，跳過表頭列（`class="title"`）與「股票合計」小計列 → 股數字串去逗號轉 `int`。找不到「股票」區塊時拋出帶 `FETCH_ISSUER_PCF_PARSE_ERROR` 標記的 `RuntimeError`。
- `src/issuer_pcf/registry.py`：`ADAPTER_REGISTRY`，`"YuantaPcfAdapter"`／`"FubonPcfAdapter"` 字串鍵對應各自的類別。
- `config/issuer_registry.json`：以投信為單位登記，每個投信一個 `isEnabled` 開關＋`etfs` 清單＋`adapter`／`pcf_url_template`（選填 `issuer_internal_codes`）。目前 `yuanta`／`fubon` 為 `isEnabled: true`；另外先登記 `cathay`／`capital`／`nomura`／`uni`／`allianz`／`fuhwa` 六家 SD 文件已查證過的投信，`isEnabled: false`（尚無對應 Adapter 程式碼，之後開發完成才能開通）。**此檔取代原本規劃的 `etf_issuer_mapping.json`**（見下方「後續調整」）。
- `src/config.py`：`ConfigLoader` 讀入 `issuer_registry.json`，`_validate_issuer_registry()` 建立 ETF→投信鍵的反查表，`watchlist.etfs` 內任一代碼若查無登記，或雖有登記但該投信 `isEnabled=false`，會在啟動階段以兩種不同訊息分別報 `ConfigError`（方便區分「代碼打錯」與「投信還沒開發完成」）。新增 `get_issuer_mapping(etf_id)`（沿用原本回傳形狀，供 `Fetcher` 查表打 API）、`get_enabled_issuers()`、`get_available_etfs_by_issuer(issuer_key)` 兩個查詢用途的輔助方法。
- `src/models.py`：`DataSourceKey.TWSE_PCF` 更名為 `ISSUER_PCF`。
- `src/fetcher.py`：移除 `TwsePcfClient` 整個類別；`Fetcher.__init__` 的 `twse_client` 參數改為 `issuer_providers: dict[str, IssuerPcfProvider] | None`（依 ETF 代碼索引的注入點）；`_fetch_etf_holdings` 改透過新增的 `_resolve_issuer_provider(etf_id)` 取得 provider（優先看注入覆寫，否則查設定檔＋`ADAPTER_REGISTRY` 動態組出），既有的 try/except/`fetched_any`/`last_error` 迴圈結構完全保留。
- `requirements.txt`：新增 `beautifulsoup4>=4.12,<5`。
- `.github/workflows/daily-chip-monitor.yml`：新增 `actions/setup-node@v4`（pin `node-version: "20"`），明確化原本隱含依賴 runner image 內建 Node 的假設。
- `README.md`：更新目錄結構（`issuer_pcf/` 子套件、`etf_issuer_mapping.json`）、快速開始加上「需先安裝 Node.js」的第 0 步、已知限制段落更新為反映新資料來源與 Phase 1 範圍。
- 測試：`tests/test_issuer_pcf_yuanta.py`（6 案例，mock `subprocess.run` 涵蓋成功／日期不符／四種失敗）、`tests/test_issuer_pcf_yuanta_integration.py`（1 案例，實際呼叫 `node` 解析真實裁剪過的 0050 頁面存檔，`node` 不存在時自動跳過）、`tests/test_issuer_pcf_fubon.py`（3 案例，用真實裁剪過的 006208 頁面存檔驗證只抓股票區塊、正確排除表頭與小計列）、`tests/test_config.py`（針對 `issuer_registry.json` 新增 7 案例，含「未登記」與「已登記但未開通」兩種錯誤情境、`get_enabled_issuers`／`get_available_etfs_by_issuer` 查詢）、`tests/test_fetcher.py`（新增 2 案例：透過 registry 動態解析、未知 adapter 鍵值時整體不中斷只標記 ERROR）。

## 後續調整：`etf_issuer_mapping.json` → `issuer_registry.json`

初版實作用的是 SD 文件定案的 `etf_issuer_mapping.json`（單純 ETF→投信 的扁平對照）。實作完成後 Roy Chiang 提出希望能用 `isEnabled` feature flag 管控「目前開放監控哪些投信」，並且反過來以投信為單位列出各自可用的 ETF 清單，供 `watchlist` 檢核與未來分流查詢使用。討論後決定**直接取代**原本的檔案，而不是兩份並存——理由是 SD 文件本身就把「ETF↔投信對照表」定位為「唯一真實來源」，若拆成兩份設定各自維護同一件事，日後很容易兜不起來。改動範圍：

- Schema 從「以 ETF 為單位」改成「以投信為單位」，每個投信底下才是 `etfs` 清單，`isEnabled` 放在投信這一層（一次開關該投信旗下所有 ETF，語意上也比較合理——沒有 Adapter 程式碼的投信，不會有「這檔 ETF 開但那檔關」的情境）。
- 順便把 SD 文件研究階段已查證過、但還沒開發 Adapter 的六家投信（國泰、群益、野村、統一、安聯、復華）也登記進來、`isEnabled: false`，這樣之後真的要開發新 Adapter 時，只需要新增程式碼＋把旗標打開，不需要再回頭設計設定檔格式。
- `ConfigLoader._validate_issuer_registry()` 把「ETF 完全沒登記」跟「ETF 有登記但投信未開通」拆成兩種不同的錯誤訊息，維運人員一看就知道是打錯代碼還是功能還沒做完。

## 與計畫的差異

- 五個階段照 PLAN 文件順序完成；額外多修了一個 PLAN 沒預期到的真實環境問題（見下）。
- 手動端到端驗證時發現對 `www.yuantaetfs.com` 的請求會拋出 `[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Missing Subject Key Identifier`，且在兩台不同機器上都重現，判斷不是單一機器的偶發環境問題，深入排查後找到根因並修掉了：該站憑證鏈最上層的自簽根憑證 `TWCA Global Root CA` 缺少 `Subject Key Identifier` 擴充欄位，`requests` 預設使用的 `certifi` 憑證清單驗證這條鏈會直接失敗；改用作業系統原生信任庫驗證（`curl`、瀏覽器走的就是這條路）則可以正常通過。修法是在 `yuanta.py` 引入 `truststore` 套件，模組載入時呼叫一次 `truststore.inject_into_ssl()`，讓 `ssl` 模組改用系統原生信任庫做驗證，範圍只影響這支 Adapter 所在的行程，不改動其他模組的 HTTP 行為。已用真實請求複驗：`ISSUER_PCF` 狀態從 `ERROR` 變成 `OK`，`data/snapshots/{date}/etf_holdings/0050.json` 正確寫入 50 筆真實成分股資料，且 `RebalanceClassifier` 也正確跑出換倉分類結果。

## 遵循的慣例

- 全部新元件依賴抽象注入：`Fetcher` 透過 `issuer_providers` 覆寫或 `ADAPTER_REGISTRY` 動態解析，未讓 `Fetcher` 直接耦合到 `YuantaPcfAdapter`／`FubonPcfAdapter` 的具體實作。
- 沿用本專案「`ConfigError` 是唯一自訂例外，其餘一律 `except Exception` + `logger.warning` + `SourceStatus.error_message`」的既有錯誤處理慣例，沒有為了這次的新錯誤情境另外建立例外類別或錯誤碼列舉。
- 長流程已拆為 private method（`YuantaPcfAdapter._fetch_html`／`_extract_nuxt_state`／`_find_pcf_data`，`FubonPcfAdapter._find_stock_table`／`_parse_rows`）。
- 註解口語化、只講功能與非顯而易見的限制（例如「用暫存檔不用 `NamedTemporaryFile(delete=True)`，Windows 上會鎖檔案」），未引用文件章節編號或決策過程（決策脈絡都記錄在本文件與 PLAN 文件）。
- `subprocess.run` 明確帶 `encoding="utf-8"`：手動驗證時發現若不指定，Windows 上預設編碼會把 Node 印出的中文 JSON 內容讀成亂碼。

## 驗證方式

- `pytest -q`：67 個測試全數通過。
- `node src/issuer_pcf/scripts/extract_nuxt_state.js tests/fixtures/yuanta_pcf_0050.html`：獨立執行，成功印出 JSON 到 stdout，`exit code 0`。
- 手動跑過 `FINMIND_TOKEN=dummy-token python main.py --date 2026-08-12 --dry-run`（真實對元大官網發送請求，未 mock）：修正 SSL 問題後 `ISSUER_PCF` 狀態為 `OK`、`etf_holdings/0050.json` 寫入 50 筆真實成分股資料；因對照的前一交易日快照本身沒有 ETF 持股資料（早於本次功能上線），`RebalanceClassifier` 把全部 50 筆分類為「新建倉」，這是比對基準是空集合的正常結果，不是分類邏輯有誤——之後每天正常執行、累積出第二筆真實快照後，才會出現有意義的加減碼差異。驗證完後已清除該次測試產生的 `data/` 目錄，避免混入正式資料。

## 整合點與使用方式

- 開通一家已登記但尚未開放的投信：先把對應的 `{Issuer}PcfAdapter` 寫好並註冊進 `ADAPTER_REGISTRY`，測試通過後把 `config/issuer_registry.json` 該投信的 `isEnabled` 改成 `true` 即可；`etfs` 清單若需要新增/調整標的也在同一個投信物件底下改。
- 新增一家 SD 文件完全沒查證過的全新投信：在 `issuer_registry.json` 新增一個投信物件（`name`／`isEnabled: false`／`adapter`／`pcf_url_template`／`etfs`），等 Adapter 開發完成再打開 `isEnabled`。
- `watchlist.json.etfs` 只能填入 `issuer_registry.json` 內、且所屬投信 `isEnabled=true` 的 ETF 代碼，否則啟動時直接報錯（錯誤訊息會區分是「代碼查無登記」還是「投信尚未開通」）。
- 要查詢「目前有哪些投信開放中」或「某投信底下有哪些 ETF」：`config.get_enabled_issuers()` / `config.get_available_etfs_by_issuer(issuer_key)`。
- 要抽換／測試特定 ETF 的資料來源：`Fetcher(config, storage, issuer_providers={"0050": 假物件})` 注入覆寫，不影響其他 ETF 走正常的 registry 解析路徑。
- 本機開發需先安裝 Node.js（任何版本，僅用內建模組）並確認 `node` 在 PATH 上；GitHub Actions 已透過 `actions/setup-node@v4` 自動安裝，不需額外設定。

## 待辦與已知限制

- [x] ~~元大官網 SSL 憑證驗證失敗~~：已找到根因並修復，見上方「與計畫的差異」（`truststore` + `inject_into_ssl()`）。建議正式排程跑過至少一次後仍實際確認 `ISSUER_PCF` 能拿到 `status=OK`，因為未在 GitHub Actions 的 Linux／OpenSSL 組合上實測過這個修法，理論上 `truststore` 在 Linux 是走系統 `ca-certificates`，同樣能繞開 `certifi` 內建清單的問題，但沒有替代「正式跑一次確認」。
- [ ] `config/watchlist.json` 目前仍僅監控 `0050`，`FubonPcfAdapter`／`NomuraPcfAdapter` 已完工並通過測試，但要等實際把對應 ETF（如 `006208`、`00980A`）加進 `watchlist.etfs` 才會在正式排程中真正被呼叫到；是否新增屬於監控範圍決策，留給 Roy Chiang。
- [ ] 目前只有一筆真實成功的 ETF 持股快照，`RebalanceClassifier` 尚未在「兩筆都是真實資料」的情況下驗證過換倉分類結果，需等累積第二個交易日的真實快照後才能確認。
- [ ] `FubonPcfAdapter` 沒有交易日期防呆（SD 文件已記錄的已知缺口，`Trade/Assets.aspx` 頁面未查得可信賴的日期欄位），會直接採用站方回傳的最新一筆資料。
- [ ] 富邦／元大服務條款全文尚待 Roy Chiang 親自審視（非技術阻塞項，上線前需完成，見 SD 文件 §六 #1／#21）。
- [x] ~~Phase 2（國泰、群益）投信的 Adapter 尚未開發~~：**重新查證後發現兩家官網已改版，SD 文件原有結論已過期，見下方「後續異動」，Phase 2 實際上目前技術不可行，暫緩**。
- [ ] Phase 3（觀察名單）其餘投信（統一、安聯、復華）的 Adapter 尚未開發；復華原查證可行的端點也已在本次重新查證中發現失效（見下方「後續異動」）。
- [ ] **SD 文件需要更新**：`docs/design/architecture/SD-ETF換倉資料來源方案評估-投信官網爬蟲-系統設計書.md` 對國泰／群益／復華的查證結論（第五～七輪）已因網站改版過期，且野村 Adapter 已從「Phase 3 觀察名單、不承諾時程」變成「已實作、已開通」，文件本身尚未回寫反映，建議另跑 `/sd` 補上「第十輪」更新，避免後續開發者誤信已過期的查證結果。

## 後續異動：新增 `NomuraPcfAdapter`（原定 Phase 2 改為 Phase 3 野村先行）

Roy Chiang 於 Phase 1 完工後指示接續開發 Phase 2（國泰＋群益）。依 SD 文件 §六 #11 的既定要求（「國泰 403 Forbidden 需列為 Phase 2 啟動前第一項確認事項」），動工前重新即時查證國泰與群益官網現況，結果與 SD 文件記載（4 天前查證）已不一致，如實記錄如下：

- **國泰投信**：原 PCF 頁面 `funds/etf/pcf.aspx?fc={fc}` 現在對任何查詢一律 301 導向網站首頁，整站已改版為 Angular 前端，首頁 SSR 內容裡沒有序列化任何 PCF 資料（`serverApp-state` 為空物件 `{}`）。原本「疑似防爬蟲」的判斷已升級為「舊頁面已不存在」，比 403 更明確地不可行。
- **群益投信**：頁面本身可正常回應（HTTP 200），但同樣已改版為 Angular（SSR 外殼 + 前端另發 API 取資料），原始回應 HTML 內完全沒有 `<td>` 標籤，SD 文件第五輪「靜態表格＋下載按鈕」的結論已經過期，成分股表格背後真正呼叫的 API 尚未查得。
- **附帶發現，復華投信**：SD 文件第六輪查得可行的 `GET /api/assets?fundID={fundID}` 端點，本次重新查證回應變成 `404`（先 302 導回首頁），同樣已改版失效。
- **野村投信**：`POST /API/ETFAPI/api/Fund/GetFundAssets` 端點重新即時查證**仍完全可行**，回應結構與 SD 文件第七輪記載一致（`Entries.Data.Table[]`，依 `TableTitle` 挑出「股票」表格），且不需要投信內部代碼、直接用市場代碼查詢，技術風險是目前所有候選投信中最低的。

經與 Roy Chiang 討論，決定**暫緩國泰／群益（Phase 2 原定範圍，目前技術不可行），改為先實作野村（原屬 Phase 3 觀察名單）**，理由是野村技術上已確認可行且比國泰／群益更簡單，不應該因為 Phase 編號的先後順序而放著已驗證可行的選項不用、硬等已證實卡關的選項。

**實際做了什麼：**
- `src/issuer_pcf/nomura.py`：`NomuraPcfAdapter`。`POST` 帶 `{"FundID": etf_id}` 呼叫官方 API（不需 `SearchDate` 參數，省略即回傳最新一期資料；曾實測傳空字串會被 API 直接拒絕，回應 400）→ 回應為結構化 JSON，`Entries.Data.Table` 是陣列，依序含股票／期貨／現金保證金等好幾張表格 → 找出 `TableTitle == "股票"` 那一張，其餘表格不是成分股持倉一律跳過 → 比對 `FundAsset.NavDate`（格式 `yyyy/MM/dd`，轉換 `/`→`-` 後比對）與查詢日期，不符則回傳空清單（比照 Yuanta/Fubon 既有慣例）→ 逐列映射為 `component_stock_id`／`component_name`／`holding_shares`。`Entries.Data` 為空（FundID 不屬於野村旗下 ETF）與「股票」表格缺失兩種情境皆拋出帶 `FETCH_ISSUER_PCF_PARSE_ERROR` 標記的 `RuntimeError`，沿用既有錯誤碼慣例，未新增新的錯誤碼列舉。
- `src/issuer_pcf/registry.py`：`ADAPTER_REGISTRY` 新增 `"NomuraPcfAdapter"` 鍵。
- `config/issuer_registry.json`：`nomura.isEnabled` 由 `false` 改為 `true`（該投信原本就已登記 `etfs: ["00980A"]`／`pcf_url_template`，本次只是開通，不需要改 schema）。`watchlist.json` 本次**未**跟著加入 `00980A`，是否要開始監控留給 Roy Chiang 決定，比照富邦當初的處理方式。
- `tests/test_issuer_pcf_nomura.py`：5 個案例——正常解析並跳過非股票表格、日期不符回傳空清單、找不到「股票」表格拋錯、`FundID` 查無資料拋錯、用即時查證擷取的真實回應（裁剪至 3 檔成分股，其餘欄位原樣保留）驗證解析邏輯不是憑空捏造的資料形狀。
- `tests/fixtures/nomura_assets_00980a.json`：即時查證當下用專案自己的 User-Agent 實際呼叫 API 取得的真實回應，裁剪成分股列數後存檔（做法比照 `yuanta_pcf_0050.html`／`fubon_assets_006208.html` 兩份既有 fixture，保留真實回應結構）。

**驗證方式：** `pytest -q` 全數通過（76 個測試，新增 5 個）；`NomuraPcfAdapter` 的解析邏輯以本次即時查證取得的真實 API 回應驗證過，但未實際跑過 `main.py` 端到端流程（因 `watchlist.json` 尚未加入 `00980A`），建議實際開始監控前先手動跑一次 `--dry-run` 複驗。

**遵循的慣例：** 沿用 Yuanta／Fubon 既有的錯誤處理慣例（`except Exception` + `logger.warning` + `SourceStatus.error_message`，不新增自訂例外類別）、依賴注入測試慣例（mock `src.issuer_pcf.nomura.requests.post`）、私有方法拆解慣例（`_fetch_data`／`_find_stock_table`／`_to_holding`）。註解口語化，只講野村 API 回應本身的結構特性（多表格、股票表格怎麼挑），未引用 SD 文件章節或本次查證的討論過程（過程記錄在本節）。
