# 實作完成說明：三大法人分級告警機制 v1

## 實際做了什麼

依 [PLAN-三大法人分級告警機制-v1.md](../plan/PLAN-三大法人分級告警機制-v1.md) 一次到位完成第一輪＋第二輪內容：

- `src/models.py`：新增 `DataSourceKey`／`AlertScope`／`AlertTriggerType`／`MarketCapTier` 四個 enum，以及 `InstitutionalTradeRecord`／`StockDailyTrading`／`StockCapitalSnapshot`／`MarketInstitutionalRecord`／`InstitutionalAlert` 五個 dataclass。
- `src/config.py`：新增 `is_broker_monitoring_enabled()`、`get_volume_ratio_threshold()`、`get_market_cap_tier_bounds()`、`get_tiered_amount_threshold()`、`get_market_institutional_threshold()`；`_validate()` 新增對 `institutional_tiered`／`market_institutional` 兩個新區塊的必填檢查。
- `src/storage.py`：新增四種新檔案類型（`institutional_trades`、`stock_trading`、`market_institutional`、`institutional_alerts`）的讀寫方法，以及獨立於日期快照之外的股本快取（`data/reference/capital_stock/{stock_id}.json`）。
- `src/fetcher.py`：`FinMindClient` 新增 `fetch_institutional_trades`／`fetch_stock_trading`／`fetch_capital_stock`／`fetch_market_institutional` 四個方法；`Fetcher` 對應新增抓取與例外容錯邏輯，並實作股本快取的新鮮度判斷。
- `src/analyzer.py`：新增 `InstitutionalTieredFilter`（個股門檻1 OR 門檻2 判斷、市值分級、金額估算）與 `MarketInstitutionalFilter`（大盤三法人各自獨立判斷）。
- `src/notifier.py`：`MessageFormatter` 改為輸出「大盤三大法人動態」「三大法人買賣超（個股）」「ETF 換倉動態」三區塊，個股區塊依觸發類型標示 `[量能異常]`／`[大額進出]`／`[量能異常＋大額進出]`。
- `main.py`：串接新的抓取→分析（個股雙門檻＋大盤門檻）→格式化→推播流程；新增 UTF-8 輸出保護（見下方「實作中發現並修正的問題」）。
- `config/thresholds.json`、`config/broker_branches.json`：依新 schema 更新，門檻數值採用 SA/SD 文件定案的值（個股成交量佔比 15%、市值分級 30/5/1 億、大盤外資/投信/自營商 200/30/50 億）。
- 更新／新增 `tests/test_config.py`、`tests/test_analyzer.py`、`tests/test_notifier.py`、`tests/test_storage.py`，`pytest` 全數 40 項通過。

## 與計畫的差異

- 無重大差異，PLAN 中預先聲明的兩項技術細節（股本快取新鮮度以 90 天 heuristic 判斷、股票中文名稱先用靜態對照表）皆照原計畫實作。

## 實作中發現並修正的問題

以下兩點是撰寫規格時沒發現、實際跑過真實資料才浮現的問題，順手修掉了：

1. **簡報中的全形中括號在 Windows 中文主控台會讓程式直接崩潰。** 原本用 `［大額進出］` 這種全形符號，本機 `python main.py --dry-run` 執行到 `print()` 時丟出 `UnicodeEncodeError`（cp950 codec 無法編碼 `［`）。已改用半形 `[大額進出]`，同樣清楚易讀且不受任何主控台編碼限制。
2. **`print()` 在此環境下會用系統代碼頁（Big5）輸出，中文字元會被無聲替換／截斷，重導向到檔案也一樣壞。** 這比第 1 點更嚴重：就算避開全形符號，其他中文字元組合仍可能在特定代碼頁下遺失資訊，讓 `--dry-run` 這個「印出來看內容對不對」的功能名不副實。已在 `main.py` 加上 `_ensure_utf8_output()`，程式一啟動就把 `stdout`／`stderr` 強制改為 UTF-8 輸出（不支援 `reconfigure` 的環境會靜靜跳過，不影響既有行為）。

兩項都已用 `python main.py --date 2026-08-04 --dry-run` 對真實 FinMind 資料實測驗證，輸出重導向到檔案後內容完整無亂碼。

## 端到端驗證紀錄

用專案既有 `.env`（真實 `FINMIND_TOKEN`）對 `2026-08-04`（已知有效交易日）執行 `python main.py --date 2026-08-04 --dry-run`，結果：

```
【籌碼監控日報】2026-08-04

◆ 大盤三大法人動態
  投信單日買超 271.7 億元
  自營商單日賣超 194.3 億元

◆ 三大法人買賣超（個股）
  2330 台積電 [量能異常＋大額進出]
    外資 -12,430 張／投信 +434 張／自營商 -2,327 張
    估算金額：賣超 332.3 億元（市值分級：大型）

（本訊息由籌碼監控引擎自動產生，個股金額為估算值）
```

另外確認：
- `data/snapshots/2026-08-04/_meta.json` 的 `sources` 正確使用新的 `DataSourceKey`（`FINMIND_INSTITUTIONAL`／`FINMIND_PRICE`／`FINMIND_BALANCE_SHEET`／`FINMIND_MARKET`／`TWSE_PCF`），且因 `broker_branches.json.enabled=false`，`FINMIND_BROKER` 這把 key 確實沒有出現。
- `data/reference/capital_stock/2330.json`、`2454.json` 正確寫入股本與估算發行股數（台積電股本 2,593.2 億元，估算發行股數約 259.3 億股，與公開資訊量級相符）。
- 對不同日期執行第二次後，股本快取的 `fetched_at` 沒有變化，確認 90 天新鮮度判斷有效攔下重複呼叫 `TaiwanStockBalanceSheet`。
- 證交所 PCF 依舊回報既有已知錯誤（`Expecting value: line 1 column 1`），不影響其餘流程，符合 SA/SD 文件「本次不處理」的範疇界定。

## 遵循的慣例

- 沿用既有的「資料源獨立 try/except、單一失敗不中斷全局」容錯模式，新資料源比照辦理。
- 沿用既有的 `dataclass` + `SnapshotRepository` 存取模式，新檔案類型的讀寫方法命名比照既有慣例。
- `InstitutionalTieredFilter`／`MarketInstitutionalFilter` 拆成獨立小方法（`_evaluate_one`、`_pick_trigger_type`、`_classify_tier` 等），避免單一方法塞入過多判斷邏輯。
- `ConfigLoader` 保持「設定檔驗證失敗即中止、不猜測預設值」的既有原則，新增區塊比照既有欄位驗證方式處理。

## 待辦與已知限制

- [ ] **股票中文名稱目前用靜態對照表**（`src/fetcher.py` 的 `_STOCK_NAME_FALLBACK`，僅含 `2330`／`2454`）。原因：`TaiwanStockInstitutionalInvestorsBuySell`／`TaiwanStockPrice` 兩個新資料集都不含股票名稱欄位，與舊分點資料集不同，這點在 SA/SD 撰寫時沒被注意到。監控清單擴增時需改為透過 `TaiwanStockInfo` 動態查詢（可仿照股本快取的做法，做成不常變動的獨立快取）。
- [ ] 分點功能（`BrokerFilter`、`fetch_broker_trades`）程式碼保留但完全未接回 `main.py` 的訊息輸出流程，即使 `broker_branches.json.enabled=true` 也一樣（因為新簡報格式沒有分點區塊的設計）。日後要復用需要另外設計呈現方式。
- [ ] `TwsePcfClient`／ETF 換倉監控維持既有已知失效狀態，本次未處理（範疇外）。
- [ ] 股本快取 90 天新鮮度為簡化 heuristic，並非精確對照財報法定截止日；SD 文件已列為待確認事項，非本次阻塞項。
