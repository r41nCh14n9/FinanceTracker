from unittest.mock import MagicMock, patch

from src.market_quote.tpex import TpexQuoteProvider

_FIELDS = [
    "代號", "名稱", "收盤 ", "漲跌", "開盤 ", "最高 ", "最低", "成交股數  ", " 成交金額(元)", " 成交筆數 ",
    "最後買價", "最後買量<br>(張數)", "最後賣價", "最後賣量<br>(張數)", "發行股數 ", "次日漲停價 ", "次日跌停價",
]

_ROW_UP = ["6789", "上櫃甲", "55.00", "+5.00", "54.00", "55.00", "53.50", "100,000", "5,500,000", "50",
           "55.00", "10", "55.10", "5", "10,000,000", "60.00", "50.00"]
_ROW_DOWN = ["6790", "上櫃乙", "45.00", "-5.00", "46.00", "46.50", "45.00", "80,000", "3,600,000", "40",
             "44.90", "10", "45.00", "5", "8,000,000", "55.00", "45.00"]
_ROW_NORMAL = ["6791", "上櫃丙", "52.00", "+2.00", "50.50", "52.50", "50.00", "60,000", "3,100,000", "30",
               "51.90", "10", "52.00", "5", "6,000,000", "55.00", "45.00"]
_ROW_NO_TRADE = ["9998", "無交易", "--", "--", "--", "--", "--", "0", "0", "0",
                  "--", "0", "--", "0", "0", "--", "--"]

_QUOTE_TABLE = {
    "title": "上櫃股票每日收盤行情(不含定價)",
    "fields": _FIELDS,
    "data": [_ROW_UP, _ROW_DOWN, _ROW_NORMAL, _ROW_NO_TRADE],
}


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def test_fetch_daily_quotes_parses_plain_signed_change_column():
    """TPEx 的漲跌欄位本身就是帶正負號的單一數字，不像 TWSE 要拆兩欄合併。"""
    payload = {"stat": "ok", "tables": [_QUOTE_TABLE]}
    provider = TpexQuoteProvider()

    with patch("src.market_quote.tpex.requests.get", return_value=_fake_response(payload)):
        quotes = provider.fetch_daily_quotes("2026-08-31")

    by_id = {q["stock_id"]: q for q in quotes}
    assert by_id["6789"] == {"stock_id": "6789", "stock_name": "上櫃甲", "close_price": 55.0, "change": 5.0}
    assert by_id["6790"] == {"stock_id": "6790", "stock_name": "上櫃乙", "close_price": 45.0, "change": -5.0}
    assert by_id["6791"]["change"] == 2.0
    assert "9998" not in by_id


def test_fetch_daily_quotes_returns_empty_when_stat_not_ok():
    payload = {"stat": "error"}
    provider = TpexQuoteProvider()

    with patch("src.market_quote.tpex.requests.get", return_value=_fake_response(payload)):
        quotes = provider.fetch_daily_quotes("2026-08-31")

    assert quotes == []


def test_fetch_daily_quotes_converts_date_to_taiwan_era_format():
    payload = {"stat": "ok", "tables": [_QUOTE_TABLE]}
    provider = TpexQuoteProvider()

    with patch("src.market_quote.tpex.requests.get", return_value=_fake_response(payload)) as mock_get:
        provider.fetch_daily_quotes("2026-08-31")

    assert mock_get.call_args.kwargs["params"]["d"] == "115/08/31"
