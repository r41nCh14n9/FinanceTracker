"""野村投信 PCF 資料來源：跟元大／富邦不同，野村官方直接提供結構化 JSON API，不需要解析
HTML、也不需要投信內部代碼，用市場代碼（ticker）當 FundID 查詢即可。回應內容把股票／期貨／
現金保證金等資產分成好幾張表格，要抓的是 TableTitle 為「股票」那一張，其餘不是成分股持倉。

SearchDate 這個查詢參數不是官方文件寫的，是實測發現有效，帶入非當日的日期也查得到對應
資料，回應的 NavDate 會跟著變動；用 NavDate 跟查詢日期比對，兩者相符才採用，避免站方
實際上忽略了這個參數卻還是照樣回傳最新一期。
"""
from __future__ import annotations

import logging

import requests

from src.issuer_pcf.base import IssuerPcfProvider

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30
_USER_AGENT = "FinanceTracker-ChipMonitor/1.0"
_API_URL = "https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets"
_STOCK_TABLE_TITLE = "股票"


class NomuraPcfAdapter(IssuerPcfProvider):
    SUPPORTS_BACKFILL = True

    def fetch_holdings(self, etf_id: str, snapshot_date: str) -> list[dict]:
        data = self._fetch_data(etf_id, snapshot_date)

        nav_date = data.get("FundAsset", {}).get("NavDate", "")
        if nav_date.replace("/", "-") != snapshot_date:
            logger.warning(
                "野村 PCF 資料日期（%s）與查詢日期（%s）不符，視為當日尚未更新",
                nav_date, snapshot_date,
            )
            return []

        stock_table = self._find_stock_table(data.get("Table", []))
        return [self._to_holding(row) for row in stock_table.get("Rows", [])]

    def _fetch_data(self, etf_id: str, snapshot_date: str) -> dict:
        resp = requests.post(
            _API_URL,
            json={"FundID": etf_id, "SearchDate": snapshot_date},
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()

        data = (payload.get("Entries") or {}).get("Data")
        if not data:
            raise RuntimeError(
                f"野村 PCF API 查無 '{etf_id}' 對應資料（FETCH_ISSUER_PCF_PARSE_ERROR），"
                "請確認代碼是否確實為野村投信旗下 ETF"
            )
        return data

    @staticmethod
    def _find_stock_table(tables: list[dict]) -> dict:
        for table in tables:
            if table.get("TableTitle") == _STOCK_TABLE_TITLE:
                return table
        raise RuntimeError(
            "野村 PCF 回應內找不到「股票」表格（FETCH_ISSUER_PCF_PARSE_ERROR），API 可能已改版"
        )

    @staticmethod
    def _to_holding(row: list[str]) -> dict:
        stock_id, stock_name, shares, _weight_pct = row
        return {
            "component_stock_id": stock_id,
            "component_name": stock_name,
            "holding_shares": int(shares.replace(",", "")),
        }
