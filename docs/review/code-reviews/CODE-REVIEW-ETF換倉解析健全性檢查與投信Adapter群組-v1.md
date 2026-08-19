# Code Review: ETF 換倉解析健全性檢查與投信 Adapter 群組（國泰/群益/統一） v1

## Summary
- **審查範圍**：`src/fetcher.py`（健全性檢查）、`main.py`（換倉比對修正）、`src/config.py`（新增門檻）、`src/issuer_pcf/cathay.py`／`capital.py`／`uni.py`（三個新 Adapter）、`src/issuer_pcf/registry.py`、`config/issuer_registry.json`／`watchlist.json`／`thresholds.json`、`requirements.txt`
- **審查日期**：2026-08-17
- **本專案無 `docs/reference/guidelines/`／`docs/reference/templates/`／`docs/reference/examples/` 可載入**，本次以既有程式碼慣例（同套件內其他 Adapter、`ConfigLoader`／`Fetcher` 既有寫法）作為一致性基準
- **結論**：發現 1 個 Critical、已當場修正；其餘為已確認的設計取捨（記錄於下方，非缺陷）

## Strengths ✅
- 三個新 Adapter（`cathay.py`／`capital.py`／`uni.py`）皆嚴格遵循既有 `IssuerPcfProvider` 介面與回傳格式（`component_stock_id`／`component_name`／`holding_shares`），上層 `Fetcher`／`Analyzer` 零異動即可相容
- 每個 Adapter 都把「查代碼」「查明細」拆成獨立 private method，主流程（`fetch_holdings`）讀起來像三步驟流程說明，符合方法拆小原則
- `CapitalPcfAdapter`／`UniPcfAdapter` 皆針對「不要抓錯區塊」（`pcf` vs `stocks`、期貨/現金 vs 股票）寫了對應測試，不是憑空假設資料結構
- `ConfigLoader.get_etf_holding_count_drop_pct_threshold()` 用選填欄位＋合理預設值，不強迫既有設定檔／測試 fixture 都要改，向下相容
- 每個新增行為都有對應測試鎖住，且用真實環境資料驗證過（非只靠 mock），包含刻意驗證「非交易日」「日期對不上」等邊界情況

## Issues Found ⚠️

### Critical（已當場修正）
- [x] **`Fetcher._is_holding_count_anomaly()` 讀取前一天快照時未防禦格式異常，可能讓整個 `fetch_all()` 崩潰**
  - **為什麼是問題**：這是一個健全性檢查用的輔助讀取，其他所有跟「讀取既有快照做輔助判斷」相關的既有程式碼（如 `_is_capital_stock_cache_fresh()`）都有 `except (KeyError, TypeError, ValueError)` 防禦格式異常，唯獨這支新函式沒有——前一天的 `etf_holdings/{etf_id}.json` 若因人為誤改或寫入中斷而損毀，`json.load()` 會拋出 `JSONDecodeError`（`ValueError` 子類），且這個例外沒有被任何 try/except 包住，會一路往上炸穿 `_fetch_etf_holdings()` → `fetch_all()`，讓當天**所有**資料源（三大法人、成交量、股本等）都抓不到，而不是像其他單一來源失敗那樣只影響一檔 ETF
  - **參考**：比照 `fetcher.py` 既有 `_is_capital_stock_cache_fresh()` 的防禦寫法與其註解精神（「不能讓格式問題直接把整個 fetch_all() 炸掉」）
  - **修正**：`_is_holding_count_anomaly()` 補上 `try/except ValueError`，格式異常時視為「沒有基準可比對」，不擋今天的資料，並記錄 warning；已補測試 `test_fetch_etf_holdings_corrupted_previous_snapshot_does_not_crash_whole_fetch` 鎖住此行為

### Major
（無）

### Minor / 已確認之設計取捨（不需修正，記錄供日後參考）
- **連續兩天皆解析異常時，健全性檢查會在第二天失效**：`_is_holding_count_anomaly()` 的比對基準是「前一交易日已寫入的快照」，若前一天剛好也被判定異常而未寫入，當天的比對基準會退化成「無資料」（`prev_count == 0` → 不擋），代表連續兩天資料異常時第二天不會被攔下。這是用「前一天快照」當基準的必然取捨，要完全防堵需要往前追溯到「最後一次成功寫入的日期」而非單純前一交易日，複雜度會明顯提高；目前 SD 文件 §六 #5「連續 N 天失敗告警」仍是待確認的非阻塞項，屬於同一類殘留風險，建議兩者一併評估是否要加強
- **ETF 真實歸零與「查無資料」在目前架構下無法區分**：`main.py._classify_rebalance_events()` 這次修正後，`curr_holdings` 為空一律視為「今天沒抓到資料」而跳過比對，代價是如果哪天某檔 ETF 真的變成 0 檔持股（現實中極罕見，如基金清算），系統不會產生任何事件或告警。這是刻意的取捨——比起極罕見的真實歸零情境不被告警，避免每次抓取失敗都誤報大量假清倉事件的優先度更高，但值得記錄讓未來維護者理解這不是遺漏
- **`UniPcfAdapter._extract_stock_rows()` 隱含假設「股票」區塊標題只出現一次**：若官網版面日後出現第二個同名區塊（目前未觀察到此情況），解析邏輯會誤判。風險極低（不符合目前已知的版面結構），不建議為此提前設計，留待實際發生改版時再處理

## References
- 既有程式碼慣例：`src/fetcher.py`（`_is_capital_stock_cache_fresh` 的格式異常防禦寫法）、`src/issuer_pcf/nomura.py`／`fubon.py`（Adapter 介面與私有方法拆分慣例）
- 設計依據：`docs/design/architecture/SD-ETF換倉資料來源方案評估-投信官網爬蟲-系統設計書.md`（第十輪～第十三輪）
- 需求依據：`docs/analysis/requirements/SA-ETF換倉資料來源方案評估-投信官網爬蟲可行性分析.md` §六（解析健全性檢查機制）

## Action Items
- [x] 修正 `_is_holding_count_anomaly()` 格式異常防禦（已完成，見上）
- [ ] 是否要處理「連續多天異常」的殘留風險，待與 SD 文件 §六 #5 一併決策，非本次阻塞項
- [ ] 無其他待辦

## Compliance 說明
本專案未建立正式的 `GUIDELINES-*.md`／`TEMPLATE-*`／`examples/`，故不套用制式合規分數；改以「是否符合既有程式碼慣例、是否有未受測試覆蓋的例外路徑」作為審查基準。上述 Critical 項目修正後，複審未再發現其他偏離既有慣例或未防禦的例外路徑。

## Test Coverage
- 全量測試：102 個測試全數通過（`python -m pytest`）
- 本次新增/異動涉及的每個行為皆有對應測試（詳見各測試檔案），包含本次審查新增的 1 個崩潰防禦回歸測試
