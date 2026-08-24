"""安聯投信 PCF 資料來源：官網前台是 SPA，真正能打的是背後獨立的 webapi 後端，且這組後端
有 ASP.NET Core 的 Antiforgery 防護，要先跟 GetAntiForgeryToken 要一組 token 放進
x-xsrf-token header、並帶著同一個 Session 的 Cookie，後兩支 API 才會認。ticker 對應的內部
CFundNo 一樣不用自己維護對照表，用清單 API 查一次就能動態查出來。

持股 API 的回應把資產分成好幾張表格，其中一張沒有標題、裡面放的是「股票／期貨」各自的
總市值總覽（列資料裡本身就有一格字面上寫「股票」，容易誤認），真正的成分股清單要挑
TableTitle 開頭是「股票」的那一張；欄位用 Columns 定義順序、Rows 依序對應的位置陣列格式，
不是具名鍵值物件，所以要照 Columns 的 Name 動態找出每一欄的位置再取值。
"""
from __future__ import annotations

import logging

import requests

from src.issuer_pcf.base import IssuerPcfProvider

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30
_USER_AGENT = "FinanceTracker-ChipMonitor/1.0"
_TOKEN_URL = "https://etf.allianzgi.com.tw/webapi/api/AntiForgery/GetAntiForgeryToken"
_OVERVIEW_URL = "https://etf.allianzgi.com.tw/webapi/api/Fund/GetFundOverview"
_ASSETS_URL = "https://etf.allianzgi.com.tw/webapi/api/Fund/GetFundAssets"
_STOCK_TABLE_TITLE_PREFIX = "股票"
_STOCK_ID_COLUMN = "股票代號"
_STOCK_NAME_COLUMN = "股票名稱"
_SHARES_COLUMN = "股數"


class AllianzPcfAdapter(IssuerPcfProvider):
    def fetch_holdings(self, etf_id: str, snapshot_date: str) -> list[dict]:
        session = self._new_session()
        fund_id = self._resolve_fund_id(session, etf_id)
        data = self._fetch_assets_data(session, fund_id)

        # 沒看到可以帶查詢日期的參數，這支 API 似乎永遠只回最新一期，跟富邦一樣用回應
        # 帶的日期欄位跟查詢日期比對，不符就當作當天還沒更新。
        pcf_date = (data.get("FundAsset") or {}).get("PCFDate", "").replace("/", "-")
        if pcf_date != snapshot_date:
            logger.warning(
                "安聯 PCF 資料日期（%s）與查詢日期（%s）不符，視為當日尚未更新",
                pcf_date, snapshot_date,
            )
            return []

        stock_table = self._find_stock_table(data.get("Table") or [])
        return self._parse_rows(stock_table)

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": _USER_AGENT})
        resp = session.get(_TOKEN_URL, timeout=_REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        session.headers["x-xsrf-token"] = resp.json()["token"]
        return session

    def _resolve_fund_id(self, session: requests.Session, etf_id: str) -> str:
        resp = session.post(
            _OVERVIEW_URL,
            json={"Keyword": "", "FundNo": "", "FundType": -1, "PageSize": 999, "PageIndex": 1},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()

        for row in payload.get("Entries", []) or []:
            if row.get("CSecuritiesCode") == etf_id:
                return row["CFundNo"]
        raise RuntimeError(
            f"安聯投信查無 '{etf_id}' 對應的內部代碼（FETCH_ISSUER_PCF_PARSE_ERROR），"
            "請確認代碼是否確實為安聯投信旗下 ETF"
        )

    def _fetch_assets_data(self, session: requests.Session, fund_id: str) -> dict:
        resp = session.post(
            _ASSETS_URL,
            json={"FundID": fund_id},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()

        data = (payload.get("Entries") or {}).get("Data")
        if not data:
            raise RuntimeError(
                f"安聯 PCF API 查無 '{fund_id}' 對應資料（FETCH_ISSUER_PCF_PARSE_ERROR）"
            )
        return data

    @staticmethod
    def _find_stock_table(tables: list[dict]) -> dict:
        for table in tables:
            if (table.get("TableTitle") or "").startswith(_STOCK_TABLE_TITLE_PREFIX):
                return table
        raise RuntimeError(
            "安聯 PCF 回應內找不到「股票」表格（FETCH_ISSUER_PCF_PARSE_ERROR），API 可能已改版"
        )

    @staticmethod
    def _parse_rows(table: dict) -> list[dict]:
        columns = [(col or {}).get("Name") for col in table.get("Columns") or []]
        try:
            id_idx = columns.index(_STOCK_ID_COLUMN)
            name_idx = columns.index(_STOCK_NAME_COLUMN)
            shares_idx = columns.index(_SHARES_COLUMN)
        except ValueError as exc:
            raise RuntimeError(
                "安聯「股票」表格欄位與預期不符（FETCH_ISSUER_PCF_PARSE_ERROR），API 可能已改版"
            ) from exc

        records = []
        for row in table.get("Rows") or []:
            records.append({
                "component_stock_id": row[id_idx],
                "component_name": row[name_idx],
                "holding_shares": int(str(row[shares_idx]).replace(",", "")),
            })
        return records
