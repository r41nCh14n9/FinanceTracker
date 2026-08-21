"""元大投信 PCF 資料來源：官方直接提供結構化 JSON API（PCF/Daily），用市場代碼（ticker）
查詢即可，不需要另外解析投信內部代碼，也不用再抓官網頁面 HTML、丟 Node.js 子行程解析
裡面的前端狀態——這支 API 不是官方文件記載的正式介面，是實際測試找到並驗證有效的，
日後官網若調整仍有可能悄悄失效或改版，需留意。

這支 API 的 `date` 查詢參數查的是「公告日」，不是「收盤持股日」：跟 PCF 產業慣例一樣，
每個交易日收盤後結算，隔一個交易日開盤前才公告使用，所以查 `date=X` 拿到的
`PCF.trandate` 會是 X 的前一個交易日（2026-08-21 實測連續驗證多組日期，含跨週末皆
正確：查週末會拿到全空的 PCF/InKind，不是隨便給舊資料）。要拿到 snapshot_date 當天
的收盤持股，得反過來查「snapshot_date 的下一個交易日」，找到公告內容剛好對上
snapshot_date 為止。

回應內容把股票／期貨／現金等資產分開放在不同區塊，成分股清單在 InKind.FundComposition，
基金層級的概況（含交易日期 trandate）則在 PCF 區塊，兩者不要搞混。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import requests
import truststore

from src.issuer_pcf.base import IssuerPcfProvider

# 元大這個網域的憑證鏈最上層（TWCA Global Root CA）過去缺少 Subject Key Identifier 欄位，
# requests 預設用的 certifi 憑證清單驗證這條鏈會直接失敗；改用作業系統原生的信任庫
# （跟瀏覽器／curl 走同一套驗證邏輯）就能正常通過，所以在這裡切換掉預設驗證方式。
truststore.inject_into_ssl()

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30
_USER_AGENT = "FinanceTracker-ChipMonitor/1.0"
_API_URL = "https://etfapi.yuantaetfs.com/ectranslation/api/bridge"
_ANNOUNCEMENT_LOOKAHEAD_DAYS_MAX = 10  # 從 snapshot_date 往後找公告日的日曆天數上限，涵蓋連假


class YuantaPcfAdapter(IssuerPcfProvider):
    SUPPORTS_BACKFILL = True

    def fetch_holdings(self, etf_id: str, snapshot_date: str) -> list[dict]:
        payload = self._find_announcement_for(etf_id, snapshot_date)
        if payload is None:
            logger.warning(
                "元大在 %d 天內找不到反映 %s 收盤持股的公告檔案，視為當日尚未更新",
                _ANNOUNCEMENT_LOOKAHEAD_DAYS_MAX, snapshot_date,
            )
            return []

        composition = (payload.get("InKind") or {}).get("FundComposition")
        if composition is None:
            raise RuntimeError(
                "元大 PCF/Daily API 回應內找不到 InKind.FundComposition"
                "（FETCH_ISSUER_PCF_PARSE_ERROR），API 可能已改版"
            )
        return [
            {
                "component_stock_id": str(row["stkcd"]),
                "component_name": row["name"],
                "holding_shares": int(row["qty"]),
            }
            for row in composition
        ]

    def _find_announcement_for(self, etf_id: str, snapshot_date: str) -> dict | None:
        """往 snapshot_date 後面逐日查公告日，直到找到 trandate 剛好等於 snapshot_date
        的那份為止；查到週末／尚未公告的日期時，站方會回傳整包欄位皆為 null 的合法
        JSON（不是錯誤），跳過繼續找下一天即可。
        """
        target_trandate = snapshot_date.replace("-", "")
        candidate = datetime.fromisoformat(snapshot_date).date()
        for _ in range(_ANNOUNCEMENT_LOOKAHEAD_DAYS_MAX):
            candidate += timedelta(days=1)
            payload = self._fetch_pcf_daily(etf_id, candidate.isoformat())
            pcf = payload.get("PCF")
            if pcf and pcf.get("trandate") == target_trandate:
                return payload
        return None

    def _fetch_pcf_daily(self, etf_id: str, query_date: str) -> dict:
        resp = requests.get(
            _API_URL,
            params={
                "APIType": "ETFAPI",
                "CompanyName": "YUANTAFUNDS",
                "FuncId": "PCF/Daily",
                "AppName": "ETF",
                "Platform": "ETF",
                "ticker": etf_id,
                "date": query_date.replace("-", ""),
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()

        if not isinstance(payload, dict):
            raise RuntimeError(
                f"元大 PCF/Daily API 回應格式不符預期（FETCH_ISSUER_PCF_PARSE_ERROR），"
                f"ticker='{etf_id}'，API 可能已改版"
            )
        return payload
