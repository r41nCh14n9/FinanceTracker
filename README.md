# FinanceTracker — 籌碼監控推播引擎

每日盤後自動抓取「主力分點買賣超」與「ETF PCF 成分股持股」，比對前後兩個交易日差異、依門檻篩選出新建倉／完全清倉／調倉加減碼等顯著訊號，並透過 LINE Messaging API 推播結構化簡報。全程以 GitHub Actions 排程執行，無需自建主機、無需部署資料庫。

設計依據：
- [SA-籌碼監控推播引擎-功能模組分析.md](docs/analysis/requirements/SA-籌碼監控推播引擎-功能模組分析.md)
- [SD-籌碼監控推播引擎-系統設計書.md](docs/design/architecture/SD-籌碼監控推播引擎-系統設計書.md)

---

## 專案架構

### 執行流程

```
GitHub Actions Cron（週一~五 台灣 18:00）
        │
        ▼
   main.py（進入點）
        │
        ├─ Fetcher   → 呼叫 FinMind API / 證交所 PCF API，寫入當日快照
        ├─ Analyzer  → 門檻篩選 + 前後日持股比對，寫入分析結果
        └─ Notifier  → 組簡報文字，透過 LINE Messaging API 推播
        │
        ▼
Workflow 額外步驟：git commit / push 回寫 data/ 目錄
```

### 目錄結構

```
FinanceTracker/
├─ main.py                     # 進入點：解析 CLI 參數，依序呼叫 Fetcher → Analyzer → Notifier
├─ requirements.txt
├─ .env.example                # 本機開發用環境變數範本
├─ src/
│  ├─ config.py                # ConfigLoader：讀取 config/*.json 與環境變數
│  ├─ models.py                # 共用資料結構與列舉（SnapshotStatus / RebalanceEventType / SendStatus）
│  ├─ fetcher.py                # Fetcher / FinMindClient / TwsePcfClient：抓取外部資料
│  ├─ analyzer.py               # BrokerFilter / RebalanceClassifier：門檻篩選與換倉分類
│  ├─ notifier.py               # MessageFormatter / LineClient / Notifier：簡報格式化與推播
│  └─ storage.py                # SnapshotRepository：讀寫 data/ 下所有 JSON 檔案
├─ config/                      # 版控內設定檔，由維運人員以 commit/PR 維護
│  ├─ thresholds.json           # 分點買賣超門檻、ETF 調倉幅度門檻（可依 ETF 代碼覆寫）
│  ├─ recipients.json           # LINE 收訊 User/Group 名單
│  ├─ broker_branches.json      # 分點代碼 ↔ 中文名稱對照表
│  └─ watchlist.json            # 監控的股票代碼／分點名稱／ETF 代碼清單
├─ data/                        # 執行後自動產生，不需手動建立
│  ├─ snapshots/{date}/         # 當日原始快照（_meta.json、broker_trades.json、etf_holdings/{etf_id}.json）
│  └─ reports/{date}/           # 當日分析與推播結果（rebalance_events.json、notification_log.json）
├─ tests/                       # pytest 單元測試
└─ .github/workflows/
   └─ daily-chip-monitor.yml    # 排程與手動觸發 workflow
```

> `data/` 目錄即本專案的持久化層——**刻意不部署資料庫**，改以版控內 JSON 檔案保存快照，每次執行完畢由 workflow 自動 commit 回 repo，天然具備版本歷史。詳細設計理由見 SD 文件第二章。

---

## 快速開始（本機開發）

1. 建立虛擬環境並安裝套件：

   ```bash
   python -m venv .venv
   source .venv/Scripts/activate   # Windows Git Bash；PowerShell 用 .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. 複製 `.env.example` 為 `.env`，填入實際密鑰：

   ```bash
   cp .env.example .env
   # 編輯 .env，填入 FINMIND_TOKEN / LINE_CHANNEL_ACCESS_TOKEN / LINE_CHANNEL_SECRET
   ```

3. 依需求調整 `config/` 下的設定檔（監控標的、門檻、收訊名單、分點對照表）。

4. 先以 `--dry-run` 執行，確認簡報內容正確、不會實際觸發 LINE 推播：

   ```bash
   python main.py --date 2026-07-28 --dry-run
   ```

5. 確認無誤後正式執行（會實際呼叫 LINE 推播並寫入 `data/`）：

   ```bash
   python main.py --date 2026-07-28
   ```

   不帶 `--date` 則預設抓當日日期，供排程正式使用。

6. 執行測試：

   ```bash
   pytest -q
   ```

---

## 部署建議

本專案設計為完全運行於 **GitHub Actions 免費額度**，不需另外申請主機或資料庫，部署步驟如下：

1. **設定 Repository Secrets**（Settings → Secrets and variables → Actions）：
   - `FINMIND_TOKEN`
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `LINE_CHANNEL_SECRET`

2. **確認 Actions 有寫入權限**（Settings → Actions → General → Workflow permissions），選擇 *Read and write permissions*，因為 workflow 執行完畢需要 `git push` 把當日快照回寫 repo。

3. **建立正式監控設定**：依實際需求編輯 `config/watchlist.json`（監控標的）、`config/thresholds.json`（門檻）、`config/broker_branches.json`（分點對照）、`config/recipients.json`（把範例收訊者換成實際 LINE User/Group ID，並將 `enabled` 設為 `true`），commit 進 repo。

4. **確認排程時間**：`.github/workflows/daily-chip-monitor.yml` 預設 `cron: "0 10 * * 1-5"`（週一至週五 UTC 10:00 = 台灣 18:00），如需調整交易所收盤後緩衝時間可直接修改此 cron 表達式。

5. **手動補跑**：於 GitHub Actions 頁面對 `daily-chip-monitor.yml` 手動觸發（`workflow_dispatch`），可填入 `date` 參數補跑指定日期（例如假日後補跑，或某次執行失敗後重跑）。

6. **首次執行注意事項**：第一次執行時 `data/` 目錄尚無歷史快照，`find_previous_trading_day` 會回傳 `None`，Analyzer 會略過 ETF 換倉比對（僅執行分點門檻篩選），這是預期行為，屬正常的「暖機」狀態，從第二個有效交易日起才會出現完整的換倉分析。

7. **監控執行狀況**：因未額外建立告警管道，若當次推播全數失敗或設定檔有誤，程式會以非 0 結束碼結束，直接觸發 GitHub Actions 內建的執行失敗通知信，請確保有 watch 此 repo 的 Actions 通知（帳號通知設定 或 repo Watch 設定）。

### 已知限制與待確認事項

- 證交所 PCF API 於主動式 ETF 與被動式 ETF 的端點/欄位是否一致、FinMind 分點資料集實際欄位名稱，皆需在正式串接時對照官方文件核實（`src/fetcher.py` 目前為最佳猜測的欄位對應）。
- 完整待確認事項清單見 [SD 文件第六章](docs/design/architecture/SD-籌碼監控推播引擎-系統設計書.md#六待確認事項)。
