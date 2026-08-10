# Code Review: 三大法人分級告警機制 v1

## Summary

- **範圍**：`main.py`、`src/models.py`、`src/config.py`、`src/storage.py`、`src/fetcher.py`、`src/analyzer.py`、`src/notifier.py`、`config/thresholds.json`、`config/broker_branches.json`、`tests/`，對應 [IMPL-三大法人分級告警機制-v1.md](../../develop/impl/IMPL-三大法人分級告警機制-v1.md)
- **審查日期**：2026-08-05
- **審查基準**：`docs/reference/guidelines/` 目前仍無任何 GUIDELINES 文件；本次以 [SD-三大法人買賣超關注清單通知-系統設計書.md](../../design/architecture/SD-三大法人買賣超關注清單通知-系統設計書.md)（含第一輪＋第二輪）、對應 SA 文件之既定設計原則，以及**專案既有的 [CODE-REVIEW-籌碼監控推播引擎-v1.md](./CODE-REVIEW-籌碼監控推播引擎-v1.md)**（上一輪對原始程式碼的審查結果）作為審查依據——後者特別重要，因為它已經點出兩個問題，本次審查需要確認是否被修正或被延續/放大。
- **測試結果**：`pytest -q` 40 個測試全數通過（analyzer / storage / config / notifier）
- **標準符合度評分**：58%（無專案級 GUIDELINES 可比對，此分數反映與 SD/SA 既定設計原則的符合程度；扣分主因為下方 Critical/Major 項目，其中兩項是上一輪審查已點名、本次不僅未修正、範圍還被擴大）
- **修正狀態（2026-08-05 同日修復）**：以下全部 6 項發現皆已修正並重新驗證，詳見各項目下方「✅ 修正結果」；修正後 `pytest -q` 49 個測試全數通過（新增 `tests/test_fetcher.py`），並重新以真實 FinMind 資料跑過 `--dry-run` 確認行為不變。

## Strengths ✅

- `InstitutionalTieredFilter`／`MarketInstitutionalFilter` 拆解得很乾淨：`_evaluate_one`／`_pick_trigger_type`／`_classify_tier` 各自單一職責，讀起來像一段流程說明，符合實作準則「方法要小」的要求。
- 個股金額與市值明確標示為「估算值」（訊息內文與 `models.py` 註解都有寫），並在 `IMPL` 文件裡誠實揭露股票中文名稱是暫時用靜態表頂著——這種主動揭露已知限制的做法值得肯定。
- 端到端用真實 FinMind 資料跑過 `--dry-run`（`2026-08-04`），不是只停留在單元測試層級，過程中也真的抓到並修掉一個會讓程式在 Windows 中文主控台崩潰的編碼問題，顯示有認真做過落地驗證。
- 股本快取獨立於日期快照之外（`data/reference/capital_stock/{stock_id}.json`），避免季更新資料被每天重複寫進版控，設計理由在 `storage.py` docstring 裡交代得很清楚。
- `ConfigLoader._validate()` 對新增的 `institutional_tiered`／`market_institutional` 兩個區塊做了完整的必填檢查，延續原有「設定錯誤啟動階段就攔下來」的原則。

## Issues Found ⚠️

### Critical

- [x] **FinMind Token 持續以明文出現在例外訊息中，經 `_meta.json` 被 GitHub Actions 自動 commit 進版控——此問題上一輪審查已點名，本次不但未修正，還因為新增的共用 `_get()` helper 而擴散到全部 4 個新端點**
  - **位置**：[src/fetcher.py](../../../src/fetcher.py) `FinMindClient._get()`（token 放在 `params` 裡）；`fetch_institutional_trades`／`fetch_stock_trading`／`fetch_capital_stock`／`fetch_market_institutional` 皆透過 `_get()` 呼叫；`Fetcher._fetch_institutional_trades` 等四個對應方法的 `except Exception as exc: ... error_message=str(exc)` 把原始例外訊息原樣存進 `SourceStatus`
  - **為什麼是問題**：與 [CODE-REVIEW-籌碼監控推播引擎-v1.md](./CODE-REVIEW-籌碼監控推播引擎-v1.md) Critical 項目描述的機制完全一樣：`requests` 的 `raise_for_status()` 拋出的 `HTTPError` 訊息固定包含完整請求 URL（含 query string），而 `token` 是用 query 參數傳遞，所以任何一次 API 失敗（逾時、額度用盡、股票代碼打錯…）例外訊息裡都會出現明文 `token=...`，接著被寫進 `_meta.json` 並經 workflow 自動 `git commit && git push` 永久留在版控歷史。**差別在於**：上一輪只有 `fetch_broker_trades` 一個端點受影響，這次因為把共用邏輯抽成 `_get()`，`fetch_institutional_trades`／`fetch_stock_trading`／`fetch_capital_stock`／`fetch_market_institutional` 四個新端點全部一起中招，等於同一個已知的 Critical 弱點被放大了 4 倍暴露面。
  - **驗證方式**（實測，非推測）：
    ```
    >>> FinMindClient(token='SUPER-SECRET-TOKEN-12345').fetch_institutional_trades('2026-08-04', ['INVALID_STOCK_ID_THAT_WILL_ERROR'])
    EXCEPTION MESSAGE: 400 Client Error: Bad Request for url:
    https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id=INVALID_STOCK_ID_THAT_WILL_ERROR&start_date=2026-08-04&end_date=2026-08-04&token=SUPER-SECRET-TOKEN-12345
    ```
    Token 完整明文出現在例外訊息中，會被 `Fetcher._fetch_institutional_trades` 的 `except` 區塊原樣存進 `_meta.json`。
  - **建議修法**：既然這次已經把呼叫邏輯集中到 `_get()`，正好可以一次修好全部 5 個端點（含原有 `fetch_broker_trades`）：在 `_get()` 內用 `try/except requests.exceptions.RequestException` 包住，重新拋出前把例外訊息中的 `token=...` 片段遮罩掉（或改用 Header 傳遞，若 FinMind 支援）。**這是本次最優先要修的項目**——上一輪已經標記過，這次是在同一顆地雷上又加蓋了 4 層。
  - **✅ 修正結果**：`_get()` 改為 `try/except requests.exceptions.RequestException`，捕捉後以正規表示式 `(token=)[^&\s]+` 遮罩訊息中的 token 再以 `raise RuntimeError(...) from None` 重新拋出（`from None` 徹底切斷與原始例外的關聯，避免透過 traceback chain 間接洩漏）。一次修好全部 5 個端點。新增測試 `test_get_masks_token_in_raised_exception_message` 實測驗證：注入帶明文 token 的假例外，確認 `str(exc)` 中已無 token 原文、只剩 `token=***`。

### Major

- [x] **`fetch_institutional_trades()`／`fetch_stock_trading()` 對股票清單的迴圈內沒有 try/except，單一股票失敗會讓整批（含已成功抓到的其他股票）一起遺失——複製了上一輪審查點名過的同一個反模式**
  - **位置**：[src/fetcher.py](../../../src/fetcher.py) `FinMindClient.fetch_institutional_trades`、`FinMindClient.fetch_stock_trading`
  - **為什麼是問題**：上一輪審查的 Major 項目 #1 就是在講 `fetch_broker_trades` 這個問題，並指出 `_fetch_etf_holdings` 已經示範了正確寫法（try/except 放在迴圈內）。這次新增的兩個方法完全沒有參考那個已經存在於同一個檔案裡的正確範例，而是複製了被點名過的錯誤模式。實際後果：watchlist 內只要有一檔股票當日查詢失敗（逾時、股票已下市、API 暫時異常），該資料源當天會被整批標記為 `ERROR`，即使其他股票原本抓取成功，資料也會被直接丟棄，不會落地。
  - **驗證方式**（實測）：模擬 `['2330', '9999', '2454']` 三檔，僅讓 `9999` 拋例外，結果 `2330`（在迴圈中排在 `9999` 之前、已經成功取得資料）也被一起丟棄，整個 `fetch_institutional_trades()` 呼叫拋出例外中斷，沒有任何一檔股票的資料被保留下來。
  - **建議修法**：把 try/except 下放到迴圈內部，單一 `stock_id` 失敗只記錄該筆錯誤、`continue` 處理下一檔，比照 `_fetch_etf_holdings` 或本次新寫的 `Fetcher._fetch_capital_stock`（這個是逐股呼叫＋外層 try/except，寫法是對的）。
  - **✅ 修正結果**：`fetch_institutional_trades`／`fetch_stock_trading`／`fetch_broker_trades`（原本就有問題的那個也一併修了）三個方法的迴圈內都加上 per-stock try/except。新增 3 個測試（`test_fetch_institutional_trades_single_stock_failure_keeps_other_stocks_data` 等）用假的 `requests.get` 讓其中一檔股票拋例外，實測確認其餘股票的資料仍正確保留。

- [x] **`is_trading_day` 的判斷條件誤把「股本快取剛好還新鮮」當成「今天有交易」，會讓假日被誤判為交易日，進而污染 `find_previous_trading_day()` 的比對基準**
  - **位置**：[src/fetcher.py](../../../src/fetcher.py) `Fetcher.fetch_all`（`is_trading_day=any(s.status == SnapshotStatus.OK for s in sources.values())`）、`Fetcher._fetch_capital_stock`（快取命中時回傳 `SourceStatus(status=SnapshotStatus.OK, ...)`）
  - **為什麼是問題**：股本是季更新的靜態參考資料，跟 `snapshot_date` 當天是否為交易日完全無關；但 `_fetch_capital_stock` 只要快取仍在 90 天新鮮期內，就一律回傳 `OK`，而這個 `OK` 又被算進 `is_trading_day = any(...)` 裡。具體情境：週一（交易日）成功執行後股本快取寫入；週二是假日，`FINMIND_INSTITUTIONAL`／`FINMIND_PRICE`／`FINMIND_MARKET`／`TWSE_PCF` 全部正確回報 `NO_DATA`，但 `FINMIND_BALANCE_SHEET` 因快取命中仍是 `OK`，導致週二的 `is_trading_day` 被誤判為 `True`。這直接違反原始 SD 文件自己訂下的設計保證（「天然排除假日/颱風假：當日無 `OK` 快照即不會被選為比對基準」），使得 `SnapshotRepository.find_previous_trading_day()` 未來有機會把這個假日快照誤選為「前一交易日」基準——而假日當天 `TWSE_PCF` 是 `NO_DATA`，`etf_holdings` 會是空清單，`RebalanceClassifier` 因此會把隔一個交易日的全部既有持股誤判為「新建倉」，產生一整批假告警。
  - **驗證方式**（實測，模擬假日情境）：`FINMIND_INSTITUTIONAL`／`FINMIND_PRICE`／`FINMIND_MARKET`／`TWSE_PCF` 皆模擬為 `NO_DATA`，僅預先寫入一份新鮮的股本快取，執行 `fetch_all('2026-08-08')` 後：
    ```
    sources: {FINMIND_INSTITUTIONAL: 'NO_DATA', FINMIND_PRICE: 'NO_DATA', FINMIND_BALANCE_SHEET: 'OK', FINMIND_MARKET: 'NO_DATA', TWSE_PCF: 'NO_DATA'}
    is_trading_day: True  # 應為 False
    ```
  - **建議修法**：`is_trading_day` 的判斷只應該看真正「與當日市場活動相關」的來源（`FINMIND_INSTITUTIONAL`／`FINMIND_PRICE`／`FINMIND_MARKET`／`TWSE_PCF`，以及啟用時的 `FINMIND_BROKER`），明確排除 `FINMIND_BALANCE_SHEET`；例如改成對 `sources` 建一個子集合、或在 `DataSourceKey` 上標記哪些屬於「日期相關」來源。
  - **✅ 修正結果**：新增 `_TRADING_DAY_SOURCES` 常數（不含 `FINMIND_BALANCE_SHEET`），`is_trading_day` 改成只在這個子集合內做 `any(...)` 判斷。新增測試 `test_is_trading_day_ignores_capital_stock_cache_hit` 重現原本回報中的假日情境，確認修正後 `is_trading_day` 正確變成 `False`；另補 `test_is_trading_day_true_when_institutional_data_present` 確認正常交易日仍判定為 `True`，避免修過頭。

- [x] **`src/fetcher.py` 依然沒有對應的單元測試——上一輪審查已經點名這個缺口，本次新增了大量 Fetcher 邏輯（含以上兩個 Major 問題）卻還是沒有補**
  - **位置**：`tests/` 目錄下仍只有 `test_analyzer.py`／`test_storage.py`／`test_config.py`／`test_notifier.py`，`test_fetcher.py` 依舊不存在
  - **為什麼是問題**：上一輪審查明確建議「補上 `tests/test_fetcher.py`，至少涵蓋單一資料源失敗不中斷整體流程」，並指出這正是能在合併前攔下問題的地方。這次不僅沒有補上，還新增了本報告點名的兩個 Major 邏輯錯誤（批次失敗遺失資料、`is_trading_day` 誤判）——這兩個問題都是本次審查用手動腳本臨時驗證才抓到的，如果當初有 `test_fetcher.py`，理論上寫測試的過程就會自然碰到。`pytest -q` 40 個測試全過，但這個「全過」完全沒有涵蓋到 `Fetcher` 這個本次改動最多、也最容易出錯的模組。
  - **建議修法**：比照 `test_notifier.py` 用 `MagicMock` 取代 `LineClient` 的模式，把 `finmind_client`／`twse_client` 換成假物件，至少補：(1) 單一股票失敗不影響其他股票的資料保留、(2) `FINMIND_BALANCE_SHEET` 快取命中不應該讓 `is_trading_day` 變 `True`、(3) 分點停用旗標關閉時 `sources` 不含 `FINMIND_BROKER` key。
  - **✅ 修正結果**：新增 `tests/test_fetcher.py`，共 9 個測試，涵蓋建議的三點（含正反情境）以及股本快取格式異常不崩潰、token 遮罩。全部隨 `pytest -q` 執行，目前 49 個測試（原 40 ＋新增 9）全數通過。

### Minor

- [x] **股本快取的讀取（`_is_capital_stock_cache_fresh`／`InstitutionalTieredFilter._classify_tier`）沒有防禦性錯誤處理，快取檔案格式異常會讓整次執行直接崩潰，而非依專案一貫原則優雅降級**
  - **位置**：[src/fetcher.py](../../../src/fetcher.py) `Fetcher._is_capital_stock_cache_fresh`（`datetime.fromisoformat(cached["fetched_at"])` 未包 try/except，且此呼叫在 `_fetch_capital_stock` 迴圈內位於 try 區塊**之外**）；[src/analyzer.py](../../../src/analyzer.py) `InstitutionalTieredFilter._classify_tier`（`cached["estimated_shares"]` 直接索引）
  - **為什麼是問題**：這兩處都是「讀自己前一次寫的檔案」，正常運作下格式必然正確，所以不是天天會炸的問題；但專案從 SA 到 SD 到既有程式碼都反覆強調「格式異常也要被容錯、不能讓整個流程中斷」，這兩處是目前少數沒有落實這個原則的地方（例如檔案在寫入過程中被中斷、或維運人員手動編輯打錯格式，就會讓 `fetch_all()`／`Analyzer` 整個崩潰，而不是該檔股票的資料被略過）。
  - **建議修法**：`_is_capital_stock_cache_fresh` 的呼叫改放進既有的 try 區塊內（或自己包一層，格式錯誤時視為「不新鮮」，跟快取不存在同樣處理）；`_classify_tier` 對 `cached` 內容缺欄位時回傳 `None`（等同無快取，門檻2 判定不達標），而不是讓 `KeyError` 往上冒。
  - **✅ 修正結果**：兩處都改成 `try/except (KeyError, TypeError, ValueError)`，格式異常時分別視為「不新鮮」／「無法分級」優雅降級並記錄 WARNING，不再讓例外往上冒。新增測試 `test_capital_stock_cache_malformed_content_treated_as_stale_not_crash` 直接寫入缺欄位的假快取檔案，確認 `fetch_all()` 不會拋例外、且會正確視為過期重新呼叫 API。

- [x] **`Fetcher._fetch_capital_stock` 在「查無資料且沒有可用快取」時，狀態回報與程式碼註解宣稱的「沿用舊快取」行為不完全一致**
  - **位置**：[src/fetcher.py](../../../src/fetcher.py) `Fetcher._fetch_capital_stock`
  - **為什麼是問題**：註解寫「FinMind 股本查無資料（%s），沿用舊快取（如有）」，但程式碼在這個分支只是單純 `continue`，沒有實際去檢查/標記「是否真的還有舊快取可沿用」；如果該股票從來沒有成功取得過股本（無舊快取），這裡會靜默略過，最終該股票的來源狀態要嘛落到 `NO_DATA`（若所有股票都這樣）要嘛完全不影響整體 `any_success` 判斷，訊息上不會明確反映「這檔股票的市值分級這次會失效」。影響有限（`InstitutionalTieredFilter._classify_tier` 本來就會處理 `cached is None` 的情況，門檻2 正常降級為不判定），純粹是狀態回報不夠精確。
  - **建議修法**：非必要，可考慮把「查無資料且無舊快取」與「查無資料但有舊快取沿用」在 log 訊息上明確區分，方便事後排查。
  - **✅ 修正結果**：查無資料時改為實際檢查 `read_capital_stock_cache()` 是否存在，log 訊息依有無舊快取分別顯示「沿用舊快取」或「且無舊快取可沿用，市值分級這次會失效」；`any_success` 也依實際是否有可沿用的快取來設值，不再一律略過不計。

## References

- [CODE-REVIEW-籌碼監控推播引擎-v1.md](./CODE-REVIEW-籌碼監控推播引擎-v1.md) —— 本次 Critical 與 Major #1 皆延續自此份文件已點名的問題
- [SA-三大法人買賣超關注清單通知-功能模組分析.md](../../analysis/requirements/SA-三大法人買賣超關注清單通知-功能模組分析.md) §一 例外容錯策略
- [SD-籌碼監控推播引擎-系統設計書.md](../../design/architecture/SD-籌碼監控推播引擎-系統設計書.md) §二「前一交易日判定邏輯：天然排除假日/颱風假」
- [SD-三大法人買賣超關注清單通知-系統設計書.md](../../design/architecture/SD-三大法人買賣超關注清單通知-系統設計書.md) §五 例外處理原則

## Action Items for Developer

- [x]（Critical）在 `FinMindClient._get()` 統一處理例外訊息遮罩，一次修好全部 5 個端點的 token 洩漏問題
- [x]（Major）`fetch_institutional_trades`／`fetch_stock_trading` 的迴圈內加上單一 stock_id 的 try/except
- [x]（Major）`is_trading_day` 的判斷排除 `FINMIND_BALANCE_SHEET`，只採計與當日市場活動相關的來源
- [x]（Major）補上 `tests/test_fetcher.py`
- [x]（Minor）股本快取讀取加上防禦性錯誤處理
- [x]（Minor）`_fetch_capital_stock` 的「查無資料」狀態回報精確化
- [x] 修正後重新執行 `pytest -q`（49 個測試全數通過）並以真實資料重跑 `--dry-run` 驗證行為不變

## 修正後殘留事項（非本次阻塞項，供下一輪參考）

- `Fetcher._fetch_capital_stock` 在「查無資料但有舊快取」時回報 `OK`，即使那份舊快取內容其實已經格式異常（見 Minor 項目修正說明）；這種極端情境下狀態顯示會偏樂觀，但 `InstitutionalTieredFilter._classify_tier` 已能安全降級，不會造成功能性錯誤，僅為狀態回報精確度的邊界情況，暫不處理。
- `docs/reference/guidelines/` 依然沒有正式的 GUIDELINES 文件；本次 Critical 項目是同一顆地雷第二次被點名，建議儘早補一份 `GUIDELINES-Security.md` 把「例外訊息預設視為可能含敏感資訊」寫成硬性規則，避免下次新增資料源時第三次發生。

## Recommendations

1. Critical 項目已經是第二次被點名，建議這次修完後把「例外訊息預設視為可能含敏感資訊，記錄/持久化前一律過濾」這條規則寫進一份正式的 `GUIDELINES-Security.md`（目前 `docs/reference/guidelines/` 是空的），否則同樣的問題很可能在下一輪新增資料源時第三次發生。
2. 已實際檢查本機 `data/` 目錄（尚未 commit）：`grep` 全目錄搜尋 `token=`／真實 Token 字串／`finmindtrade.com` 網址，皆無殘留——因為先前的 dry-run 剛好每次呼叫都成功，沒有觸發到這個問題。但這只是運氣好，不代表問題不存在，Critical 項目還是要修；日後若曾經歷過呼叫失敗，記得在 commit 前用同樣方式檢查一次。
3. `test_fetcher.py` 這個缺口已經連續兩輪審查都被點名，建議這次直接當作最高優先度處理，而不是又留到下一輪。
