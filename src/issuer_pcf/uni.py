"""統一投信 PCF 資料來源：跟其他投信不同，這裡拿到的不是 JSON，而是一份真的 Excel 匯出檔，
同一張工作表混了基金概況、期貨、現金、股票好幾個區塊，要挑出「股票」那一段才是成分股清單。
先造訪一次首頁是為了拿到官網要求的工作階段 Cookie，之後才能正常打 Excel 匯出端點；資料日期
欄位是民國年格式（例：115/08/14），也要自己換算成西元年才能跟查詢日期比對。
"""
from __future__ import annotations

import io
import logging
import re

import openpyxl
import requests
from bs4 import BeautifulSoup

from src.issuer_pcf.base import IssuerPcfProvider

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30
_USER_AGENT = "FinanceTracker-ChipMonitor/1.0"
_HOME_URL = "https://www.ezmoney.com.tw/"
_LIST_URL = "https://www.ezmoney.com.tw/ETF/Fund/Index"
_ASSET_URL = "https://www.ezmoney.com.tw/ETF/Fund/AssetExcelNPOI"
_STOCK_SECTION_TITLE = "股票"
_FUND_CODE_PATTERN = re.compile(r"fundCode=([A-Za-z0-9]+)")
_ROC_DATE_PATTERN = re.compile(r"(\d+)/(\d{2})/(\d{2})")


class UniPcfAdapter(IssuerPcfProvider):
    def fetch_holdings(self, etf_id: str, snapshot_date: str) -> list[dict]:
        session = self._new_session()
        fund_code = self._resolve_fund_code(session, etf_id)
        rows, sheet_date = self._fetch_asset_sheet(session, fund_code)

        if sheet_date != snapshot_date:
            logger.warning(
                "統一投信 Excel 資料日期（%s）與查詢日期（%s）不符，視為當日尚未更新",
                sheet_date, snapshot_date,
            )
            return []

        return [self._to_holding(row) for row in self._extract_stock_rows(rows)]

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": _USER_AGENT})
        session.get(_HOME_URL, timeout=_REQUEST_TIMEOUT_SECONDS)
        return session

    def _resolve_fund_code(self, session: requests.Session, etf_id: str) -> str:
        resp = session.get(_LIST_URL, timeout=_REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for link in soup.find_all("a", href=_FUND_CODE_PATTERN):
            if link.get_text(strip=True).startswith(etf_id):
                match = _FUND_CODE_PATTERN.search(link["href"])
                if match:
                    return match.group(1)
        raise RuntimeError(
            f"統一投信查無 '{etf_id}' 對應的內部代碼（FETCH_ISSUER_PCF_PARSE_ERROR），"
            "請確認代碼是否確實為統一投信旗下 ETF"
        )

    def _fetch_asset_sheet(self, session: requests.Session, fund_code: str) -> tuple[list[tuple], str]:
        resp = session.get(_ASSET_URL, params={"fundCode": fund_code}, timeout=_REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        workbook = openpyxl.load_workbook(io.BytesIO(resp.content), data_only=True)
        rows = list(workbook.worksheets[0].iter_rows(values_only=True))
        return rows, self._parse_roc_date(rows)

    @staticmethod
    def _parse_roc_date(rows: list[tuple]) -> str:
        header = str(rows[0][0]) if rows and rows[0] else ""
        match = _ROC_DATE_PATTERN.search(header)
        if not match:
            raise RuntimeError(
                "統一投信 Excel 找不到資料日期欄位（FETCH_ISSUER_PCF_PARSE_ERROR），格式可能已改版"
            )
        roc_year, month, day = match.groups()
        return f"{int(roc_year) + 1911:04d}-{month}-{day}"

    @staticmethod
    def _extract_stock_rows(rows: list[tuple]) -> list[tuple]:
        records = []
        in_section = False
        skip_header = False
        for row in rows:
            first = row[0] if row else None
            if first == _STOCK_SECTION_TITLE and not any(row[1:]):
                in_section = True
                skip_header = True
                continue
            if not in_section:
                continue
            if skip_header:
                skip_header = False  # 這一列是欄位標題（股票代號/股票名稱/股數/持股權重），跳過
                continue
            if first is None:
                break  # 空白列代表「股票」區塊結束
            records.append(row)

        if not records:
            raise RuntimeError(
                "統一投信 Excel 找不到「股票」區塊（FETCH_ISSUER_PCF_PARSE_ERROR），網站可能已改版"
            )
        return records

    @staticmethod
    def _to_holding(row: tuple) -> dict:
        stock_id, stock_name, shares = row[0], row[1], row[2]
        return {
            "component_stock_id": str(stock_id),
            "component_name": stock_name,
            "holding_shares": int(str(shares).replace(",", "")),
        }
