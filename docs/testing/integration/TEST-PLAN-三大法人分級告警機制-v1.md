# 測試計畫：三大法人分級告警機制 v1

## 0. 文件資訊

| 項目 | 內容 |
| :--- | :--- |
| 測試範圍 | `FinanceTracker` 籌碼監控推播引擎——三大法人買賣超監控（個股雙門檻＋大盤門檻）、ETF 換倉監控（現況不動）、分點功能（停用態） |
| 設計依據 | [SA-三大法人分級門檻告警機制-功能模組分析.md](../../analysis/requirements/SA-三大法人分級門檻告警機制-功能模組分析.md)、[SD-三大法人買賣超關注清單通知-系統設計書.md](../../design/architecture/SD-三大法人買賣超關注清單通知-系統設計書.md)、[IMPL-三大法人分級告警機制-v1.md](../../develop/impl/IMPL-三大法人分級告警機制-v1.md)、[CODE-REVIEW-三大法人分級告警機制-v1.md](../../review/code-reviews/CODE-REVIEW-三大法人分級告警機制-v1.md) |
| 建立日期 | 2026-08-10 |
| 對象讀者 | Roy Chiang（維運/測試執行人） |
| 系統形態說明 | 本專案**無 HTTP API、無前端網頁**，是由 GitHub Actions 排程觸發的 Python 批次腳本；「部署到 server 測試」在本專案的實際對應是「部署到 GitHub Actions」，因此本計畫不產出 Postman Collection／Playwright 測試，改以「本機執行 → GitHub Actions `workflow_dispatch` 手動觸發 → 正式排程觀察」三階段驗證 |

### 測試範圍界定

| 納入範圍 | 排除範圍 |
| :--- | :--- |
| 三大法人資料抓取（個股/大盤）、個股雙門檻判斷、大盤三法人門檻判斷、簡報格式化、LINE 推播、股本快取機制、`CODE-REVIEW-三大法人分級告警機制-v1.md` 修復項目的回歸驗證 | ETF PCF 換倉監控（既有已知失效，本次未異動）、分點功能（保留但停用，不驗證其功能正確性，只驗證「確實不會被呼叫」） |

---

## 一、測試環境與階段總覽

| 階段 | 環境 | 目的 | 是否阻塞下一階段 |
| :--- | :--- | :--- | :--- |
| 階段 1：本機單元測試 | 本機 Python venv | 驗證程式邏輯正確性，不打真實 API | 是 |
| 階段 2：本機整合測試（dry-run） | 本機，真實 FinMind／證交所 API，`--dry-run` 不推播 | 驗證端到端資料流與簡報內容，不影響正式收訊者 | 是 |
| 階段 3：本機正式推播測試 | 本機，真實 API＋真實 LINE 推播，僅測試帳號 | 驗證 LINE 推播真的送得到、內容一致 | 是 |
| 階段 4：部署前檢查 | GitHub Repository 設定畫面 | 確認 Secrets／版控狀態就緒，不執行程式 | 是 |
| 階段 5：GitHub Actions 手動觸發 | GitHub Actions（`workflow_dispatch`） | 驗證雲端執行環境行為與本機一致 | 是 |
| 階段 6：正式排程觀察期 | GitHub Actions（`schedule`），3-5 個交易日 | 驗證自動排程長期穩定運作 | 否（觀察期，非阻塞） |

**環境間差異提醒**：階段 1-3 用的是你本機 `.env` 內的憑證；階段 4 起改用 **GitHub Repository Secrets**（`FINMIND_TOKEN`／`LINE_CHANNEL_ACCESS_TOKEN`／`LINE_CHANNEL_SECRET`），兩邊要各自確認憑證有效，本機測試通過不代表 Secrets 一定有正確設定。

---

## 二、階段 1：本機單元測試

### 前置條件
- 已執行 `pip install -r requirements.txt`

### 測試案例

| # | 案例 | 步驟 | 預期結果 | 對應依據 |
| :--- | :--- | :--- | :--- | :--- |
| 1-1 | 全部單元測試通過 | `python -m pytest tests/ -q` | 49 個測試全數通過，無 `FAILED`／`ERROR` | 所有模組 |
| 1-2 | Critical 修復回歸：token 不外洩 | 執行 `tests/test_fetcher.py::test_get_masks_token_in_raised_exception_message` | 通過；例外訊息中不含明文 token | CODE-REVIEW Critical |
| 1-3 | Major 修復回歸：單股失敗不拖累整批 | 執行 `test_fetch_institutional_trades_single_stock_failure_keeps_other_stocks_data`／`test_fetch_stock_trading_...`／`test_fetch_broker_trades_...` | 三者皆通過 | CODE-REVIEW Major #1 |
| 1-4 | Major 修復回歸：假日不誤判交易日 | 執行 `test_is_trading_day_ignores_capital_stock_cache_hit` 與 `test_is_trading_day_true_when_institutional_data_present` | 兩者皆通過（前者驗證 `False`、後者驗證 `True`，避免修過頭） | CODE-REVIEW Major #2 |
| 1-5 | Minor 修復回歸：壞掉的股本快取不會讓程式崩潰 | 執行 `test_capital_stock_cache_malformed_content_treated_as_stale_not_crash` | 通過；不拋例外 | CODE-REVIEW Minor #1 |

### 驗收標準
- [ ] 上述 5 項全數通過，`pytest` 結束碼為 0

---

## 三、階段 2：本機整合測試（`--dry-run`，真實 API）

### 前置條件
- 本機 `.env` 內 `FINMIND_TOKEN` 有效
- `config/watchlist.json.stocks` 至少包含一檔大型股（如 `2330`）與一檔中小型股，供分級門檻測試
- 已知一個**近期真實交易日**（例如上一個非假日）與一個**近期真實假日**（例如上一個週六/週日或國定假日）的日期字串

### 測試案例

| # | 案例 | 步驟 | 預期結果 | 對應依據 |
| :--- | :--- | :--- | :--- | :--- |
| 2-1 | 正常交易日端到端執行 | `python main.py --date {交易日} --dry-run` | 結束碼 0；輸出含「◆ 大盤三大法人動態」「◆ 三大法人買賣超（個股）」「◆ {ETF} ETF 換倉動態」三區塊；中文顯示正常無亂碼 | SA FR-1.1/1.4/1.5/1.6、FR-2.5/2.6、FR-3.3 |
| 2-2 | 假日執行不誤判交易日 | `python main.py --date {假日} --dry-run` | `data/snapshots/{假日}/_meta.json` 的 `is_trading_day` 為 `false`；`FINMIND_INSTITUTIONAL`/`FINMIND_PRICE`/`FINMIND_MARKET`/`TWSE_PCF` 為 `NO_DATA`；即使 `FINMIND_BALANCE_SHEET` 因快取命中顯示 `OK` 也不影響 `is_trading_day` | CODE-REVIEW Major #2（雲端環境重新驗證一次） |
| 2-3 | 大盤門檻檢查 | 檢視 2-1 產出的 `data/reports/{交易日}/institutional_alerts.json` | 若當日外資／投信／自營商任一淨額絕對值達 `config/thresholds.json.market_institutional` 設定門檻，對應 `scope=MARKET` 項目存在且 `trigger_type` 正確（`MARKET_FOREIGN`/`MARKET_TRUST`/`MARKET_DEALER`） | SA §3.2 大盤三法人門檻判斷規則 |
| 2-4 | 個股雙門檻檢查（大型股） | 挑一檔大型股（估算市值 ≥1000億），比對 `institutional_alerts.json` 內該股的 `trigger_type`／`estimated_amount`／`market_cap_tier` | `market_cap_tier=LARGE`；若 `abs(estimated_amount)≥30億` 或佔成交量≥15% 其一成立，應出現在告警清單並標示對應 `trigger_type` | SA §3.2 個股雙門檻判斷規則 |
| 2-5 | 個股雙門檻檢查（中小型股） | 挑一檔估算市值 <100億的股票，重複 2-4 的檢查方式 | `market_cap_tier=SMALL`，門檻2 適用 1 億（而非大型股的 30 億），驗證分級門檻確實依市值切換 | 同上 |
| 2-6 | 股本快取確實生效 | 對同一股票連續兩天執行 `--dry-run`（不同 `--date`），比對 `data/reference/capital_stock/{stock_id}.json` 的 `fetched_at` | 第二次執行後 `fetched_at` 不變（未重打 `TaiwanStockBalanceSheet` API） | SD §一（第二輪）股本快取策略 |
| 2-7 | 分點功能確實不會被呼叫 | 確認 `config/broker_branches.json.enabled` 為 `false`，執行 2-1 | `data/snapshots/{交易日}/_meta.json` 的 `sources` 不含 `FINMIND_BROKER` key；`data/snapshots/{交易日}/broker_trades.json` 不存在或未更新 | SD §一 分點功能降級設計 |
| 2-8 | 證交所 PCF 已知失效不影響整體流程 | 檢視 2-1 執行 log | 出現 `證交所 PCF 抓取失敗（{etf}）` 的 WARNING，但程式仍正常執行完畢、結束碼為 0 | SA §一 例外容錯策略（既有已知限制，非本次待修） |
| 2-9 | token 不出現在任何落地檔案中 | `grep -r "token=" data/` 及對本機 `.env` 內的實際 token 字串做全文搜尋 | 皆無結果 | CODE-REVIEW Critical（雲端環境前的最後一次確認） |
| 2-10 | 設定檔錯誤能被啟動階段攔截 | 暫時把 `config/thresholds.json` 的 `institutional_tiered` 區塊改壞（如刪除 `volume_ratio_pct`），執行 `python main.py --dry-run` | 結束碼非 0；log 顯示明確的 `設定檔錯誤，中止執行` 訊息而非未預期例外；**測試後記得改回正確設定** | SD §五 `CONFIG_INVALID` |

### 驗收標準
- [ ] 上述 10 項全數符合預期
- [ ] 執行過程 log 中沒有出現任何 Python Traceback（未捕捉例外）

---

## 四、階段 3：本機正式推播測試（真實 LINE 推播，僅測試帳號）

### 前置條件
- 建立一個**你自己的 LINE 測試帳號/群組**，取得其 User ID，於 `config/recipients.json` 新增一筆並設 `enabled: true`，**確認除此之外沒有其他 `enabled: true` 的正式收訊者**（避免測試訊息推給真正的使用者）

### 測試案例

| # | 案例 | 步驟 | 預期結果 | 對應依據 |
| :--- | :--- | :--- | :--- | :--- |
| 3-1 | 真實推播送達 | `python main.py --date {交易日}`（不加 `--dry-run`） | 測試 LINE 帳號實際收到訊息；內容與階段 2 的 `--dry-run` 輸出一致 | SA FR-3.3、SD API 契約 LINE Push |
| 3-2 | 推播紀錄正確落地 | 檢視 `data/reports/{交易日}/notification_log.json` | 該筆記錄 `send_status=SUCCESS`、`retry_count=0`（或視實際重試次數）、`recipient_id` 為測試帳號 ID | SD §二 NOTIFICATION_LOG |
| 3-3 | 推播失敗重試與放棄機制（選測，破壞性測試） | 暫時把 `LINE_CHANNEL_ACCESS_TOKEN` 改成無效值，執行一次 | log 顯示 3 次重試（間隔 5s/15s/30s）後放棄；`notification_log.json` 記錄 `FAILED`；程式結束碼非 0；**測試後記得改回正確 token** | SD §五 LINE 推播失敗處理原則 |

### 驗收標準
- [ ] 3-1、3-2 通過；3-3 為選測（會刻意觸發失敗，測試後需還原設定）
- [ ] 測試結束後，把測試用收訊者的 `enabled` 改回 `false`，正式收訊者改回 `true`（見階段 4）

---

## 五、階段 4：部署前檢查（Server／GitHub Actions 前置確認）

這一階段不執行程式，純檢查設定是否就緒，避免上到雲端才發現漏設 Secret。

| # | 檢查項 | 如何確認 | 通過標準 |
| :--- | :--- | :--- | :--- |
| 4-1 | GitHub Repository Secrets 已設定 | GitHub repo → Settings → Secrets and variables → Actions | `FINMIND_TOKEN`／`LINE_CHANNEL_ACCESS_TOKEN`／`LINE_CHANNEL_SECRET` 三者皆存在，且與本機 `.env` 內用來測試的憑證一致（或至少同樣有效） |
| 4-2 | `.env` 未被 commit 進版控 | `git log --all --oneline -- .env`；`git ls-files \| grep "^\.env$"` | 皆無結果（`.gitignore` 已排除，且歷史紀錄中也沒有） |
| 4-3 | `config/recipients.json` 收訊名單正確 | 直接檢視檔案內容 | 測試帳號 `enabled=false`；正式應收到推播的名單 `enabled=true`，且名單即為預期名單（不多不少） |
| 4-4 | `config/broker_branches.json.enabled` 維持 `false` | 直接檢視檔案內容 | 除非本次刻意要復用分點功能，否則維持 `false`（本次分點功能未接回訊息輸出，開了也不會有分點區塊） |
| 4-5 | `config/thresholds.json` 門檻數值為正式定案值 | 直接檢視檔案內容，比對 [SA-三大法人分級門檻告警機制](../../analysis/requirements/SA-三大法人分級門檻告警機制-功能模組分析.md) §一 | 個股門檻1=15%、門檻2 大型/中型/中小型=30億/5億/1億、大盤外資/投信/自營商=200億/30億/50億（若曾在測試階段 2-10 改壞過，確認已還原） |
| 4-6 | 本次程式碼異動已合併至 workflow 實際會執行的分支 | `git branch --show-current`；確認 `.github/workflows/daily-chip-monitor.yml` 觸發的分支包含本次變更 | 是 |
| 4-7 | `CODE-REVIEW-三大法人分級告警機制-v1.md` 六項發現皆已標記修正 | 檢視文件 Action Items 區塊 | 六個核取方塊皆為 `[x]` |

### 驗收標準
- [ ] 4-1 ~ 4-7 全數確認通過才可進入階段 5

---

## 六、階段 5：GitHub Actions 手動觸發測試（`workflow_dispatch`）

### 前置條件
- 階段 4 全數通過
- 程式碼已 push 到 GitHub

### 測試案例

| # | 案例 | 步驟 | 預期結果 | 對應依據 |
| :--- | :--- | :--- | :--- | :--- |
| 5-1 | 手動補跑指定日期 | GitHub repo → Actions → 籌碼監控推播引擎 → Run workflow，`date` 欄位填一個已知有效交易日 | Job 執行成功（綠勾）；三個 step（Install/Run/Commit）皆成功 | `.github/workflows/daily-chip-monitor.yml` |
| 5-2 | Actions 執行紀錄不含明文 token | 檢視該次 Run 的完整 log（尤其 `Run chip monitor` step） | 全文搜尋 token 字串（可比對 Secrets 設定值的字尾片段）無結果；即使該次執行剛好遇到 API 失敗也一樣 | CODE-REVIEW Critical（雲端環境正式驗證） |
| 5-3 | `data/` 正確自動 commit 回 repo | 檢視該次 Run 後 repo 的 commit 歷史 | 有一筆 `chore: 更新籌碼快照資料 {日期}` 的 commit，內容為新增的 `data/snapshots/{date}/`／`data/reports/{date}/` 等檔案 | workflow `Commit snapshot data` step |
| 5-4 | LINE 推播確實送達正式收訊名單 | 確認 `config/recipients.json` 已切換回正式名單（見 4-3），檢查 LINE 是否收到訊息 | 正式收訊者收到當日籌碼監控日報，內容與本機階段 2/3 測試格式一致 | SA FR-3.3 |
| 5-5 | 不帶 `date` 參數的預設行為 | Run workflow 時 `date` 留空 | 使用當日日期執行；若當日非交易日，行為應等同階段 2-2 的假日情境（`is_trading_day=false`，簡報三個區塊皆顯示「無資料/未達門檻」而非報錯） | main.py `date.today()` 邏輯 |

### 驗收標準
- [ ] 5-1 ~ 5-5 全數符合預期
- [ ] 若任一項失敗，**不進入階段 6**，回到本機重現問題、修正後重跑階段 1-5

---

## 七、階段 6：正式排程觀察期（3-5 個交易日）

排程本身（`cron: "0 10 * * 1-5"`，台灣時間週一至週五 18:00）已在階段 5 驗證過等效行為，這階段是觀察「無人值守」情況下連續多日是否穩定，非阻塞性但強烈建議執行。

| # | 觀察項 | 頻率 | 通過標準 |
| :--- | :--- | :--- | :--- |
| 6-1 | 排程準時觸發 | 每個交易日 | GitHub Actions 執行歷史顯示每個交易日皆有一筆準時（台灣時間 18:00 起 10 分鐘內）的執行紀錄 |
| 6-2 | 無非預期失敗通知信 | 每次執行後 | 沒有收到 GitHub Actions 寄出的失敗通知信（若收到，依信件內容回到本機重現問題） |
| 6-3 | 推播內容合理性人工複核 | 每個交易日 | 三大法人數字量級與當日新聞/公開資訊大致吻合（例如外資大幅賣超當天新聞通常會有對應報導），非精確比對，僅作為「資料沒有離譜錯誤」的常識檢查 |
| 6-4 | 週末/國定假日確實不誤觸發 | 觀察期間遇到的第一個假日 | 該日 `cron` 本身已排除週末不觸發；若觀察期間遇到平日國定假日（cron 仍會觸發），需確認產出簡報三區塊皆顯示「無資料/未達門檻」，不產生假告警 |

### 驗收標準
- [ ] 連續 3-5 個交易日 6-1～6-3 皆符合預期
- [ ] 若觀察期間剛好遇到平日國定假日，6-4 也需符合預期（若沒遇到則於文件註記「本次觀察期未涵蓋假日情境，已於階段 2-2／5-5 以模擬/補跑方式驗證過」）

---

## 八、風險與應變計畫

| 風險情境 | 應變方式 |
| :--- | :--- |
| 正式排程推播內容有誤（如門檻誤觸發、格式錯亂） | 立即把 `config/recipients.json` 所有收訊者 `enabled` 改為 `false` 並 commit，暫停對外推播；本機重現問題、修正後重跑階段 1-5 再重新開啟 |
| GitHub Actions 排程完全沒觸發 | 檢查 repo 的 Actions 是否被停用（Settings → Actions → General）；檢查 workflow YAML 語法是否因合併衝突等原因損毀 |
| FinMind／LINE 憑證忽然失效 | 檢查 FinMind 帳號額度／Token 有效期；LINE Developers Console 確認 Channel Access Token 未過期；更新 GitHub Repository Secrets 後以 `workflow_dispatch` 重新驗證（回到階段 5） |
| 發現新的資料異常（如 FinMind 回傳格式變更） | 記錄實際回應內容，比照本次 CODE-REVIEW 流程走一輪：先在本機用實測資料重現，修正後補單元測試，再重新走完整套階段 1-6 |

---

## 九、需求追溯表（Traceability）

| 來源需求/發現 | 對應測試案例 |
| :--- | :--- |
| SA FR-1.4/1.5/1.6（成交量、股本、大盤三法人抓取） | 2-1、2-6 |
| SA FR-2.5（個股雙門檻 OR 判斷） | 2-4、2-5 |
| SA FR-2.6（大盤三法人各自判斷） | 2-3 |
| SA FR-3.3（差異化告警訊息） | 2-1、3-1 |
| SD 分點功能降級設計 | 2-7、4-4 |
| SD 例外處理原則（CONFIG_INVALID、單一來源失敗不中斷） | 2-8、2-10 |
| CODE-REVIEW Critical（token 洩漏） | 1-2、2-9、5-2 |
| CODE-REVIEW Major #1（單股失敗隔離） | 1-3 |
| CODE-REVIEW Major #2（假日誤判交易日） | 1-4、2-2、5-5 |
| CODE-REVIEW Minor（快取防禦性處理） | 1-5 |

---

## 十、驗收標準彙總

- [ ] 階段 1：`pytest -q` 49 個測試全數通過
- [ ] 階段 2：10 項本機整合測試全數符合預期，無未捕捉例外，無 token 洩漏
- [ ] 階段 3：測試帳號真實收到推播且內容正確
- [ ] 階段 4：7 項部署前檢查全數確認
- [ ] 階段 5：5 項 GitHub Actions 手動觸發測試全數符合預期
- [ ] 階段 6：連續 3-5 個交易日觀察無異常

全部完成後，本次「三大法人分級告警機制」功能視為正式上線驗證完畢。
