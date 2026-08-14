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
- [x] ~~尚未涵蓋「多個 ETF 同時大量換倉、簡報內容超過 LINE 單則訊息長度上限」的情境~~：已解決，見文末「後續調整」。

## 後續調整：訊息拆分與長度保護

原設計把整份簡報組成單一字串、單一則 LINE 訊息推播。後續在監控 ETF 數量變多、且能接上真實資料後，實際量測發現：單一 ETF 50 檔成分股全部換倉（例如首次執行、無前一日快照可比對時）約 1,672 字元，5 檔 ETF 同時發生類似規模換倉會來到 7,944 字元，超過 LINE 文字訊息型別的 5,000 字元硬上限，訊息會直接送不出去。改動如下：

- `MessageFormatter.format()` 回傳型別從 `str` 改成 `list[str]`：三大法人（大盤＋個股）自成一組訊息，每檔「當天有換倉」的 ETF 各自一組，彼此獨立、不再全部塞進同一則。
- 新增 `_paginate()`：任一組內容逼近安全長度（4,500 字元，留緩衝不要卡在 LINE 5,000 上限的邊界）時自動分頁成好幾則，標題加上「（n/總頁數）」，並確保「一檔股票的標題行＋明細行」「一筆換倉事件」這種不可拆的區塊不會被攔腰切在兩則訊息之間。
- `Notifier.notify()` 依 LINE push API 一次最多帶 5 則訊息的限制，把整批訊息切成多次 `push()` 呼叫；重試/記錄仍以「一次 push 呼叫」為單位（多則訊息一起成功或一起需要重試）。
- `main.py --dry-run` 改為依序印出每則訊息並標示「訊息 i/N」。

沿用既有慣例：`LineClient`／`Notifier` 的重試與例外處理邏輯不變，只是操作對象從單一字串改成字串清單；未新增例外類別或錯誤碼機制。測試新增／調整於 `tests/test_notifier.py`（訊息確實拆分、超長區塊自動分頁、超過 5 則時分批呼叫 `push()`），全數通過。

**第二輪調整（每日訊息量上限＋非交易日不推播）：** 用 LINE 官方配額查詢 API 實測得知目前免費方案月配額 200 則。與 Roy Chiang 討論後，**暫時先假設單一收訊者**，決定：

- `src/notifier.py` 新增 `_MAX_MESSAGES_PER_DAY = 10`（依 1 位收訊者、免費方案 200 則／月、每月約 20 個交易日概算），`Notifier._cap_daily_messages()` 在超過時保留最前面 9 則（三大法人優先，其餘依 ETF 順序），第 10 則改成明確的截斷提示（「另有 N 則內容未發送，完整資料請查看 data/snapshots/ 快照」），不靜默丟棄資訊。
- `main.py`：`Fetcher.fetch_all()` 的回傳值（`DailySnapshotMeta`）現在會被接住檢查 `is_trading_day`；非交易日（國定假日剛好落在平日）時直接跳過分析與推播、不消耗配額；`--dry-run` 不受此限，仍可預覽任何日期的簡報。

**明確排除、留待日後評估（Roy Chiang 決議）：** 若未來收訊者不只一人，200 則／月的配額無論怎麼調參數都不夠用（1 人 10 則/日 × 20 交易日就已經是 200，零緩衝），屆時可能需要改用「直接傳送 txt 檔」等不同的推播形式，而非持續在文字訊息則數上打轉。**這件事排定在本專案所有目標投信機構的 Adapter 都開發完成、監控範圍確定之後再評估**，本輪不處理，避免過早設計用不到的機制。
