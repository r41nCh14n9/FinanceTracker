# 實作完成說明書：每日完整籌碼報告與漲跌停監控 v1

依 [PLAN-每日完整籌碼報告與漲跌停監控-v1.md](../plan/PLAN-每日完整籌碼報告與漲跌停監控-v1.md) 五個階段全數完成並通過測試（263 個單元測試，含本次新增約 90 個）。

## 實際做了什麼

### Phase 0：既有功能調整
- `config/thresholds.json` 新增 `institutional_tiered.threshold_multiplier: 1.5`
- `src/config.py`：`get_volume_ratio_threshold()`／`get_tiered_amount_threshold()` 套用倍率，選填欄位缺省為 1.0
- `src/classification.py` 新增 `group_by_first_concept()`（分組工具）、`display_industry()`、`TIER_LABELS`、`build_classification_tags()`（皆從 `notifier.py` 抽出，見下方「與計畫的差異」）
- `src/notifier.py`：個股買賣超區塊、ETF 換倉動態區塊統一改為「依第一個概念分組＋顯示 `[分類]` 標題」

### Phase 1：漲跌停掃描模組
- `src/models.py` 新增 `MarketType`／`LimitType`／`LimitUpDownRecord`，`DataSourceKey` 新增兩成員
- `src/market_quote/base.py`／`twse.py`／`tpex.py`：兩個 Provider＋共用數字解析工具
- `src/limit_scanner.py`：`calculate_limit_prices()`／`evaluate_limit_type()`／`LimitScanner`

### Phase 2：完整版報告產出模組
- `src/storage.py` 新增 `write_limit_up_down()`／`read_limit_up_down()`／`write_daily_report_md()`／`read_institutional_alerts()`／`read_rebalance_events()`
- `src/report_generator.py`：`ReportGenerator.generate()`，watchlist 全量、漲跌停清單、ETF 換倉三段皆依概念分組

### Phase 3：CLI 拆分與短網址
- `src/link_publisher.py`：`LinkPublisher.shorten()`
- `src/notifier.py`：`MessageFormatter.format()`／`Notifier.notify()` 新增選填 `report_link`
- `main.py`：新增 `--skip-notify`／`--notify-only`／`--report-url`（前兩者互斥）、`run_notify_only()`、`_scan_limit_up_down()`、`_fetch_limit_institutional_trades()`、`_write_daily_report()`

### Phase 4：排程與本機腳本整合
- `.github/workflows/daily-chip-monitor.yml`：拆成 Prepare／Commit（`continue-on-error`）／Notify／失敗判斷四步驟
- `scripts/run.sh` 新增 `skip-notify`／`notify-only` 模式
- `docker/crontab` 同步改為三段式呼叫

## 與計畫的差異

1. **`_meta.json` 未新增 `TWSE_MARKET_QUOTE`／`TPEX_MARKET_QUOTE` 來源狀態追蹤**：`LimitScanner` 內部已對單一市場失敗做容錯（記錄 Log、略過），但目前不會把個別市場的成功/失敗狀態寫回 `_meta.json`。原因：`LimitScanner.scan()` 對外只回傳合併後的 `LimitUpDownRecord` 清單，要額外暴露逐市場狀態需要調整其回傳介面，評估後認為這屬於可觀測性的錦上添花、非功能性必要項，先以 Log 呈現，列入待辦。
2. **`_classification_tags`／`_display_industry`／市值分級標籤從 `notifier.py` 抽到 `classification.py`**：計畫書原先沒有明確提到這個重構，是實作 `ReportGenerator` 時發現兩邊需要完全相同的分類標籤組版邏輯才追加的，避免同一段邏輯維護兩份。`notifier.py` 現在改為呼叫 `classification.build_classification_tags()`，行為與原本完全一致（含 `_format_stock_alert_line` 查無市值分級時顯示「未知」、ETF 換倉事件一律不顯示市值分級等既有規則），已有測試覆蓋，非破壞性變更。
3. **`docker/crontab` 與 `daily-chip-monitor.yml` 的排程時間不一致**：`docker/crontab` 先前被手動改成台灣時間 20:00，但 `daily-chip-monitor.yml` 目前排程仍是 `30 11 * * 1-5`（UTC，即台灣 19:30），本次未對此做取捨判斷（不屬於這次「CLI 拆分＋排程步驟重排」的範疇），維持兩者現狀，僅同步更新兩邊的**指令內容**（三段式呼叫）。**待確認：** 這個時間差異是否需要收斂成同一個值。

## 遵循的慣例

- 新元件（`market_quote/`、`limit_scanner.py`、`report_generator.py`、`link_publisher.py`）皆比照既有 `issuer_pcf/` Provider 介面慣例：薄封裝、模組層級常數、`raise_for_status()`＋例外邊界在呼叫端處理
- `main.py` 的旗標分流沿用 `--purge` 既有設計語言（獨立旗標、各自可單獨呼叫、無旗標時維持原行為）
- 測試風格沿用既有慣例：`tmp_path` fixture、`unittest.mock.patch`、中文註解只在情境不明顯時補充「為什麼」

## 整合點與使用方式

- 本機測試漲跌停判定：`pytest tests/test_limit_scanner.py`
- 本機預覽完整版報告：`python main.py --date {日期} --skip-notify` 後查看 `data/reports/{日期}/daily_report.md`
- 本機測試兩階段推播：先 `--skip-notify` 再 `--notify-only --dry-run`（可加 `--report-url` 測試短網址）
- 兩者皆已於真實環境（真實 FinMind／TWSE／TPEx／TinyURL API）驗證過一次完整跑通，非僅限單元測試

## 待辦事項與已知限制

- [ ] `_meta.json` 尚未追蹤 `TWSE_MARKET_QUOTE`／`TPEX_MARKET_QUOTE` 個別來源狀態（見上方差異說明 1）
- [ ] `docker/crontab`（20:00）與 `daily-chip-monitor.yml`（19:30）排程時間不一致，需要與 Roy Chiang 確認是否要統一
- [ ] SD 文件§六「殘餘差異」提及之 GitHub Actions 實際部署驗證（本次已在本機 Python 3.11 原生環境＋Docker Linux 容器環境雙重驗證 TPEx 不會重現 TLS 問題，但尚未在真正的 GitHub Actions runner 上跑過一次 `workflow_dispatch`）
- [ ] 新股掛牌前五個交易日無漲跌幅限制的已知限制（`limit_scanner.py` 已有文件註解說明，不會誤判，純粹是這類股票不會被本功能捕捉）
