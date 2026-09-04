"""證交所（TWSE）全市場上市股票每日收盤行情，免金鑰公開端點，供漲跌停掃描使用。

MI_INDEX 一次回傳好幾張表（大盤統計、特別股指數...），要先從中找出真正的個股收盤行情表；
該表的漲跌方向另外包在一段 HTML 片段裡（如 `<p style= color:red>+</p>`），跟漲跌幅度
（漲跌價差）是分開兩個欄位，需要合併還原成一個帶正負號的漲跌金額。
"""
from __future__ import annotations

import logging

import requests

from src.market_quote.base import MarketQuoteProvider, extract_html_text, parse_number

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30
_MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"


class TwseQuoteProvider(MarketQuoteProvider):
    def fetch_daily_quotes(self, trade_date: str) -> list[dict]:
        resp = requests.get(
            _MI_INDEX_URL,
            params={"date": trade_date.replace("-", ""), "type": "ALLBUT0999", "response": "json"},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()

        if payload.get("stat") != "OK":
            return []

        table = self._find_quote_table(payload.get("tables") or [])
        if table is None:
            return []
        return self._parse_rows(table)

    @staticmethod
    def _find_quote_table(tables: list[dict]) -> dict | None:
        """同時含「收盤價」與「漲跌價差」欄位的那張表才是全市場個股每日收盤行情，
        其餘表格（指數、特別股、統計摘要）欄位結構完全不同，不會誤判。
        """
        for table in tables:
            fields = [f.strip() for f in (table.get("fields") or [])]
            if "收盤價" in fields and "漲跌價差" in fields:
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
        close_price = parse_number(values.get("收盤價"))
        magnitude = parse_number(values.get("漲跌價差"))
        if close_price is None or magnitude is None:
            return None

        sign = extract_html_text(values.get("漲跌(+/-)"))
        change = -magnitude if "-" in sign else magnitude

        return {
            "stock_id": (values.get("證券代號") or "").strip(),
            "stock_name": (values.get("證券名稱") or "").strip(),
            "close_price": close_price,
            "change": change,
        }
