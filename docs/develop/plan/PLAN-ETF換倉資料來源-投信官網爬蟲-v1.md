# Implementation Plan: ETF PCF 資料來源改造（投信官網爬蟲）v1

## Overview

依 [SD-ETF換倉資料來源方案評估-投信官網爬蟲-系統設計書.md](../../design/architecture/SD-ETF換倉資料來源方案評估-投信官網爬蟲-系統設計書.md) 實作 Phase 1（元大投信＋富邦投信），將已證實無穩定端點的 `TwsePcfClient`（證交所 PCF API）替換為依 ETF 對應投信、逐一呼叫該投信官網頁面的 Adapter 架構。SD 文件已歷經 9 輪查證與 Roy Chiang 拍板，所有技術路線（URL、HTML/JS 結構、Node.js 解析可行性）動工前已驗證完畢，本次是「照設計落地」的實作。

## Approach

1. `src/issuer_pcf/base.py` 先定義 `IssuerPcfProvider` 抽象介面，作為兩個 Adapter 與 `Fetcher` 之間的共同語言。
2. `config/etf_issuer_mapping.json` ＋ `src/config.py` 擴充，把「哪個 ETF 用哪個投信/Adapter」外部化成設定，`ConfigLoader._validate()` 新增白名單檢查，未受支援的 ETF 在啟動階段就擋下，不會進到抓取流程才失敗。
3. 兩個 Adapter 各自獨立開發、獨立測試，彼此不互相依賴：
   - `YuantaPcfAdapter`：HTTP 取回 HTML 後，改以 Node.js 子行程（`extract_nuxt_state.js`，只用內建 `vm`／`fs`，無 npm 相依）解析頁尾 `window.__NUXT__` 狀態，取得完整成分股清單。這是本專案第一次引入 Node.js 執行期相依。
   - `FubonPcfAdapter`：純 `BeautifulSoup` 解析靜態 HTML，鎖定「股票」標題後的表格。
4. `src/issuer_pcf/registry.py` 提供 adapter 鍵字串到類別的對照，`Fetcher._resolve_issuer_provider()` 依設定檔動態查表，取代原本寫死呼叫 `TwsePcfClient` 的方式。
5. `Fetcher` 建構子的 `twse_client` 參數改為 `issuer_providers`（依 ETF 代碼索引的注入點），維持原有「可注入假物件測試」的慣例；`_fetch_etf_holdings` 的 try/except/fetched_any 迴圈結構完全不變，只替換中間解析資料來源的那一步。
6. 測試策略採「mock 單元測試 + 真實 fixture 整合測試」雙軌：兩個 Adapter 都用手動裁剪過的真實頁面存檔（`tests/fixtures/`）驗證解析邏輯，而不是憑空手寫簡化 HTML，避免漏掉真實頁面的結構細節（例如富邦頁面同時有期貨/股票/債券三張同構表格）。Yuanta 的整合測試會真的呼叫 `node`，用 `skipif(shutil.which("node") is None)` 保護沒裝 Node 的環境。
7. CI workflow 新增 `actions/setup-node@v4`，明確 pin Node 版本，取代原本隱含依賴 runner image 內建 Node 的做法。

## File Structure

```
src/issuer_pcf/
├─ __init__.py
├─ base.py                    # IssuerPcfProvider 抽象介面
├─ yuanta.py                  # YuantaPcfAdapter
├─ fubon.py                   # FubonPcfAdapter
├─ registry.py                # ADAPTER_REGISTRY
└─ scripts/
   └─ extract_nuxt_state.js   # Node 子行程，解析元大頁面 SSR 狀態

config/etf_issuer_mapping.json

tests/
├─ test_issuer_pcf_yuanta.py
├─ test_issuer_pcf_yuanta_integration.py
├─ test_issuer_pcf_fubon.py
└─ fixtures/
   ├─ yuanta_pcf_0050.html
   └─ fubon_assets_006208.html
```

修改既有檔案：`src/models.py`（`DataSourceKey.TWSE_PCF` → `ISSUER_PCF`）、`src/config.py`、`src/fetcher.py`（移除 `TwsePcfClient`）、`requirements.txt`（新增 `beautifulsoup4`）、`tests/test_config.py`、`tests/test_fetcher.py`、`.github/workflows/daily-chip-monitor.yml`、`README.md`。

## Guidelines Followed

本專案 `docs/reference/guidelines/` 仍為空，無既有 GUIDELINES 文件可遵循，改以 SD 文件的元件設計與既有程式碼慣例（`ConfigError` 為唯一自訂例外、`except Exception` + `logger.warning` + `SourceStatus.error_message` 的錯誤處理模式、`unittest.mock.patch("模組.requests.get", ...)` 的 HTTP mock 慣例）作為依據，未發生偏離既定慣例的情形。

## 方案比較與決策

- **元大成分股怎麼解析（核心技術決策）**：實測發現原始 HTML 只渲染 5 檔成分股，完整清單在頁尾 `window.__NUXT__` 這包 Nuxt.js 參數去重壓縮狀態裡，不是乾淨 JSON。考慮過 (a) Python 呼叫 Node.js 子行程在沙箱解析、(b) 反查「匯出excel」按鈕的下載端點、(c) 引入 Headless Browser 執行 JS。(b) 在 SD 查證階段已確認頁面渲染期 8 支候選 API 都不是匯出端點，價值存疑；(c) 違反本專案「輕量爬蟲、不用 Headless Browser」的既定原則。最終選 (a)，已用 0050／0056 兩檔驗證可完整取回 50/50 筆持股，經 Roy Chiang 拍板採用，代價是本專案首次引入 Node.js 執行期相依（GitHub Actions `ubuntu-latest` 預設內建，本機開發需自行安裝）。
- **Node 子行程的資料傳遞方式：暫存檔 vs stdin**：選擔存檔（`tempfile.mkstemp`），不用 `NamedTemporaryFile(delete=True)`——後者在 Windows 上檔案還被 Python 端開著時，子行程開不了同一個檔案，會導致解析失敗；用 `mkstemp` 自己控制檔案的開關時機比較保險，並在 `finally` 區塊確保清除。
- **富邦是否比照元大做交易日期防呆**：`Assets.aspx` 頁面沒有查證到像元大 `trandate` 那樣明確可信賴的日期欄位，決定不硬做一個沒驗證過的比對邏輯，如實接受站方預設回傳的最新一筆資料，記錄為已知限制而非假裝解決。
- **`Fetcher` 建構子的注入點命名**：`twse_client` 單數（因為原本只有一個資料源）改為 `issuer_providers` 複數字典（依 ETF 代碼索引），因為現在同時有元大／富邦兩種 Adapter，測試需要能個別覆寫特定 ETF 對應的假物件。
- **測試 fixture 來源**：兩份 fixture 皆取自設計階段已透過 `curl` 實際下載存檔的真實頁面內容裁剪而成，不是憑空手寫的簡化 HTML；元大的 `window.__NUXT__` 運算式本身逐字保留（裁剪會破壞參數去重的索引對應），只裁掉外圍無關的 HTML；富邦則因為是純靜態表格，可以安全裁剪資料列數，但保留三張表格的真實結構與 class 名稱。

## Estimated Effort

- Planning：沿用已核准的 SD 文件，另外用 Explore／Plan 子代理各跑一輪確認既有程式碼慣例與細節設計，約 0.5 hr
- Implementation：5 個階段（Config 基礎 → 元大 Adapter → 富邦 Adapter → Fetcher 整合 → CI/文件），核心程式碼 8 個新檔案＋6 個修改檔案
- Testing：3 支新測試檔（yuanta 單元／yuanta 整合／fubon）＋2 支既有測試檔擴充（config／fetcher），共新增 15 個測試案例；全套 `pytest -q` 64 個測試全數通過
