"""各投信官網 PCF 資料來源的共同介面，讓 Fetcher 不需要知道背後是哪家投信、怎麼爬的。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class IssuerPcfProvider(ABC):
    @abstractmethod
    def fetch_holdings(self, etf_id: str, snapshot_date: str) -> list[dict]:
        """取得指定 ETF 於指定日期的成分股清單。

        回傳每筆為 dict，鍵為 component_stock_id / component_name / holding_shares，
        與 EtfHoldingRecord 的欄位一致，方便呼叫端直接組出該 dataclass。

        若網站當天尚未更新到查詢日期的資料，回傳空清單（不是錯誤）；
        其餘無法取得資料的情況（連線失敗、頁面結構跟預期不符等）一律拋出例外，
        交由呼叫端記錄並標記該 ETF 當次抓取失敗。
        """
