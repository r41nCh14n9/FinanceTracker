"""櫃買中心（TPEx）全市場上櫃股票每日收盤行情，免金鑰公開端點，供漲跌停掃描使用。

跟 TWSE 不同，這裡的漲跌欄位本身就是帶正負號的單一數字（如 "+0.08"／"-0.27"），
不需要另外拆解符號；但查詢日期參數要用民國年格式，需要在這裡轉換，呼叫端不用知道
這個歷史包袱。
"""
from __future__ import annotations

import logging
from datetime import date

import requests

from src.market_quote.base import MarketQuoteProvider, parse_number

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30
_QUOTES_URL = "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php"
_TAIWAN_ERA_OFFSET_YEARS = 1911  # 民國年 = 西元年 - 1911


class TpexQuoteProvider(MarketQuoteProvider):
    def fetch_daily_quotes(self, trade_date: str) -> list[dict]:
        resp = requests.get(
            _QUOTES_URL,
            params={"l": "zh-tw", "d": self._to_taiwan_era_date(trade_date), "se": "EW", "o": "json"},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()

        if str(payload.get("stat", "")).lower() != "ok":
            return []

        table = self._find_quote_table(payload.get("tables") or [])
        if table is None:
            return []
        return self._parse_rows(table)

    @staticmethod
    def _to_taiwan_era_date(trade_date: str) -> str:
        d = date.fromisoformat(trade_date)
        return f"{d.year - _TAIWAN_ERA_OFFSET_YEARS}/{d.month:02d}/{d.day:02d}"

    @staticmethod
    def _find_quote_table(tables: list[dict]) -> dict | None:
        """同時含「收盤」與「漲跌」欄位的那張表才是全市場個股每日收盤行情。"""
        for table in tables:
            fields = [f.strip() for f in (table.get("fields") or [])]
            if "收盤" in fields and "漲跌" in fields:
                return table
        return None

    def _parse_rows(self, table: dict) -> list[dict]:
        fields = [f.strip() for f in table["fields"]]
        rows = []
        for raw_row in table.get("data", []):
            parsed = self._parse_row(dict(zip(fields, raw_row)))
            if parsed is not None:
                rows.append(parsed)
        return rows

    @staticmethod
    def _parse_row(values: dict) -> dict | None:
        close_price = parse_number(values.get("收盤"))
        change = parse_number(values.get("漲跌"))
        if close_price is None or change is None:
            return None

        return {
            "stock_id": (values.get("代號") or "").strip(),
            "stock_name": (values.get("名稱") or "").strip(),
            "close_price": close_price,
            "change": change,
        }
