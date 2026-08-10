# Implementation Plan: 籌碼監控推播引擎 v1

## Overview

依 SD-籌碼監控推播引擎-系統設計書 實作 Fetcher / Analyzer / Notifier 三大模組、檔案型持久化層與 CLI 進入點，並補上 GitHub Actions 排程與 README 部署說明。全新 greenfield 專案，本次為初版實作（無既有程式碼可沿用）。

## Approach

1. `src/models.py` 先定義共用 Enum 與 dataclass，作為其他模組的共同語言，避免各模組各自定義相似結構。
2. `src/config.py`／`src/storage.py` 作為底層基礎設施先行完成（設定讀取、檔案讀寫、前一交易日查找），上層模組直接依賴這兩者的介面。
3. `src/analyzer.py` 為純運算邏輯（無 I/O），先實作最容易單元測試的部分。
4. `src/fetcher.py`／`src/notifier.py` 涉及外部 HTTP 呼叫，透過建構子注入 Client（`FinMindClient`／`TwsePcfClient`／`LineClient`），讓單元測試可以用假物件替換，不需真的打外部 API。
5. `main.py` 串接以上模組，只負責流程順序與最外層例外邊界，不含業務邏輯。
6. 補齊 `config/*.json` 範例設定、`.env.example`、`requirements.txt`、`.gitignore`、GitHub Actions workflow。
7. 撰寫單元測試涵蓋 analyzer（門檻篩選、換倉分類三種事件＋略過情境）、storage（讀寫 roundtrip、前一交易日查找）、config（合法/不合法設定檔驗證）、notifier（簡報格式化、重試邏輯）。
8. 更新 README，補上專案架構圖與部署建議。

## File Structure

```
main.py
src/{models,config,storage,fetcher,analyzer,notifier}.py
config/{thresholds,recipients,broker_branches,watchlist}.json
tests/test_{analyzer,storage,config,notifier}.py
.github/workflows/daily-chip-monitor.yml
```

## Guidelines Followed

本專案 `docs/reference/guidelines/` 目前為空（尚未建立任何 GUIDELINES 文件），故本次實作無既有規範可遵循，改以 SD 文件的元件設計、命名與模組劃分作為唯一依據；未發生「偏離既定 GUIDELINES」的情形。

## 方案比較與決策

- **LINE Push 重試邏輯放在哪一層**：考慮過放在 `LineClient`（傳輸層自己重試）或 `Notifier`（業務層決定重試策略）。選擇放在 `Notifier`，因為重試次數與退避秒數屬於業務規則（SD §五 例外處理原則），且 `Notifier` 才知道要不要繼續嘗試下一位收訊者；`LineClient` 保持單純的一次性 HTTP 呼叫封裝，方便單元測試以 mock 物件替換。
- **`main.py` 的 `--dry-run` 實作方式**：一開始考慮讓 `Notifier` 內建 dry-run 開關，後來改為 `main.py` 直接呼叫 `MessageFormatter` 組字串印出、完全不建立 `Notifier`／`LineClient`。理由：dry-run 情境下不該連 `LINE_CHANNEL_ACCESS_TOKEN` 這個環境變數都要求存在，讓「只想看簡報長相」的使用情境不必先申請 LINE 密鑰。
- **ETF 持股是否依 etf_id 拆成多個檔案**：SD 文件已定案採拆檔（`etf_holdings/{etf_id}.json`），實作時依此設計，好處是比對時只需讀取單一 ETF 的前後日檔案，不需載入其他 ETF 資料。
- **FinMind／證交所 PCF 的實際欄位名稱**：SD 文件已將此列為待確認事項（尚未實際串接驗證）。實作時採用目前查得到的最合理欄位命名（如 `securities_trader`、`buy`/`sell`），並在程式檔頭與 README 明確註記「需在正式串接時核實」，不假裝已驗證過。

## Estimated Effort

- Planning：0.5 hr（沿用既有 SD 文件，不需重新設計）
- Implementation：核心模組 + 設定檔 + workflow
- Testing：4 支測試檔、24 個測試案例
