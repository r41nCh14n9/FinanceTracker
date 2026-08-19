"""群益投信 PCF 資料來源：官方 JSON API，ticker 對應的內部 fundNo 一樣不用自己維護對照表，
用市場代碼查一次清單 API 就能動態查出來。持股明細 API 的回應裡有好幾個區塊（現金、期貨、
債券...），成分股清單在 stocks 這個區塊，不是看起來像基金總覽的 pcf 區塊，抓錯區塊會拿到
一堆淨值/受益權單位數之類的欄位，不是實際持股。
"""
from __future__ import annotations

import logging

import requests

from src.issuer_pcf.base import IssuerPcfProvider

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30
_USER_AGENT = "FinanceTracker-ChipMonitor/1.0"
_LIST_API_URL = "https://www.capitalfund.com.tw/CFWeb/api/etf/list"
_DETAIL_API_URL = "https://www.capitalfund.com.tw/CFWeb/api/etf/buyback"


class CapitalPcfAdapter(IssuerPcfProvider):
    def fetch_holdings(self, etf_id: str, snapshot_date: str) -> list[dict]:
        fund_id = self._resolve_fund_id(etf_id)
        data = self._fetch_buyback(fund_id, snapshot_date)
        if data is None:
            return []

        pcf_date = data.get("pcf", {}).get("date1", "")
        if pcf_date != snapshot_date:
            logger.warning(
                "群益 PCF 資料日期（%s）與查詢日期（%s）不符，視為當日尚未更新",
                pcf_date, snapshot_date,
            )
            return []

        return [self._to_holding(row) for row in data.get("stocks", []) or []]

    def _resolve_fund_id(self, etf_id: str) -> str:
        resp = requests.post(
            _LIST_API_URL,
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()

        funds = (payload.get("data") or {}).get("funds", []) or []
        for row in funds:
            if row.get("stockNo") == etf_id:
                return row["fundNo"]
        raise RuntimeError(
            f"群益投信查無 '{etf_id}' 對應的內部代碼（FETCH_ISSUER_PCF_PARSE_ERROR），"
            "請確認代碼是否確實為群益投信旗下 ETF"
        )

    def _fetch_buyback(self, fund_id: str, snapshot_date: str) -> dict | None:
        resp = requests.post(
            _DETAIL_API_URL,
            json={"fundId": fund_id, "date": snapshot_date},
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()

        if payload.get("code") != 200 or payload.get("data") is None:
            return None
        return payload["data"]

    @staticmethod
    def _to_holding(row: dict) -> dict:
        return {
            "component_stock_id": str(row["stocNo"]),
            "component_name": row["stocName"],
            "holding_shares": int(row["share"]),
        }
