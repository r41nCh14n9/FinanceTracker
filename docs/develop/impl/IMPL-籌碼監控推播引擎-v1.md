# Implementation Report: 籌碼監控推播引擎 v1

## 實際做了什麼

- `src/models.py`：`SnapshotStatus`／`RebalanceEventType`／`SendStatus` 三個 Enum，以及 `DailySnapshotMeta`／`BrokerTradeRecord`／`EtfHoldingRecord`／`RebalanceEvent`／`NotificationLogEntry` 五個 dataclass，對應 SD 文件資料模型章節的五個檔案類型。
- `src/config.py`：`ConfigLoader` 讀取 `config/*.json` 四個設定檔與環境變數，啟動階段即驗證必要欄位是否齊全，缺漏直接拋出 `ConfigError` 中止執行（不靜默帶預設值）。
- `src/storage.py`：`SnapshotRepository` 封裝所有 `data/` 目錄下 JSON 檔案的讀寫路徑，並實作「回溯掃描快照目錄找最近一筆有效交易日」的前一交易日查找邏輯。
- `src/fetcher.py`：`Fetcher` 協調 `FinMindClient`／`TwsePcfClient`，任一資料源失敗（逾時、格式錯誤、無資料）都只記錄狀態，不中斷整體流程。
- `src/analyzer.py`：`BrokerFilter`（門檻篩選）與 `RebalanceClassifier`（新建倉／完全清倉／調倉加減碼三分類，支援依 ETF 代碼覆寫門檻）。
- `src/notifier.py`：`MessageFormatter`（簡報格式化，貼合 SA 文件簡報草案格式）、`LineClient`（LINE Push 薄封裝）、`Notifier`（重試 3 次、指數退避 5s/15s/30s，逐一收訊者記錄推播結果）。
- `main.py`：CLI 進入點，支援 `--date` 補跑與 `--dry-run` 兩個參數；未預期例外於此層攔截並以非 0 結束碼結束。
- `config/*.json`：四個設定檔範例（含一個停用中的範例收訊者，避免誤發到假 ID）。
- `.github/workflows/daily-chip-monitor.yml`：`schedule` + `workflow_dispatch` 雙觸發，執行完畢後自動 `git commit`／`push` 回寫 `data/`。
- `tests/`：4 支測試檔、24 個測試案例，涵蓋門檻篩選、三種換倉分類（含門檻覆寫、無變動略過）、快照讀寫 roundtrip、前一交易日查找、設定檔驗證、簡報格式化、推播重試（成功/放棄兩種情境）。
- `README.md`：補上專案架構（執行流程圖＋目錄結構說明）、快速開始、部署建議（Secrets 設定、Actions 寫入權限、首次執行暖機說明、監控執行狀況）、已知限制。

## 與計畫的差異

- 無重大差異。實作過程中額外發現 `_fetch_etf_holdings` 若同時追蹤 `any_ok`／`any_data` 兩個旗標會是死邏輯（兩者恆相等），簡化為單一 `fetched_any` 旗標，此為程式碼層級簡化，不影響對外行為。

## 遵循的慣例

- 全部模組依賴抽象注入（`Fetcher`／`Notifier` 建構子皆可傳入假的 Client 物件），未讓 `main.py` 或 `analyzer.py` 接觸 `requests` 這類第三方 SDK 型別。
- 長流程已拆為 private method（例如 `Fetcher._fetch_broker_trades`／`_fetch_etf_holdings`、`RebalanceClassifier._classify_one`、`Notifier._push_with_retry`／`_log_result`）。
- 註解口語化、只講功能，不引用文件章節編號或決策過程（決策脈絡都記錄在本文件與 PLAN 文件）。

## 驗證方式

- `pytest -q`：24 個測試全數通過。
- 以 `FINMIND_TOKEN=dummy`／`LINE_CHANNEL_ACCESS_TOKEN=dummy` 手動跑過一次 `python main.py --date 2026-07-24 --dry-run`：外部 API 呼叫失敗時能正確寫入 `ERROR` 狀態、不中斷流程、正常印出簡報骨架、結束碼為 0；驗證完後已清除該次測試產生的 `data/` 目錄，避免混入正式資料。

## 整合點與使用方式

- 其他人要重跑某一天：`python main.py --date YYYY-MM-DD`；只想看簡報內容不推播：加 `--dry-run`。
- 新增監控標的／調整門檻／增減收訊者：直接編輯 `config/*.json` 並 commit，不需改程式碼。
- 要抽換資料源或推播管道：`Fetcher`／`Notifier` 建構子都保留了注入點（`finmind_client`／`twse_client`／`line_client`），符合 SA 文件「擴充性策略」不需重構核心邏輯的要求。

## 待辦與已知限制

- [ ] `src/fetcher.py` 內 FinMind／證交所 PCF 的 dataset 名稱與欄位對應尚未經實際 API 呼叫驗證（因無可用測試 Token／尚未確認 PCF 端點差異），為目前最佳猜測，正式串接時需核對官方文件並視需要調整 `_to_broker_trade_record`／`_to_etf_holding_record`。
- [ ] `config/broker_branches.json`、`config/watchlist.json` 內容為範例資料，需維運人員填入實際監控名單。
- [ ] `config/recipients.json` 的範例收訊者 `enabled: false`，正式上線前需替換為真實 LINE ID 並啟用。
- [ ] 尚未涵蓋「多個 ETF 同時大量換倉、簡報內容超過 LINE 單則訊息長度上限」的情境（SD 文件驗收標準要求「單則訊息可完整顯示」，目前無長度截斷保護）。
