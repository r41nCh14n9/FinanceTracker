from unittest.mock import MagicMock, patch

from src.market_quote.twse import TwseQuoteProvider

_FIELDS = [
    "證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價",
    "收盤價", "漲跌(+/-)", "漲跌價差", "最後揭示買價", "最後揭示買量", "最後揭示賣價", "最後揭示賣量", "本益比",
]

_ROW_UP = ["1101", "台泥", "1,000,000", "500", "110,000,000", "109.00", "110.00", "108.00",
           "110.00", "<p style= color:red>+</p>", "10.00", "110.00", "10", "110.50", "5", "15.00"]
_ROW_DOWN = ["1102", "亞泥", "1,000,000", "500", "90,000,000", "91.00", "92.00", "89.50",
             "90.00", "<p style= color:green>-</p>", "10.00", "89.50", "10", "90.00", "5", "12.00"]
_ROW_NORMAL = ["1103", "統一", "1,000,000", "500", "105,000,000", "101.00", "106.00", "100.50",
               "105.00", "<p style= color:red>+</p>", "5.00", "104.50", "10", "105.00", "5", "20.00"]
_ROW_NO_TRADE = ["9999", "停牌股", "0", "0", "0", "--", "--", "--",
                  "--", "<p style= color:black> </p>", "--", "--", "0", "--", "0", "--"]

_DECOY_TABLE = {
    "title": "大盤統計資訊",
    "fields": ["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)"],
    "data": [["發行量加權股價指數", "20000.00", "<p style= color:red>+</p>", "50.00", "0.25"]],
}
_QUOTE_TABLE = {
    "title": "115年08月31日 每日收盤行情",
    "fields": _FIELDS,
    "data": [_ROW_UP, _ROW_DOWN, _ROW_NORMAL, _ROW_NO_TRADE],
}


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def test_fetch_daily_quotes_finds_quote_table_among_decoys_and_parses_signed_change():
    payload = {"stat": "OK", "tables": [_DECOY_TABLE, _QUOTE_TABLE]}
    provider = TwseQuoteProvider()

    with patch("src.market_quote.twse.requests.get", return_value=_fake_response(payload)):
        quotes = provider.fetch_daily_quotes("2026-08-31")

    by_id = {q["stock_id"]: q for q in quotes}
    assert by_id["1101"] == {"stock_id": "1101", "stock_name": "台泥", "close_price": 110.0, "change": 10.0}
    assert by_id["1102"] == {"stock_id": "1102", "stock_name": "亞泥", "close_price": 90.0, "change": -10.0}
    assert by_id["1103"]["change"] == 5.0


def test_fetch_daily_quotes_skips_rows_with_unparseable_close_price():
    """收盤價是 "--"（今日無成交）的股票沒有意義判斷漲跌停，直接跳過這筆，不當成錯誤。"""
    payload = {"stat": "OK", "tables": [_QUOTE_TABLE]}
    provider = TwseQuoteProvider()

    with patch("src.market_quote.twse.requests.get", return_value=_fake_response(payload)):
        quotes = provider.fetch_daily_quotes("2026-08-31")

    assert "9999" not in {q["stock_id"] for q in quotes}


def test_fetch_daily_quotes_returns_empty_when_stat_not_ok():
    """非交易日或查詢異常時 TWSE 會回傳非 OK 的 stat，視為當日查無資料，不是錯誤。"""
    payload = {"stat": "ERROR"}
    provider = TwseQuoteProvider()

    with patch("src.market_quote.twse.requests.get", return_value=_fake_response(payload)):
        quotes = provider.fetch_daily_quotes("2026-08-31")

    assert quotes == []


def test_fetch_daily_quotes_returns_empty_when_no_matching_table():
    payload = {"stat": "OK", "tables": [_DECOY_TABLE]}
    provider = TwseQuoteProvider()

    with patch("src.market_quote.twse.requests.get", return_value=_fake_response(payload)):
        quotes = provider.fetch_daily_quotes("2026-08-31")

    assert quotes == []


def test_fetch_daily_quotes_sends_date_without_dashes():
    payload = {"stat": "OK", "tables": [_QUOTE_TABLE]}
    provider = TwseQuoteProvider()

    with patch("src.market_quote.twse.requests.get", return_value=_fake_response(payload)) as mock_get:
        provider.fetch_daily_quotes("2026-08-31")

    assert mock_get.call_args.kwargs["params"]["date"] == "20260831"
