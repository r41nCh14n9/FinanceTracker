# IMPL-ETF換倉歷史日期回補機制-v1

依據 [SD-ETF換倉歷史日期回補機制-系統設計書.md](../../design/architecture/SD-ETF換倉歷史日期回補機制-系統設計書.md)（第七輪定案）實作。

## 實際做了什麼

**元大改採 API 架構**
- `src/issuer_pcf/yuanta.py` 整支重寫：改呼叫 `etfapi.yuantaetfs.com` 的 `PCF/Daily` JSON API，帶 `ticker`／`date` 查詢參數，比對回應 `PCF.trandate` 是否等於查詢日期，解析 `InKind.FundComposition` 為持股清單。原本抓 HTML、寫暫存檔、呼叫 `subprocess.run(["node", ...])` 解析 `__NUXT__` 狀態的流程整段移除。
- 刪除 `src/issuer_pcf/scripts/extract_nuxt_state.js`（不再被呼叫）與 `tests/fixtures/yuanta_pcf_0050.html`（HTML 端對端測試不再需要）。
- `tests/test_issuer_pcf_yuanta.py` 改寫為對 `PCF/Daily` 回應的 mock 測試；`tests/test_issuer_pcf_yuanta_integration.py`（原本驗證 Node 子行程真的能跑）已無對應標的，整份刪除。
- `.github/workflows/daily-chip-monitor.yml` 移除 `actions/setup-node` 步驟；`README.md` 同步移除 Node.js 安裝說明與目錄結構描述。
- `config/issuer_registry.json` 的元大 `pcf_url_template` 更新為新端點（純文件用途，程式不會讀這個值決定實際呼叫哪支 API）。

**各投信 `SUPPORTS_BACKFILL` 宣告**
- `src/issuer_pcf/base.py`：`IssuerPcfProvider` 新增 `SUPPORTS_BACKFILL: ClassVar[bool] = False`。
- `CapitalPcfAdapter`／`FuhwaPcfAdapter`／`YuantaPcfAdapter`：`SUPPORTS_BACKFILL = True`（程式碼已具備日期參數與可驗證欄位）。
- `NomuraPcfAdapter`：請求 body 加入 `SearchDate`（既有的 `NavDate` 比對邏輯本來就有，不用新寫），新增 `SUPPORTS_BACKFILL = True`。
- 統一／國泰／富邦維持預設 `False`，不覆寫。

**就近一日回補機制**
- `src/fetcher.py` 新增 `Fetcher.resolve_backfill_trading_day()`：本地已有交易日快照優先採用；本地完全無歷史快照時，逐日（上限 `_BACKFILL_LOOKBACK_DAYS_MAX = 10` 天）輕量呼叫 `FinMindClient.fetch_institutional_trades()` 對單一監控股票確認候選日期是否為交易日。
- `src/fetcher.py` 新增 `Fetcher.ensure_etf_holdings()`：本地已有前一天快照直接讀；沒有的話僅在 `SUPPORTS_BACKFILL=True` 時即時補抓，沿用既有 `_is_holding_count_anomaly()` 健全性檢查（比對對象是「再前一天」的筆數），成功則落地＋局部更新 `_meta.json`；不支援、查無資料、逾時、解析異常、健全性檢查未通過，一律回傳空清單。
- `src/storage.py` 新增 `SnapshotRepository.upsert_meta_source()`：局部讀取—合併—寫回 `_meta.json` 單一來源狀態，`is_trading_day` 只允許 `False → True`。

**main.py 串接**
- `run()` 保留 `Fetcher` 實例並傳入 `_classify_rebalance_events()`，不再用完即棄。
- `_classify_rebalance_events()` 簽名改為 `(config, storage, fetcher, target_date)`，改用 `fetcher.resolve_backfill_trading_day()` / `ensure_etf_holdings()` 取代直接呼叫 `storage.find_previous_trading_day()` 後即放棄的行為。
- 「無前一交易日資訊」情境（不論成因是投信不支援回補、還是支援但這次查無資料）統一處理：僅保留當日快照、不執行 `RebalanceClassifier.classify()`，log 訊息統一標註 `FETCH_ISSUER_PCF_NO_PREVIOUS_DAY` 供人工排查，不再區分獨立的錯誤碼／分支。

## 與 SD 文件的差異

- SD 文件建議 `_BACKFILL_LOOKBACK_DAYS_MAX` 預設 10，本次直接採用，尚未經 Roy Chiang 正式確認（SD §六第 1 項仍列為待確認，非阻塞項）。
- 野村、元大的 `SearchDate`／`date` 參數目前都只用單一日期人工驗證過（SD §六第 6、11 項），依 Roy Chiang 指示逕行採用，未另外做多日交叉驗證。

## 整合點與使用方式

- `Fetcher.ensure_etf_holdings(etf_id, prev_date)` 回傳形狀與 `SnapshotRepository.read_etf_holdings()` 一致（含 `snapshot_date`/`etf_id`/`component_stock_id`/`component_name`/`holding_shares`），呼叫端不需要區分資料是本地讀到的還是即時回補的。
- 新增/異動的 Adapter 對外介面（`fetch_holdings(etf_id, snapshot_date)`）完全不變，`Fetcher._resolve_issuer_provider()` 與 `ADAPTER_REGISTRY` 查找方式也不變。

## 待辦與已知限制

- [ ] 元大 `PCF/Daily`、野村 `SearchDate` 皆非官方文件記載的介面，日後官網若調整有失效風險；目前沒有額外的防呆偵測機制去分辨「端點失效」與「單純查無資料」。
- [ ] `_BACKFILL_LOOKBACK_DAYS_MAX`、多日期交叉驗證兩項待確認事項尚未收斂，見 SD 文件 §六。
- [ ] 深度歷史回補（如數週前）明確不支援，行為與既有設計一致。

## 測試

`pytest -q`：123 passed（含本次新增/改寫的 `test_issuer_pcf_yuanta.py`、`test_issuer_pcf_nomura.py`／`capital.py`／`fuhwa.py` 的 `SUPPORTS_BACKFILL` 斷言、`test_fetcher.py`／`test_storage.py`／`test_main.py` 的回補流程測試）。
