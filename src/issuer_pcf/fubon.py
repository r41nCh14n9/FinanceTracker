"""富邦投信 PCF 頁面的資料來源：靜態伺服器渲染 HTML，成分股在 Assets.aspx（不是原本
以為的 Pcf.aspx，那支頁面顯示的其實是現金申購買回概況）。頁面上依序有期貨、股票、
附買回債券三張長得一模一樣的表格，要抓的是「股票」標題後面那一張，抓錯就會混進期貨
或債券部位。
"""
from __future__ import annotations

import logging

import requests
from bs4 import BeautifulSoup

from src.issuer_pcf.base import IssuerPcfProvider

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30
_USER_AGENT = "FinanceTracker-ChipMonitor/1.0"
_STOCK_SECTION_TITLE = "股票"
_SUMMARY_ROW_LABEL = "股票合計"


class FubonPcfAdapter(IssuerPcfProvider):
    _URL_TEMPLATE = "https://websys.fsit.com.tw/FubonETF/Trade/Assets.aspx?stkId={etf_id}&lan=TW"

    def fetch_holdings(self, etf_id: str, snapshot_date: str) -> list[dict]:
        # 這個頁面沒有像元大那樣可信賴的交易日期欄位可以比對，
        # 目前先直接採用站方回傳的最新一筆資料，不做日期防呆。
        html = self._fetch_html(etf_id)
        soup = BeautifulSoup(html, "html.parser")
        table = self._find_stock_table(soup)
        return self._parse_rows(table)

    def _fetch_html(self, etf_id: str) -> str:
        resp = requests.get(
            self._URL_TEMPLATE.format(etf_id=etf_id),
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
