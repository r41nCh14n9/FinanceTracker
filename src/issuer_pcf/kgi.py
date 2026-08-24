"""凱基投信 PCF 資料來源：基金明細頁是傳統伺服器端渲染，完整持股表格已經存在原始 HTML
回應裡，不需要額外呼叫 AJAX 端點，也不需要 Headless Browser。頁面網址用的是投信內部
基金代碼（例如 009816 對應 J023），不是市場代碼；目前還沒查到能動態查詢對照的清單端點，
先用固定表維護。頁面本身沒有日期查詢參數，只能抓到當下最新一期。

「持股比重」標題正下方有一個 `(YYYY/MM/DD)` 格式的日期（`<p class="fund-asset__date">`），
跟緊接在後面的「股票」表格是同一個區塊，可以拿來驗證這批資料是不是查詢日期當天的。
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
_URL_TEMPLATE = "https://www.kgifund.com.tw/Fund/Detail?fundID={fund_id}"
_STOCK_SECTION_TITLE = "股票"
_HOLDINGS_SECTION_TITLE = "持股比重"
_HOLDINGS_DATE_PATTERN = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")

# 市場代碼 -> 凱基投信內部基金代碼，頁面網址靠這組代碼組成，不能直接帶市場代碼進去。
_FUND_ID_BY_TICKER = {
    "009816": "J023",
    "00407A": "J024",
}


class KgiPcfAdapter(IssuerPcfProvider):
    def fetch_holdings(self, etf_id: str, snapshot_date: str) -> list[dict]:
        fund_id = self._resolve_fund_id(etf_id)
        html = self._fetch_html(fund_id)
        soup = BeautifulSoup(html, "html.parser")

        holdings_date = self._find_holdings_date(soup)
        if holdings_date != snapshot_date:
            logger.warning(
                "凱基 PCF 持股比重日期（%s）與查詢日期（%s）不符，視為當日尚未更新",
                holdings_date, snapshot_date,
            )
            return []

        table = self._find_stock_table(soup)
        return self._parse_rows(table)

    @staticmethod
    def _resolve_fund_id(etf_id: str) -> str:
        fund_id = _FUND_ID_BY_TICKER.get(etf_id)
        if fund_id is None:
            raise RuntimeError(
                f"凱基投信查無 '{etf_id}' 對應的內部基金代碼（FETCH_ISSUER_PCF_PARSE_ERROR），"
                "請確認代碼是否確實為凱基投信旗下 ETF，或尚未登記於內部代碼對照表"
            )
        return fund_id

    def _fetch_html(self, fund_id: str) -> str:
        resp = requests.get(
            _URL_TEMPLATE.format(fund_id=fund_id),
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.text

    @staticmethod
    def _find_holdings_date(soup: BeautifulSoup) -> str | None:
        title = soup.find("div", class_="fund-asset__title", string=_HOLDINGS_SECTION_TITLE)
        if title is None:
            return None
        date_tag = title.find_next("p", class_="fund-asset__date")
        if date_tag is None:
            return None
        match = _HOLDINGS_DATE_PATTERN.search(date_tag.get_text())
        if match is None:
            return None
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    @staticmethod
    def _find_stock_table(soup: BeautifulSoup):
        for heading in soup.find_all("h4"):
            if heading.get_text(strip=True) == _STOCK_SECTION_TITLE:
                table = heading.find_next("table")
                if table is not None:
                    return table
                break
        raise RuntimeError(
            "凱基投信 PCF 頁面找不到「股票」區塊的表格（FETCH_ISSUER_PCF_PARSE_ERROR），"
            "網站可能已改版"
        )

    @staticmethod
    def _parse_rows(table) -> list[dict]:
        records = []
        seen_stock_ids = set()
        for row in table.find_all("tr", attrs={"name": "content"}):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            stock_id = cells[0].get_text(strip=True)
            if not stock_id or stock_id in seen_stock_ids:
                # 頁面 class 命名（fund-asset__asset--desktop-none）暗示可能存在對應的
                # 響應式重複區塊，用股票代碼去重避免同一檔股票被重複計入。
                continue
            try:
                shares = int(cells[2].get_text(strip=True).replace(",", ""))
            except ValueError:
                continue
            seen_stock_ids.add(stock_id)
            records.append({
                "component_stock_id": stock_id,
                "component_name": cells[1].get_text(strip=True),
                "holding_shares": shares,
            })
        return records
