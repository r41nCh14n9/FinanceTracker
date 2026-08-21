"""復華投信 PCF 資料來源：官方 JSON API，ticker 對應的內部 fundID 一樣不用自己維護對照表，用
市場代碼查一次清單 API 就能動態查出來。持股明細 API 有個地雷：qDate 只吃 yyyy/MM/dd 斜線格式，
用連字號格式查詢站方不會報錯，只會默默回官網首頁 HTML（不是錯誤，是查詢格式不對），沒注意到
很容易誤判成端點已經失效。回應裡的 detail 陣列混了股票跟現金/其他資產，要篩 ftype 是「股票」
的那些列才是真正持股。
"""
from __future__ import annotations

import logging

import requests

from src.issuer_pcf.base import IssuerPcfProvider

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30
_USER_AGENT = "FinanceTracker-ChipMonitor/1.0"
_LIST_API_URL = "https://www.fhtrust.com.tw/api/fundList"
_ASSET_API_URL = "https://www.fhtrust.com.tw/api/assets"
_STOCK_FTYPE = "股票"


class FuhwaPcfAdapter(IssuerPcfProvider):
    SUPPORTS_BACKFILL = True

    def fetch_holdings(self, etf_id: str, snapshot_date: str) -> list[dict]:
        fund_id = self._resolve_fund_id(etf_id)
        detail, holding_date = self._fetch_asset_detail(fund_id, snapshot_date)

        if holding_date != snapshot_date:
            logger.warning(
                "復華 PCF 資料日期（%s）與查詢日期（%s）不符，視為當日尚未更新",
                holding_date, snapshot_date,
            )
            return []

        return [self._to_holding(row) for row in detail if row.get("ftype") == _STOCK_FTYPE]

    def _resolve_fund_id(self, etf_id: str) -> str:
        resp = requests.get(_LIST_API_URL, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        payload = resp.json()

        for row in payload.get("result", []) or []:
            if row.get("etf002") == etf_id:
                return row["fundID"]
        raise RuntimeError(
            f"復華投信查無 '{etf_id}' 對應的內部代碼（FETCH_ISSUER_PCF_PARSE_ERROR），"
            "請確認代碼是否確實為復華投信旗下 ETF"
        )

    def _fetch_asset_detail(self, fund_id: str, snapshot_date: str) -> tuple[list[dict], str]:
        query_date = snapshot_date.replace("-", "/")  # 官網只認斜線格式，見上方模組說明
        resp = requests.get(
            _ASSET_API_URL,
            params={"fundID": fund_id, "qDate": query_date},
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()

        rows = payload.get("result") or []
        if not rows:
            return [], ""
        fund = rows[0]
        holding_date = (fund.get("dDate") or "").replace("/", "-")
        return fund.get("detail") or [], holding_date

    @staticmethod
    def _to_holding(row: dict) -> dict:
        return {
            "component_stock_id": row["stockid"],
            "component_name": row["stockname"],
            "holding_shares": int(str(row["qshare"]).replace(",", "")),
        }
