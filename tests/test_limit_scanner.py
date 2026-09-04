from unittest.mock import MagicMock

from src.limit_scanner import LimitScanner, calculate_limit_prices, evaluate_limit_type
from src.models import LimitType, MarketType


def test_calculate_limit_prices_uses_correct_tick_size_per_price_band():
    # 前收盤 100 元：理論漲停 110（[100,500) 區間升降單位 0.5，110 剛好對齊）
    # 理論跌停 90（[50,100) 區間升降單位 0.1，90 剛好對齊）
    assert calculate_limit_prices(100.0) == (110.0, 90.0)


def test_calculate_limit_prices_rounds_down_for_limit_up_and_up_for_limit_down():
    """理論價格沒有剛好落在升降單位上時，漲停要無條件捨去、跌停要無條件進位，
    確保漲跌幅絕對不會超過 10%。前收盤 33 元：理論漲停 36.3、理論跌停 29.7，
    兩者所在的 [10,50) 區間升降單位為 0.05。
    """
    limit_up, limit_down = calculate_limit_prices(33.0)
    assert limit_up == 36.3  # 36.3 剛好對齊 0.05，捨去後不變
    assert limit_down == 29.7  # 29.7 剛好對齊 0.05，進位後不變
    assert limit_up <= 33.0 * 1.1
    assert limit_down >= 33.0 * 0.9


def test_evaluate_limit_type_returns_up_when_close_hits_limit_up():
    assert evaluate_limit_type(close_price=110.0, change=10.0) == LimitType.UP


def test_evaluate_limit_type_returns_down_when_close_hits_limit_down():
    assert evaluate_limit_type(close_price=90.0, change=-10.0) == LimitType.DOWN


def test_evaluate_limit_type_returns_none_for_ordinary_move():
    assert evaluate_limit_type(close_price=105.0, change=5.0) is None


def test_evaluate_limit_type_returns_none_when_previous_close_not_positive():
    """新股掛牌首日等情況反推不出有效的前收盤價，無從判定，不能當成漲跌停。"""
    assert evaluate_limit_type(close_price=10.0, change=10.0) is None


def _fake_provider(quotes: list[dict]) -> MagicMock:
    provider = MagicMock()
    provider.fetch_daily_quotes.return_value = quotes
    return provider


def test_scan_combines_records_from_both_markets():
    twse = _fake_provider([{"stock_id": "1101", "stock_name": "台泥", "close_price": 110.0, "change": 10.0}])
    tpex = _fake_provider([{"stock_id": "6789", "stock_name": "上櫃甲", "close_price": 45.0, "change": -5.0}])
    scanner = LimitScanner(providers={MarketType.TWSE: twse, MarketType.TPEX: tpex})

    records = scanner.scan("2026-08-31")

    assert {r.stock_id for r in records} == {"1101", "6789"}
    twse_record = next(r for r in records if r.stock_id == "1101")
    assert twse_record.market == MarketType.TWSE
    assert twse_record.limit_type == LimitType.UP
    assert twse_record.prev_close_price == 100.0
    assert twse_record.change_pct == 10.0


def test_scan_excludes_quotes_that_do_not_hit_limit():
    twse = _fake_provider([{"stock_id": "1103", "stock_name": "統一", "close_price": 105.0, "change": 5.0}])
    scanner = LimitScanner(providers={MarketType.TWSE: twse, MarketType.TPEX: _fake_provider([])})

    assert scanner.scan("2026-08-31") == []


def test_scan_one_market_failure_does_not_affect_the_other():
    """單一市場抓取失敗只記錄下來、回傳空清單，不能讓另一個市場的掃描結果也不見。"""
    twse = MagicMock()
    twse.fetch_daily_quotes.side_effect = RuntimeError("boom")
    tpex = _fake_provider([{"stock_id": "6789", "stock_name": "上櫃甲", "close_price": 55.0, "change": 5.0}])
    scanner = LimitScanner(providers={MarketType.TWSE: twse, MarketType.TPEX: tpex})

    records = scanner.scan("2026-08-31")

    assert [r.stock_id for r in records] == ["6789"]
