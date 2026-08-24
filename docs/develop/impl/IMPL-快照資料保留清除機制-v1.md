# Implementation Report: 快照資料保留清除機制 v1

## 實際做了什麼

- `src/models.py`：新增 `PurgeResult` dataclass（`cutoff_date`／`deleted`／`skipped_invalid_format`／`failed`），單次清除執行結果的回傳型別，不落地存檔
- `src/storage.py`：新增 `SnapshotRepository.purge_expired(retention_days, as_of_date, dry_run=False)`，掃描 `data/snapshots/`／`data/reports/` 第一層子目錄，只對完全符合 `YYYY-MM-DD`（且能被 `date.fromisoformat()` 解析）的目錄名稱做保留範圍判斷，`data/reference/` 完全不掃描；`dry_run=True` 時只記錄「會清除」不真的刪
- `src/config.py`：新增 `ConfigLoader.get_snapshot_retention_days()`，讀 `thresholds.json.default.snapshot_retention_days`，未設定時預設 365
- `config/thresholds.json`：`default` 區塊新增 `snapshot_retention_days: 365`
- `main.py`：
  - `parse_args()` 新增 `--purge` 旗標
  - 新增 `run_purge(dry_run)`：讀設定、呼叫 `purge_expired()`、記錄逐筆與彙總 log、依是否有刪除失敗回傳成功/失敗
  - `main()` 優先檢查 `args.purge`，成立就直接跑 `run_purge()` 並回傳，完全不進入既有 `_parse_target_date()`／`run()` 流程（`--date` 被忽略）
- `.github/workflows/daily-chip-monitor.yml`：「Run chip monitor」步驟在既有 `python main.py --date "$TARGET_DATE"` 之後追加一行 `python main.py --purge`
- `scripts/run.sh`：新增 `purge` 模式，沿用既有 `check_env_vars` 前置檢查（依使用者確認：即使清除本身不需要 FinMind/LINE 金鑰，仍維持跟其他模式一致的前置檢查行為）
- `tests/test_storage.py`：新增 8 個 `purge_expired()` 測試（正常清除／截止日邊界保留／非法格式略過／dry-run 不刪除／不動 reference／單一目錄失敗容錯）
- `tests/test_config.py`：新增 2 個 `get_snapshot_retention_days()` 測試（預設值／讀取設定值）
- `tests/test_main.py`：新增 7 個測試（`--purge` 分流、忽略 `--date`、`--dry-run` 透傳、`run_purge()` 讀取保留天數並以今日為基準呼叫、刪除失敗回傳 False、設定檔錯誤回傳 False）

## 與計畫的差異

無重大差異。SD 文件已明確記錄與 SA 原始設計的差異（清除改為與分析完全脫鉤的獨立指令，而非 `run()` 內部自動觸發的最後一步），本次依 SD 文件定案直接實作，過程中沒有再產生新的偏離。

## 遵循的慣例

- `SnapshotRepository` 沿用既有「純 I/O、不含 logger」的分工，`purge_expired()` 只回傳結構化結果，記錄 log 的責任留給呼叫端（`main.py`），跟 `Fetcher`（自己有 logger）與 `storage.py`（沒有）的既有分工一致
- 保留範圍判斷沿用 `find_previous_trading_day()` 既有「`YYYY-MM-DD` 字典序等於時間序」的字串比較慣例，不需要額外的日期物件排序
- 單一目錄刪除失敗不中斷其餘目錄處理，沿用全專案一貫的「單一項目失敗不拖累整體」設計原則；但 `run_purge()` 整體執行完後若有任何失敗仍回傳 `False`（跟 `Fetcher` 內部「單一資料源失敗當正常」不同）——因為清除是獨立指令，失敗本身值得被看見並反映在 exit code 上，不是每日排程裡「正常會發生」的雜訊
- `--dry-run` 語意延續既有「只預覽、不產生真實副作用」的定位

## 整合點與使用方式

- 排程：`.github/workflows/daily-chip-monitor.yml` 已自動在每次執行時依序跑分析與清除，不需要額外設定
- 本機：`scripts/run.sh purge`（或加 `--dry-run` 先預覽）
- 直接呼叫：`python main.py --purge [--dry-run]`
- 保留天數：改 `config/thresholds.json.default.snapshot_retention_days` 即可調整，不需要改程式碼

## 測試結果

- 新增與既有測試：`pytest` 全數 166 個測試通過
- 實際打向真實環境驗證：
  - `python main.py --purge --dry-run`（對正式 `data/` 目錄）：截止日正確算出 `2025-08-24`，因目前所有資料都在一年保留範圍內，清除/略過/失敗皆為 0，符合預期（同時確認先前查證到的殘留垃圾目錄 `data/snapshots/2026/` 已不存在，略過數為 0 而非之前預期的 1）
  - 另建一份隔離的模擬工作目錄（含超出保留期限的快照/報告、範圍內的快照、格式不符的殘留目錄、`reference/` 快取），直接呼叫 `SnapshotRepository.purge_expired()`：超出範圍的兩個目錄正確被刪除、範圍內的目錄保留、格式不符目錄原封不動、`reference/` 完全未被觸碰，四項結果皆與設計相符

## 待辦與已知限制

- [ ] `scripts/run.sh purge` 目前沿用 `check_env_vars`，若之後想讓清除功能在沒有設定 LINE/FinMind 金鑰的環境也能獨立使用（例如全新環境只想先跑清除），需要另外調整，本次依使用者指示維持現狀
- [ ] 本次清除只影響工作目錄，不會縮減 `.git` 儲存庫實際大小（SD 文件已明確記錄此限制），若未來需要真正從版控歷史移除資料，屬另一個獨立、具破壞性的任務，不在本次範圍
- 已知的既有殘留垃圾目錄 `data/snapshots/2026/` 使用者已手動清除，本次程式邏輯本身仍會安全略過任何未來再出現的類似格式不符目錄
