"""統一投信 PCF 資料來源：基金明細頁是 Vue.js 前端框架搭配伺服器端渲染，完整的資產組合
資料其實已經以 JSON 形式內嵌在頁面裡（藏在 `id="DataAsset"` 的 `<div>` `data-content`
屬性，HTML 實體編碼過，BeautifulSoup 解析屬性值時會自動還原），不需要像原本那樣另外呼叫
Excel 匯出端點、也不用處理 openpyxl／NPOI 相容性警告。資產組合依 `AssetCode` 分成好幾類
（淨資產／現金／股票...），成分股在 `AssetCode == "ST"` 那一組，每筆明細帶 `TranDate`
（交易日期）可驗證新鮮度，還附一個 `EditTime` 實際更新時間戳記可供診斷用。

頁面本身沒有查詢日期的參數（實測過 date/qDate/tranDate/ddate/assetDate 等常見命名皆無
效果，一律回傳當下最新資料），跟原本 Excel 匯出端點的限制一樣，只是換了個更乾淨的資料
來源，不影響「能不能查歷史日期」這件事。
"""
from __future__ import annotations

import json
import logging
import re

import requests
from bs4 import BeautifulSoup

from src.issuer_pcf.base import IssuerPcfProvider

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30
_USER_AGENT = "FinanceTracker-ChipMonitor/1.0"
_HOME_URL = "https://www.ezmoney.com.tw/"
_LIST_URL = "https://www.ezmoney.com.tw/ETF/Fund/Index"
_INFO_URL = "https://www.ezmoney.com.tw/ETF/Fund/Info"
_FUND_CODE_PATTERN = re.compile(r"fundCode=([A-Za-z0-9]+)")
_STOCK_ASSET_CODE = "ST"


class UniPcfAdapter(IssuerPcfProvider):
    def fetch_holdings(self, etf_id: str, snapshot_date: str) -> list[dict]:
        session = self._new_session()
        fund_code = self._resolve_fund_code(session, etf_id)
        details, tran_date = self._fetch_stock_details(session, fund_code)

        if tran_date != snapshot_date:
            logger.warning(
                "統一投信資產組合日期（%s）與查詢日期（%s）不符，視為當日尚未更新",
                tran_date, snapshot_date,
            )
            return []

        return [self._to_holding(row) for row in details]

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

    def _fetch_stock_details(self, session: requests.Session, fund_code: str) -> tuple[list[dict], str | None]:
        resp = session.get(_INFO_URL, params={"fundCode": fund_code}, timeout=_REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        container = soup.find("div", id="DataAsset")
        if container is None:
            raise RuntimeError(
                "統一投信基金明細頁找不到資產組合資料區塊（FETCH_ISSUER_PCF_PARSE_ERROR），"
                "網站可能已改版"
            )
        try:
            asset_groups = json.loads(container.get("data-content") or "")
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError(
                "統一投信資產組合資料解析失敗（FETCH_ISSUER_PCF_PARSE_ERROR），格式可能已改版"
            ) from exc

        stock_group = next((g for g in asset_groups if g.get("AssetCode") == _STOCK_ASSET_CODE), None)
        details = (stock_group or {}).get("Details") or []
        if not details:
            return [], None

        # TranDate 格式為 "YYYY-MM-DDTHH:MM:SS"，只取日期部分跟查詢日期比對。
        tran_date = str(details[0].get("TranDate", ""))[:10]
        return details, tran_date

    @staticmethod
    def _to_holding(row: dict) -> dict:
        return {
            "component_stock_id": str(row["DetailCode"]),
            "component_name": row["DetailName"],
            "holding_shares": int(row["Share"]),
        }
