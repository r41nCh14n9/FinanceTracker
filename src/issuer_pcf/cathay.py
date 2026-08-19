"""國泰投信 PCF 資料來源：官網舊頁面（funds/etf/pcf.aspx）會擋爬蟲回 403，改用另一組沒被擋的
官方 API（cwapi.cathaysite.com.tw）。這組 API 還有一個好處：ticker 對應的內部 fundCode 不用
自己維護對照表，用市場代碼查一次清單 API 就能動態查出來。
"""
from __future__ import annotations

import logging

import requests

from src.issuer_pcf.base import IssuerPcfProvider

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30
_USER_AGENT = "FinanceTracker-ChipMonitor/1.0"
_LIST_API_URL = "https://cwapi.cathaysite.com.tw/api/ETF/GetETFList"
_DETAIL_API_URL = "https://cwapi.cathaysite.com.tw/api/ETF/GetETFDetailStockList"


class CathayPcfAdapter(IssuerPcfProvider):
    def fetch_holdings(self, etf_id: str, snapshot_date: str) -> list[dict]:
        # 這組 API 沒有像元大那樣可信賴的交易日期欄位可以比對，跟富邦一樣直接採用
        # 站方針對 SearchDate 回傳的資料；查無資料時 API 回空陣列，自然對應到 NO_DATA。
        fund_code = self._resolve_fund_code(etf_id)
        rows = self._fetch_detail(fund_code, snapshot_date)
        return [self._to_holding(row) for row in rows]

    def _resolve_fund_code(self, etf_id: str) -> str:
        resp = requests.get(
            _LIST_API_URL,
            params={
                "FundType": "",
                "Keyword": etf_id,
                "CurrentPage": 1,
                "PerPageCount": 20,
                "status": 1,
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()

        for row in payload.get("result", []) or []:
            if row.get("stockCode") == etf_id:
                return row["fundCode"]
        raise RuntimeError(
            f"國泰投信查無 '{etf_id}' 對應的內部代碼（FETCH_ISSUER_PCF_PARSE_ERROR），"
            "請確認代碼是否確實為國泰投信旗下 ETF"
        )

    def _fetch_detail(self, fund_code: str, snapshot_date: str) -> list[dict]:
        resp = requests.get(
            _DETAIL_API_URL,
            params={"FundCode": fund_code, "SearchDate": snapshot_date, "status": 1},
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("result", []) or []

    @staticmethod
    def _to_holding(row: dict) -> dict:
        return {
            "component_stock_id": str(row["stockCode"]),
            "component_name": row["stockName"],
            "holding_shares": int(str(row["volumn"]).replace(",", "")),
        }
