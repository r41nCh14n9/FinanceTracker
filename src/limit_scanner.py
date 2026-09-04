"""判斷當日是否有股票觸及漲跌停，彙整成 LimitUpDownRecord 清單供報告產出與三大法人補查使用。

漲跌停不需要另外查前一交易日的收盤價快照：TWSE／TPEx 回應本身就同時給了「今日收盤價」
與「今日漲跌金額」，前一日收盤價可以直接反推（今日收盤 - 今日漲跌），比另外去查一次
昨天的快照更簡單，也不會因為昨天剛好沒抓到資料就整批判斷不了。
"""
from __future__ import annotations

import logging
import math

from src.market_quote.base import MarketQuoteProvider
from src.market_quote.tpex import TpexQuoteProvider
from src.market_quote.twse import TwseQuoteProvider
from src.models import LimitType, LimitUpDownRecord, MarketType

logger = logging.getLogger(__name__)

_LIMIT_PCT = 0.10  # 台股現股（含注意股／處置股，兩者僅撮合機制不同）漲跌幅限制統一為前一交易日收盤價的 ±10%
_COMPARE_TOLERANCE = 0.005  # 浮點數比對容許誤差，避免因小數點捨入誤差漏判

# 台股現行升降單位表：(價格上限（不含）, 對應升降單位)，依序取第一個符合的區間
_TICK_TABLE = [
    (10, 0.01),
    (50, 0.05),
    (100, 0.1),
    (500, 0.5),
    (1000, 1.0),
    (float("inf"), 5.0),
]


def _tick_size(price: float) -> float:
    for upper_bound, tick in _TICK_TABLE:
        if price < upper_bound:
            return tick
    return _TICK_TABLE[-1][1]


def calculate_limit_prices(prev_close: float) -> tuple[float, float]:
    """回傳 (漲停價, 跌停價)。漲停價依台股規則無條件捨去至對應升降單位（確保不超過
    +10%），跌停價無條件進位（確保跌幅不超過 -10%）。
    """
    theoretical_up = prev_close * (1 + _LIMIT_PCT)
    theoretical_down = prev_close * (1 - _LIMIT_PCT)
    tick_up = _tick_size(theoretical_up)
    tick_down = _tick_size(theoretical_down)
    limit_up = math.floor(theoretical_up / tick_up) * tick_up
    limit_down = math.ceil(theoretical_down / tick_down) * tick_down
    return round(limit_up, 2), round(limit_down, 2)


def evaluate_limit_type(close_price: float, change: float) -> LimitType | None:
    """依當日收盤價與漲跌金額判定是否觸及漲跌停；反推不出有效前收盤價時（例如新股
    掛牌首五個交易日依規定無漲跌幅限制）無從判定，回傳 None，不當成漲跌停也不當成錯誤。
    """
    prev_close = close_price - change
    if prev_close <= 0:
        return None
    limit_up, limit_down = calculate_limit_prices(prev_close)
    if abs(close_price - limit_up) <= _COMPARE_TOLERANCE:
        return LimitType.UP
    if abs(close_price - limit_down) <= _COMPARE_TOLERANCE:
        return LimitType.DOWN
    return None


class LimitScanner:
    """協調呼叫 TWSE／TPEx 兩個資料源，掃出當日全市場觸及漲跌停的股票。單一市場查詢
    失敗只會記錄下來、略過該市場，不會讓另一個市場的掃描結果也一起不見。
    """

    def __init__(self, providers: dict[MarketType, MarketQuoteProvider] | None = None):
        self._providers = providers or {
            MarketType.TWSE: TwseQuoteProvider(),
            MarketType.TPEX: TpexQuoteProvider(),
        }

    def scan(self, trade_date: str) -> list[LimitUpDownRecord]:
        records: list[LimitUpDownRecord] = []
        for market, provider in self._providers.items():
            records.extend(self._scan_one_market(market, provider, trade_date))
        return records

    def _scan_one_market(
        self, market: MarketType, provider: MarketQuoteProvider, trade_date: str
    ) -> list[LimitUpDownRecord]:
        try:
            quotes = provider.fetch_daily_quotes(trade_date)
        except Exception as exc:  # noqa: BLE001 - 單一市場失敗不能拖累另一個市場的掃描結果
            logger.warning("%s 全市場收盤行情抓取失敗：%s", market.value, exc)
            return []

        records = []
        for quote in quotes:
            limit_type = evaluate_limit_type(quote["close_price"], quote["change"])
            if limit_type is None:
                continue
            records.append(self._to_record(market, trade_date, quote, limit_type))
        return records

    @staticmethod
    def _to_record(market: MarketType, trade_date: str, quote: dict, limit_type: LimitType) -> LimitUpDownRecord:
        close_price = quote["close_price"]
        prev_close_price = close_price - quote["change"]
        change_pct = (quote["change"] / prev_close_price) * 100
        return LimitUpDownRecord(
            trade_date=trade_date,
            stock_id=quote["stock_id"],
            stock_name=quote["stock_name"],
            market=market,
            limit_type=limit_type,
            close_price=close_price,
            prev_close_price=round(prev_close_price, 2),
            change_pct=round(change_pct, 2),
        )
