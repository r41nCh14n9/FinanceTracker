"""富邦投信 PCF 頁面的資料來源：靜態伺服器渲染 HTML，成分股在 Assets.aspx（不是原本
以為的 Pcf.aspx，那支頁面顯示的其實是現金申購買回概況）。頁面上依序有期貨、股票、
附買回債券三張長得一模一樣的表格，要抓的是「股票」標題後面那一張，抓錯就會混進期貨
或債券部位。

頁面其實吃一個簡單的網址查詢參數 `ddate`（不需要模擬 ASP.NET 表單回傳），查詢結果會
印出「資料日期：YYYY/MM/DD」——這才是真正代表這批持股資料實際對應哪一天的欄位；
頁面上另一個查詢日期欄位背後的隱藏欄位 hidSearchsDate 只是把查詢輸入原封不動印回來，
不管那天有沒有資料都照樣顯示查詢日期本身，不能拿來驗證，之前誤用過一次要小心。
查詢日期若不是交易日（週末、尚未發生的未來日期），站方會自動回退到最近一個有資料的
交易日，「資料日期」欄位會誠實反映這件事，不會悄悄拿舊資料充數卻讓人以為是當天的。
"""
from __future__ import annotations

import logging
import re

import requests
from bs4 import BeautifulSoup

from src.issuer_pcf.base import IssuerPcfProvider

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30
_USER_AGENT = "FinanceTracker-ChipMonitor/1.0"
_URL_TEMPLATE = "https://websys.fsit.com.tw/FubonETF/Trade/Assets.aspx"
_STOCK_SECTION_TITLE = "股票"
_SUMMARY_ROW_LABEL = "股票合計"
_DATA_DATE_PATTERN = re.compile(r"資料日期[：:]\s*(\d{4})/(\d{1,2})/(\d{1,2})")


class FubonPcfAdapter(IssuerPcfProvider):
    SUPPORTS_BACKFILL = True

    def fetch_holdings(self, etf_id: str, snapshot_date: str) -> list[dict]:
        html = self._fetch_html(etf_id, snapshot_date)

        data_date = self._find_data_date(html)
        if data_date != snapshot_date:
            logger.warning(
                "富邦 PCF 資料日期（%s）與查詢日期（%s）不符，視為當日尚未更新",
                data_date, snapshot_date,
            )
            return []

        soup = BeautifulSoup(html, "html.parser")
        table = self._find_stock_table(soup)
        return self._parse_rows(table)

    @staticmethod
    def _find_data_date(html: str) -> str | None:
        match = _DATA_DATE_PATTERN.search(html)
        if match is None:
            return None
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    def _fetch_html(self, etf_id: str, snapshot_date: str) -> str:
        resp = requests.get(
            _URL_TEMPLATE,
            params={"stkId": etf_id, "ddate": snapshot_date.replace("-", ""), "lan": "TW"},
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.text

    @staticmethod
    def _find_stock_table(soup: BeautifulSoup):
        for heading in soup.find_all("h6"):
            if heading.get_text(strip=True) == _STOCK_SECTION_TITLE:
                table = heading.find_next("table")
                if table is not None:
                    return table
                break
        raise RuntimeError(
            "富邦 PCF 頁面找不到「股票」區塊的表格（FETCH_ISSUER_PCF_PARSE_ERROR），"
            "網站可能已改版"
        )

    @staticmethod
    def _parse_rows(table) -> list[dict]:
        records = []
        for row in table.find_all("tr"):
            if "title" in (row.get("class") or []):
                continue
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            stock_id = cells[0].get_text(strip=True)
            if not stock_id or stock_id == _SUMMARY_ROW_LABEL:
                continue
            try:
                shares = int(cells[2].get_text(strip=True).replace(",", ""))
            except ValueError:
                continue
            records.append({
                "component_stock_id": stock_id,
                "component_name": cells[1].get_text(strip=True),
                "holding_shares": shares,
            })
        return records
