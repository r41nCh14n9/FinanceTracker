# Implementation Report: 復華投信 PCF Adapter v1

## 實際做了什麼

- `src/issuer_pcf/fuhwa.py`：新增 `FuhwaPcfAdapter`
  - `_resolve_fund_id()`：`GET /api/fundList` 動態查出 `fundID`，不維護靜態對照表
  - `_fetch_asset_detail()`：`GET /api/assets?fundID=...&qDate=yyyy/MM/dd` 查持股，回應 `result[0].detail[]` 篩 `ftype=="股票"` 即為成分股清單
- `src/issuer_pcf/registry.py`：註冊 `FuhwaPcfAdapter`
- `config/issuer_registry.json`：`fuhwa.isEnabled` 改為 `true`，`pcf_url_template` 更新，移除靜態 `issuer_internal_codes`
- `config/watchlist.json`：`etfs` 加入 `00991A`
- `tests/test_issuer_pcf_fuhwa.py`（新檔案）：新增 5 個測試
- `docs/design/architecture/SD-ETF換倉資料來源方案評估-投信官網爬蟲-系統設計書.md`：更正上一輪（第十三輪）誤判「復華官網已改版失效」的結論，記錄第十四輪查證與實作

## 重要澄清：上一輪的「復華已阻塞」結論是誤判，本輪已更正

上一輪（第十三輪）測試 `GET /api/assets?fundID=ETF23&qDate=2026-08-14`（連字號日期格式）拿到官網首頁 HTML，因此判定「網站已改版為 Vue.js SPA、原 API 已失效」。本輪比對使用者提供的新候選端點時，改用 `qDate=2026/08/20`（斜線格式）重新測試同一個端點，**端點正常回傳完整 JSON**。真正原因是站方對 `qDate` 參數格式要求嚴格，格式不符時不會回錯誤碼，只會默默回傳首頁 HTML——這剛好符合「靜默失效」的樣子，容易讓人誤判成整個網站/API 已經改版報廢，但其實只是查詢參數格式錯了。

## 與計畫的差異
- 使用者這次提供的兩個候選端點中，只有 `fundList`（清單）真的是我們要的；`ETFPcf`（使用者原本以為的持股端點）實測後確認是「實物申購買回籃」專用，對 `00929`／`00991A` 這類現金基礎申贖的主動式 ETF 恆為空——最終持股資料來源改用比對舊記錄後重新驗證通過的 `api/assets`，而非使用者提供的兩個端點之一
- 原計畫（第十三輪）判定復華完全阻塞，本輪推翻該結論並直接實作完成，非漸進式的「先解除阻塞、再排入開發」

## 遵循的慣例
- 沿用既有 `IssuerPcfProvider` 介面與回傳格式，跟其餘四個已實作 Adapter 一致
- 查代碼、查明細拆成兩個 private method
- 回應中「摘要區塊」（`result[0].result`，基金層級的資產類別彙總）與「明細區塊」（`result[0].detail`，逐檔股票持股）容易混淆，比照群益 `data.pcf` vs `data.stocks` 的教訓，明確只讀 `detail` 並篩 `ftype=="股票"`
- 找不到對應 ticker 時的錯誤訊息比照既有 `FETCH_ISSUER_PCF_PARSE_ERROR` 代碼慣例

## 整合點與使用方式
- `Fetcher._resolve_issuer_provider()` 依 `config/issuer_registry.json` 自動查到 `FuhwaPcfAdapter`，呼叫端不需額外改動
- `issuer_registry.json` 的 `fuhwa.etfs` 同時登記 `00991A`／`00929`，本次僅實際加入 `00991A` 到 watchlist 測試

## 測試結果
- 全量單元測試：**107 個測試全數通過**（本輪新增 5 個 Fuhwa 測試）
- 真實 API 驗證：`fundList` 動態查得 `00991A→fundID=ETF23`；`assets` 端點用 8/19、8/20 兩個交易日各取回 **50 檔真實持股**，個股股數確實隨日期變動（非快取假資料）
- `main.py --date 2026-08-20 --dry-run` 完整跑過排程主流程：`ISSUER_PCF` 狀態 `OK`，`00991A.json` 正確寫入 50 筆，換倉簡報第 4 則訊息正常產出（首次納入監控，全數顯示「新建倉」，屬預期行為）

## 待辦與已知限制
- [ ] `qDate` 參數格式為此站特有的嚴格限制（僅接受 `yyyy/MM/dd`），已在程式與註解中明確記錄，但若日後站方又調整參數規則，同樣可能出現「不報錯、默默回錯誤內容」的靜默失效模式，需留意
- [ ] `ETFPcf`（實物申購買回籃）端點雖未使用，但已確認對至少一檔被動式 ETF（`006207`）可行，若日後有被動型復華 ETF 監控需求，可評估是否改用該端點取得更精確的申贖籃資訊
- 本次僅開通 `00991A`；`00929`、Phase 3 剩餘之安聯投信仍維持未實作／未開通
