# 階段性實作計畫：每日完整籌碼報告與漲跌停監控 v1

## 概述

依 [SA-每日完整籌碼報告與漲跌停監控-功能模組分析.md](../../analysis/requirements/SA-每日完整籌碼報告與漲跌停監控-功能模組分析.md) 與 [SD-每日完整籌碼報告與漲跌停監控-系統設計書.md](../../design/architecture/SD-每日完整籌碼報告與漲跌停監控-系統設計書.md) 實作：全市場漲跌停監控、Watchlist 完整版報告（Markdown）、GitHub 短網址推播。SD 文件第六章四項待確認事項皆已確認（含實測驗證 TPEx TLS 問題不會在正式環境重現），無阻塞項。

實作前使用者另外提出兩項對**既有已上線功能**（三大法人買賣超通知）的調整，目的是降低訊息量與呼叫成本，一併納入本次實作（決策脈絡見下方「Phase 0」）。

## Phase 0：既有功能調整（門檻倍率化＋排版改為概念分組）

### 為什麼先做這階段

這是對現有 production 邏輯的參數/格式調整，範圍小、風險低、獨立於後續新功能之外，且是使用者這次最先關心（降低雜訊）的部分，先完成可以儘早讓使用者確認格式符合預期，再繼續後面工程量較大的新功能開發。

### 決策脈絡（完整記錄於此，不寫進程式註解）

**門檻倍率化：**
- 原本考慮直接把 `institutional_tiered` 底下的門檻改成使用者指定的絕對值，但使用者表示無法一次決定精確數值，希望改為「倍率」機制，之後可以隨時調整這一個數字，不用重算每個門檻。
- 決定新增 `institutional_tiered.threshold_multiplier`（選填，預設 `1.0`，不影響既有未設定此欄位的環境），套用於 `volume_ratio_pct` 與 `amount_thresholds.{large,mid,small}`；`market_cap_tiers`（市值分級門檻本身、非買賣超門檻）與大盤 `market_institutional`、ETF `etf_rebalance_pct` **不**套用此倍率——使用者原始需求明確是「關注股票（個股）三大法人買賣超」，範圍不包含大盤或換倉判定。
- 本次先設定為 `1.5`。

**排版改為「依概念分組＋顯示 `[分類]` 標題」：**
- 使用者提供的期望格式範例，本質是把現行 ETF 換倉動態區塊「依產業靜默分組」的模式，換成「依概念分組＋印出可視標題」，並套用到目前完全平鋪、沒有任何分組的個股買賣超區塊。
- 討論過「個股同時符合多個概念時怎麼處理」：確認分組只取**第一個**概念（避免同一檔股票在報告中重複出現好幾次），但每行後面的 `[]` 標籤內容維持現狀不變（產業別＋市值分級＋**全部**概念標籤都列出，不因為某個概念已經拿去當分組依據就從 `[]` 省略）——分組依據跟呈現內容是兩件事，各自獨立。
- 確認 ETF 換倉動態區塊也要一併改成同一套邏輯（依概念分組＋顯示標題），與個股區塊風格一致；因此兩個區塊可以共用同一套「依第一個概念分組＋分頁安全 block 產生」邏輯，避免寫兩份幾乎一樣的程式碼。
- 查無概念分類的股票／成分股統一歸入「未分類」，且一律排在該區塊最後（沿用現行 `_group_and_format_events()` 對「查無產業別」的處理慣例：組間順序＝清單中各分類第一次出現的順序，未分類固定殿後）。

### 涉及檔案

| 檔案 | 異動 |
| :--- | :--- |
| `config/thresholds.json` | 新增 `institutional_tiered.threshold_multiplier: 1.5` |
| `src/config.py` | `get_volume_ratio_threshold()`／`get_tiered_amount_threshold()` 套用倍率；`_validate_institutional_tiered()` 允許此欄位選填，缺省視為 `1.0` |
| `src/classification.py` | 新增共用函式 `group_by_first_concept(stock_ids, concept_map)`：依股票在 `concept_map`（`invert_category_table()` 產出的多值反查表）中的**第一個**概念分組，回傳分組後的 `stock_id` 順序清單＋每組標籤；查無概念者歸入 `None`（呼叫端顯示為「未分類」），統一排最後 |
| `src/notifier.py`（`MessageFormatter`） | 新增共用私有方法（如 `_build_grouped_blocks()`），依 `group_by_first_concept()` 的分組結果，比照現行「大標題黏第一組第一筆、組標題黏各組第一筆、其餘各自成 block」的分頁安全模式，產生一組 block；`_build_stock_section()`／`_build_etf_rebalance_section()` 改為呼叫這個共用方法，各自傳入自己的「單筆格式化函式」（`_format_stock_alert_line`／`_format_single_event`，皆維持不變） |
| `tests/test_config.py` | 新增門檻倍率相關測試（有設定值時套用倍率、未設定時預設 1.0 不影響既有行為） |
| `tests/test_classification.py` | 新增 `group_by_first_concept()` 單元測試（含多重概念只取第一個、查無概念歸最後等情境） |
| `tests/test_notifier.py` | 更新既有測試對應新排版（含 `[分類]` 標題行），新增分組相關情境（多概念、無概念、分頁邊界跨分類） |

### 驗收方式

- `pytest tests/test_config.py tests/test_classification.py tests/test_notifier.py -q` 全數通過
- `python main.py --date {某個已有快照的日期} --dry-run` 實際印出訊息，人工核對排版與 SA/SD 文件範例一致

---

## Phase 1：漲跌停掃描模組（LimitScanner）

### 涉及檔案

| 檔案 | 異動 |
| :--- | :--- |
| `src/models.py` | 新增 `MarketType`／`LimitType`／`LimitUpDownRecord`；`DataSourceKey` 新增 `TWSE_MARKET_QUOTE`／`TPEX_MARKET_QUOTE` |
| `src/market_quote/base.py`（新增） | `MarketQuoteProvider` 抽象介面，比照 `src/issuer_pcf/base.py` 之 `IssuerPcfProvider` 設計語言 |
| `src/market_quote/twse.py`（新增） | `TwseQuoteProvider`，呼叫 TWSE `MI_INDEX` |
| `src/market_quote/tpex.py`（新增） | `TpexQuoteProvider`，呼叫 TPEx 上櫃股票每日收盤行情端點（`otc_quotes_no1430`） |
| `src/limit_scanner.py`（新增） | `calculate_limit_prices()`／`evaluate_limit_type()`（純函式，依 SD 文件§四漲跌停判定規則）、`LimitScanner.scan()`（協調兩個 Provider，per-source try/except） |
| `tests/test_market_quote_twse.py`／`test_market_quote_tpex.py`（新增） | 比照現有 `tests/test_issuer_pcf_*.py` 風格，mock HTTP 回應驗證解析邏輯 |
| `tests/test_limit_scanner.py`（新增） | 漲跌停判定規則單元測試（含跳動點位邊界、新股無前收盤價等情境） |

### 驗收方式

- 單元測試涵蓋 SD 文件附的判定規則虛擬碼
- 本機以真實資料源跑一次 `LimitScanner.scan()` 確認實際回應格式與預期一致

---

## Phase 2：完整版報告產出模組（ReportGenerator）

### 涉及檔案

| 檔案 | 異動 |
| :--- | :--- |
| `src/storage.py` | 新增 `write_limit_up_down()`／`read_limit_up_down()`／`write_daily_report_md()`；新增 `read_institutional_alerts()`／`read_rebalance_events()`（現行僅有寫入方法，Phase 3 的 `--notify-only` 會需要） |
| `src/report_generator.py`（新增） | `ReportGenerator.generate()`，沿用 Phase 0 的 `group_by_first_concept()` 產生 Markdown（依 SD 文件§四報告版面） |
| `tests/test_storage.py` | 新增對應讀寫方法測試 |
| `tests/test_report_generator.py`（新增） | 報告內容與分組結果驗證 |

### 驗收方式

- 單元測試 + `--dry-run` 人工核對產出的 `daily_report.md` 內容

---

## Phase 3：`main.py` CLI 拆分＋短網址附加

### 涉及檔案

| 檔案 | 異動 |
| :--- | :--- |
| `src/link_publisher.py`（新增） | `LinkPublisher.shorten()`：TinyURL 優先、失敗改 is.gd、兩者皆失敗回退原始長網址 |
| `src/notifier.py` | `MessageFormatter.format()`／`Notifier.notify()` 新增選填 `report_link` 參數 |
| `main.py` | 新增 `--skip-notify`／`--notify-only`／`--report-url` 旗標（互斥群組）；`run()` 移除流程尾端直接呼叫 `Notifier`，改依旗標分流；新增 `run_notify_only()` |
| `tests/test_link_publisher.py`（新增） | 兩層降級邏輯測試 |
| `tests/test_main.py` | 新增旗標分流、`run_notify_only()` 相關測試 |
| `tests/test_notifier.py` | 新增 `report_link` 附加訊息的測試 |

### 驗收方式

- 單元測試 + 本機模擬兩階段呼叫（`--skip-notify` 後接 `--notify-only`）確認讀回資料一致

---

## Phase 4：Workflow 與本機腳本整合

### 涉及檔案

| 檔案 | 異動 |
| :--- | :--- |
| `.github/workflows/daily-chip-monitor.yml` | 步驟重排（依 SD 文件§四 Orchestration，含 `continue-on-error` 風險緩解設計） |
| `scripts/run.sh` | 新增 `notify-only` 模式，供本機測試兩階段流程 |
| `docker/crontab` | 同步改為三段式呼叫（`--skip-notify` → `--purge` →〔本機排程不含 git push，故不產生短網址〕`--notify-only`，不帶 `--report-url`） |
| `config/watchlist.json`／新增設定 | 視 Phase 1 實測結果，確認是否需要漲跌停判定的排除清單等（若無則不異動） |

### 驗收方式

- Workflow YAML 語法檢查（`actionlint` 或人工核對）
- 本機以 `--date` 補跑一個已知日期，確認 commit 前後順序、短網址、通知內容皆正確

---

## 整體風險與待觀察事項

- Phase 0 的排版重構影響現行**每日都在運作**的通知功能，需確保既有測試全數通過且 `--dry-run` 人工核對過一次，才進入後續 Phase（避免把新功能的 bug 跟既有功能的回歸問題混在一起除錯）。
- Phase 1 的 TPEx 呼叫已於 SD 文件§六實測確認正式環境不會重現 TLS 問題，但仍建議 Phase 4 實際跑一次 GitHub Actions（`workflow_dispatch` 手動觸發）做最終確認。
- 各 Phase 之間有依賴順序（2 依賴 0、1；3 依賴 2；4 依賴 3），依序完成，不跳階段。
