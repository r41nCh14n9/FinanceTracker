# 實作計畫：三大法人分級告警機制 v1

## 概述

依 [SD-三大法人買賣超關注清單通知-系統設計書.md](../../design/architecture/SD-三大法人買賣超關注清單通知-系統設計書.md)（含第一輪＋第二輪內容）實作：主力分點監控改版為三大法人買賣超監控，並將門檻機制擴充為「個股雙門檻（成交量佔比／市值分級金額）OR 判斷」＋「大盤三大法人金額門檻（外資/投信/自營商各自獨立）」。

## 範圍

一次到位實作第一輪＋第二輪全部內容（不分階段），因為第一輪的 `INSTITUTIONAL_TRADE_RECORD` 與第二輪的雙門檻判斷是同一條資料流，拆開實作反而要多繞一次路。

分點功能（`BrokerFilter`、`FinMindClient.fetch_broker_trades`）維持現有程式碼不動，僅新增 `ConfigLoader.is_broker_monitoring_enabled()` 供未來 `main.py` 決定是否呼叫；**本次不將分點資料接回訊息格式化**，因為新的簡報格式（大盤／個股三大法人／ETF 換倉三區塊）沒有分點區塊的設計，之後若要復用需要另外設計呈現方式。

## 涉及檔案

| 檔案 | 異動 |
| :--- | :--- |
| `src/models.py` | 新增 `DataSourceKey`／`AlertScope`／`AlertTriggerType`／`MarketCapTier` enum；新增 `InstitutionalTradeRecord`／`StockDailyTrading`／`StockCapitalSnapshot`／`MarketInstitutionalRecord`／`InstitutionalAlert` dataclass |
| `src/config.py` | 新增分級門檻／大盤門檻／分點停用旗標的讀取方法與對應 `_validate()` 檢查 |
| `src/storage.py` | 新增四種新檔案類型的讀寫方法，含獨立於日期快照之外的股本快取 |
| `src/fetcher.py` | `FinMindClient` 新增三個抓取方法；`Fetcher` 新增對應的抓取＋容錯＋落地邏輯 |
| `src/analyzer.py` | 新增 `InstitutionalTieredFilter`（個股雙門檻）、`MarketInstitutionalFilter`（大盤三法人） |
| `src/notifier.py` | `MessageFormatter` 改為輸出大盤／個股三大法人／ETF 換倉三區塊 |
| `main.py` | 串接新的抓取→分析→格式化流程 |
| `config/thresholds.json`、`config/broker_branches.json` | 依新 schema 更新 |

## 待實作時自行決定的技術細節（SD 未明確規定的部分）

- **股本快取新鮮度判斷**：SD 描述為「比對是否已是最新可取得財報季」，但精確財報截止日規則複雜。改用「`fetched_at` 未滿 90 天視為新鮮」的簡化heuristic，季更新資料用 90 天窗格已足夠避免每日重打 API，且不需要額外維護財報行事曆。
- **個股股票中文名稱**：`TaiwanStockInstitutionalInvestorsBuySell`／`TaiwanStockPrice` 兩個新資料集皆不含 `stock_name` 欄位（與舊分點資料集不同）。本次先用程式內建的靜態對照表（目前僅 `2330`／`2454` 兩檔）暫代，未知代碼則直接顯示代碼本身；長期應改用 `TaiwanStockInfo` 動態查詢，列入 IMPL 待辦事項。

## 不做的事

- 不動 `TwsePcfClient`／`RebalanceClassifier`（ETF 換倉監控維持現況，含既有已知失效問題）。
- 不新增單元測試框架設定（若專案尚無 pytest 設定，本次僅聚焦核心程式邏輯，測試另案處理）。
