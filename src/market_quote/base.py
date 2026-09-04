"""全市場收盤行情資料源的共同介面與數字解析工具，讓 LimitScanner 不需要知道 TWSE／TPEx
回應格式的差異（漲跌欄位一個拆成符號＋幅度兩欄、一個直接給帶正負號的單一數字）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class MarketQuoteProvider(ABC):
    @abstractmethod
    def fetch_daily_quotes(self, trade_date: str) -> list[dict]:
        """取得指定交易日全市場個股收盤行情。

        回傳每筆為 dict，鍵為 stock_id / stock_name / close_price / change，
        change 為帶正負號的漲跌金額（漲為正、跌為負）。查無資料（假日、尚未開盤）
        回傳空清單，不是錯誤；連線失敗或回應格式不如預期一律拋出例外，交由呼叫端
        記錄並標記該市場當次抓取失敗。
        """


def parse_number(text: str | None) -> float | None:
    """把可能帶千分位逗號的數字字串轉成 float；空字串／None／無法解析一律回傳 None，
    交由呼叫端決定要不要跳過這筆資料，不要在這裡假裝成 0 誤導後續的漲跌停判斷。
    """
    if text is None:
        return None
    cleaned = text.strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_html_text(value: str | None) -> str:
    """有些欄位（如 TWSE 的漲跌符號）是包在 `<p style=...>內容</p>` 這種 HTML 片段裡，
    這裡取出標籤中間的純文字；如果本來就是純文字（沒有 HTML 標籤），原樣回傳去除頭尾空白後的結果。
    """
    if not value:
        return ""
    first_gt = value.find(">")
    next_lt = value.find("<", first_gt + 1)
    if first_gt == -1 or next_lt == -1:
        return value.strip()
    return value[first_gt + 1 : next_lt].strip()
