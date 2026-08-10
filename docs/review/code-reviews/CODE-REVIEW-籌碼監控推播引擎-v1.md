# Code Review: 籌碼監控推播引擎 v1

## Summary

- **範圍**：commit `22ad1e2`（`main.py`、`src/`、`config/*.json`、`tests/`、`.github/workflows/daily-chip-monitor.yml`），對應 [IMPL-籌碼監控推播引擎-v1.md](../../develop/impl/IMPL-籌碼監控推播引擎-v1.md)
- **審查日期**：2026-07-29
- **審查基準**：`docs/reference/guidelines/` 目前尚無任何 GUIDELINES 文件，本次以 [SD-籌碼監控推播引擎-系統設計書.md](../../design/architecture/SD-籌碼監控推播引擎-系統設計書.md)／[SA-籌碼監控推播引擎-功能模組分析.md](../../analysis/requirements/SA-籌碼監控推播引擎-功能模組分析.md) 的既定設計原則與通用安全/可靠性實務作為審查依據
- **測試結果**：`pytest -q` 24 個測試全數通過（analyzer / storage / config / notifier）
- **標準符合度評分**：72%（無專案級 GUIDELINES 可比對，此分數反映與 SD/SA 既定設計原則的符合程度；扣分主因為下方 Critical/Major 項目）

## Strengths ✅

- 三大模組（Fetcher/Analyzer/Notifier）皆透過建構子注入外部 Client（`FinMindClient`／`TwsePcfClient`／`LineClient`），低耦合、易於用假物件測試，`test_notifier.py` 已示範這個模式。
- `Analyzer` 為純運算邏輯（無 I/O），配合 `_classify_one` 等 private method 拆解，測試涵蓋新建倉/清倉/調倉/門檻覆寫/無變動略過五種分支，符合 SD §四業務邏輯落地方式。
- `ConfigLoader` 在啟動階段即驗證設定檔完整性（`_validate()`）並拋出明確的 `ConfigError`，對應 SD §五 `CONFIG_INVALID` 錯誤碼設計。
- GitHub Actions workflow 的 `permissions: contents: write` 僅開最小必要權限，未過度授權。
- `.env` 已正確加入 `.gitignore`，本機密鑰管理符合設計。
- README／PLAN／IMPL 文件完整記錄了決策脈絡（如重試邏輯放在 Notifier 而非 LineClient 的理由），符合開發準則對「決策過程不寫進程式碼註解」的要求。

## Issues Found ⚠️

### Critical

- [ ] **FinMind API Token 會透過錯誤訊息外洩到 GitHub Actions 執行記錄，並被 commit 進版控歷史**
  - **位置**：[src/fetcher.py:42-56](../../../src/fetcher.py)（`FinMindClient.fetch_broker_trades`，token 以 query string 傳遞）、[src/fetcher.py:107-109](../../../src/fetcher.py)（`error_message=str(exc)` 原樣保存例外訊息）、[.github/workflows/daily-chip-monitor.yml:44-49](../../../.github/workflows/daily-chip-monitor.yml)（`git add data/ && git commit && git push`）
  - **為什麼是問題**：`requests` 的 `resp.raise_for_status()` 在拋出 `HTTPError` 時，訊息內容固定包含完整的請求 URL（含 query string）。由於 `FinMindClient.fetch_broker_trades` 把 `token` 放在 `params` 裡（而非 Header），任何 FinMind API 呼叫失敗（例如額度用盡、token 失效、暫時性錯誤）都會讓例外訊息中出現 `...&token=<實際的 FINMIND_TOKEN 明文>`。這段訊息接著：(1) 被 `logger.warning` 寫進 GitHub Actions 執行記錄；(2) 被存進 `SourceStatus.error_message`，經 `Fetcher.fetch_all` → `SnapshotRepository.write_meta` 寫入 `data/snapshots/{date}/_meta.json`；(3) 該檔案被 workflow 自動 `git commit`／`git push` 回 repo，永久留在版控歷史中。這直接違反 SD 文件自己訂下的密鑰管理原則（§一「程式碼與版控中不得出現明文金鑰」）。
  - **驗證方式**：本次審查以 `FINMIND_TOKEN=dummy` 實際跑過 `python main.py --dry-run`，`data/snapshots/2026-07-24/_meta.json` 的 `error_message` 欄位中確實出現 `token=dummy` 字樣（該次測試產出的 `data/` 已於當時清除，未進入本次審查的 commit）。真實 Token 情境下會是明文外洩。
  - **建議修法**：改用 Header 帶 token（若 FinMind API 支援 `Authorization` 或自訂 Header，比照 `LineClient` 目前的做法——LINE 是用 `Authorization: Bearer` header，因此完全不會有此問題）；若 FinMind 僅支援 query string 傳 token，至少要在寫入 `error_message`／記錄 Log 前，對例外訊息做遮罩處理（移除或替換 `token=...` 片段）再持久化。

### Major

- [ ] **`FinMindClient.fetch_broker_trades` 沒有做到單一標的失敗不影響其他標的**
  - **位置**：[src/fetcher.py:39-56](../../../src/fetcher.py)
  - **為什麼是問題**：此方法對 `stock_ids` 逐一發送請求，但迴圈內沒有 try/except；只要其中一檔股票的請求失敗（逾時、該股票當日無資料回傳格式異常等），例外會直接中斷整個迴圈，連同已經成功抓到的其他股票資料一起遺失，最終讓 `Fetcher._fetch_broker_trades` 把當日「FINMIND」整個資料源標記為 `ERROR`——即使 watchlist 內大部分股票其實抓取成功。這與 SA 文件 §一「任何單一資料來源失敗或無資料...不得中斷整體流程；其餘模組仍應嘗試完成當日可用的分析與推播」的設計原則不一致。對照 [src/fetcher.py:118-138](../../../src/fetcher.py) 的 `_fetch_etf_holdings`，該方法已正確地把 try/except 放在每個 `etf_id` 的迴圈內，兩者處理方式不一致。
  - **建議修法**：把 try/except 下放到迴圈內部，單一 stock_id 失敗只記錄該筆錯誤並繼續下一檔，比照 `_fetch_etf_holdings` 的寫法。

- [ ] **設定錯誤的處理路徑不一致：JSON 設定檔錯誤有清楚提示，環境變數缺漏卻沒有**
  - **位置**：[main.py:31-39](../../../main.py)（`try/except ConfigError` 只包住 `ConfigLoader()` 這一行）、[src/fetcher.py:84](../../../src/fetcher.py)、[src/notifier.py:100](../../../src/notifier.py)（`FINMIND_TOKEN`／`LINE_CHANNEL_ACCESS_TOKEN` 透過 `config.get_env(..., required=True)` 延遲到 `Fetcher`／`Notifier` 建構時才檢查）
  - **為什麼是問題**：`config.get_env()` 缺少必要環境變數時同樣拋出 `ConfigError`，但這個拋出點（`Fetcher(config, storage)` 於 [main.py:39](../../../main.py)）不在 `run()` 開頭那段 `try/except ConfigError` 的保護範圍內，於是會一路冒到 `main()` 的 catch-all，被記錄成「執行時發生未預期例外」而非 SD §五 設計的 `CONFIG_INVALID`／「設定檔錯誤，中止執行」清楚訊息。實際後果都是結束碼非 0、都會觸發 Actions 失敗通知，差別在於維運人員看 log 除錯時，前者（JSON 設定檔漏欄位）一眼就知道是設定問題，後者（忘記設 Secret）卻要多花時間才能定位到根因。
  - **建議修法**：讓 `ConfigLoader` 在 `_validate()` 階段就一併檢查 `FINMIND_TOKEN`／`LINE_CHANNEL_ACCESS_TOKEN`／`LINE_CHANNEL_SECRET` 是否存在（即使當次是 dry-run 也可以只檢查 FinMind 那把），或是把 `run()` 裡 `try/except ConfigError` 的範圍擴大包住 `Fetcher`／`Notifier` 的建構。

- [ ] **`src/fetcher.py` 完全沒有對應的單元測試**
  - **位置**：`tests/` 目錄下只有 `test_analyzer.py`／`test_storage.py`／`test_config.py`／`test_notifier.py`，缺少 `test_fetcher.py`
  - **為什麼是問題**：`Fetcher` 正是負責實現「單一資料源失敗不中斷流程」這條 NFR 的模組（也是上面 Major 項目 #1 的所在地），卻是四個核心模組裡唯一沒有測試覆蓋的一個。`Fetcher` 建構子已經預留了 `finmind_client`／`twse_client` 注入點，寫法可以完全比照 `test_notifier.py` 用 `MagicMock` 取代 `LineClient` 的方式來做，技術上沒有阻礙。缺少這層測試，代表上面「單一標的失敗拖垮整批資料」的問題原本有機會在合併前被測試攔下來。
  - **建議修法**：補上 `tests/test_fetcher.py`，至少涵蓋：(1) 單一資料源全部成功、(2) 單一資料源逾時/例外時整體流程不中斷且狀態標記正確、(3) 針對 Major 項目 #1 的情境——`finmind_client` 對其中一檔股票丟例外時，其餘股票資料是否仍被保留。

### Minor

- [ ] **`config/recipients.json` 內單一收訊者缺少必要欄位（如 `id`）時，不會在啟動階段被攔截**
  - **位置**：[src/config.py:35-48](../../../src/config.py)（`_validate()` 只檢查 `recipients` 陣列本身存在，不檢查陣列內每個物件的欄位）、[src/notifier.py:108](../../../src/notifier.py)（`recipient["id"]` 直接索引，未經過 try/except 保護）
  - **為什麼是問題**：若維運人員編輯 `recipients.json` 時漏打 `"id"`，不會在 `ConfigLoader` 啟動驗證時被發現，而是在 `Notifier.notify()` 迴圈跑到該筆資料時丟出 `KeyError`，一路冒到 `main()` 的「未預期例外」分支。影響範圍有限（設定檔由維運人員自行維護，非外部輸入），但與 Major 項目 #2 是同一類「本該在啟動階段攔下的設定問題，卻被歸類成未預期例外」的狀況。
  - **建議修法**：`_validate()` 可加一段迴圈檢查每個 `recipients[]` 物件是否具備 `id`／`type`／`enabled` 欄位。

## References

- [SA-籌碼監控推播引擎-功能模組分析.md](../../analysis/requirements/SA-籌碼監控推播引擎-功能模組分析.md) §一 密鑰管理策略、例外容錯策略
- [SD-籌碼監控推播引擎-系統設計書.md](../../design/architecture/SD-籌碼監控推播引擎-系統設計書.md) §一 安全設計、§五 錯誤碼彙整

## Action Items for Developer

- [ ] （Critical）修正 FinMind Token 洩漏：改用 Header 傳遞，或在記錄/持久化前遮罩 `error_message` 中的 token
- [ ] （Major）`FinMindClient.fetch_broker_trades` 迴圈內加上單一 stock_id 的 try/except，比照 `_fetch_etf_holdings`
- [ ] （Major）讓環境變數缺漏也能走清楚的 `ConfigError`／「設定檔錯誤，中止執行」路徑
- [ ] （Major）補上 `tests/test_fetcher.py`
- [ ] （Minor）`ConfigLoader._validate()` 補上 `recipients[]` 內每筆物件的必要欄位檢查
- [ ] 修正後重新執行 `pytest -q` 並重新提交 review

## Recommendations

1. 一併排查 `TwsePcfClient` 是否有類似的敏感參數會出現在例外訊息中（目前看來 PCF 端點無需金鑰，風險較低，但養成「例外訊息預設視為可能含敏感資訊」的習慣較保險）。
2. 待 Critical 項目修正後，建議手動確認 `data/` 目錄目前的 git 歷史中沒有殘留任何一次因真實 Token 失敗而外洩的記錄；本次審查用的是假 Token，且測試產出已於當次清除未進入正式 commit，但正式上線後應建立這個檢查習慣。
3. 待補上 `test_fetcher.py` 後，可考慮把「單一標的/單一 ETF 失敗不中斷整體」這條規則整理成一個共用的測試輔助（例如一個會在第 N 次呼叫拋例外的假 Client），三個 Client 的容錯測試都能共用。
