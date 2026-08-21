from unittest.mock import MagicMock, patch

import pytest

from src.issuer_pcf.fuhwa import FuhwaPcfAdapter

_LIST_PAYLOAD = {
    "result": [
        {"fundID": "ETF01", "etf002": "006207", "twNameFull": "復華滬深300 A股基金"},
        {"fundID": "ETF21", "etf002": "00929", "twNameFull": "復華台灣科技優息基金"},
        {"fundID": "ETF23", "etf002": "00991A", "twNameFull": "復華台灣未來50主動式ETF基金"},
    ]
}

_ASSET_PAYLOAD = {
    "result": [
        {
            "fundID": "ETF23",
            "etf002": "00991A",
            "dDate": "2026/08/20",
            "result": [
                {"ftype": "股票", "itemName": "股票", "tot_mvalue": "85,549,782,876"},
                {"ftype": "其他資產", "itemName": "現金餘額(NTD)", "tot_mvalue": "270,226,635"},
            ],
            "detail": [
                {"ftype": "股票", "stockid": "2330", "stockname": "台灣積體", "qshare": "4,500,000", "mvalue": "10,687,500,000", "price": "2,375.0000"},
                {"ftype": "股票", "stockid": "2383", "stockname": "台光電子", "qshare": "1,430,000", "mvalue": "8,558,550,000", "price": "5,985.0000"},
                {"ftype": "其他資產", "stockid": None, "stockname": "應付買回款", "qshare": "0", "mvalue": "-2,350,769,753", "price": None},
            ],
        }
    ]
}


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _fake_get(list_payload=_LIST_PAYLOAD, asset_payload=_ASSET_PAYLOAD):
    def side_effect(url, **kwargs):
        if url == "https://www.fhtrust.com.tw/api/fundList":
            return _fake_response(list_payload)
        if url == "https://www.fhtrust.com.tw/api/assets":
            return _fake_response(asset_payload)
        raise AssertionError(f"未預期的請求網址：{url}")

    return side_effect


def test_supports_backfill_is_enabled():
    assert FuhwaPcfAdapter.SUPPORTS_BACKFILL is True


def test_fetch_holdings_resolves_fund_id_then_filters_stock_rows_only():
    """detail 陣列混了股票跟其他資產，只有 ftype=='股票' 那幾列才是持股。"""
    adapter = FuhwaPcfAdapter()
    with patch("src.issuer_pcf.fuhwa.requests.get", side_effect=_fake_get()):
        records = adapter.fetch_holdings("00991A", "2026-08-20")

    assert records == [
        {"component_stock_id": "2330", "component_name": "台灣積體", "holding_shares": 4500000},
        {"component_stock_id": "2383", "component_name": "台光電子", "holding_shares": 1430000},
    ]


def test_fetch_holdings_sends_slash_formatted_date_not_dash():
    """qDate 只吃 yyyy/MM/dd，用連字號格式會被官網默默導去首頁 HTML 而非報錯，容易誤判成端點失效。"""
    adapter = FuhwaPcfAdapter()
    with patch("src.issuer_pcf.fuhwa.requests.get", side_effect=_fake_get()) as mock_get:
        adapter.fetch_holdings("00991A", "2026-08-20")

    asset_call = mock_get.call_args_list[-1]
    assert asset_call.kwargs["params"]["qDate"] == "2026/08/20"


def test_fetch_holdings_returns_empty_when_no_data_for_date():
    """非交易日站方回傳 dDate: null、detail: null，要視為 NO_DATA 而非錯誤。"""
    payload = {"result": [{"fundID": None, "etf002": None, "dDate": None, "result": None, "detail": None}]}
    adapter = FuhwaPcfAdapter()
    with patch("src.issuer_pcf.fuhwa.requests.get", side_effect=_fake_get(asset_payload=payload)):
        records = adapter.fetch_holdings("00991A", "2026-08-22")

    assert records == []


def test_fetch_holdings_returns_empty_when_response_date_mismatches_snapshot_date():
    adapter = FuhwaPcfAdapter()
    with patch("src.issuer_pcf.fuhwa.requests.get", side_effect=_fake_get()):
        records = adapter.fetch_holdings("00991A", "2026-08-19")

    assert records == []


def test_fetch_holdings_raises_when_ticker_not_found_in_list():
    adapter = FuhwaPcfAdapter()
    with patch("src.issuer_pcf.fuhwa.requests.get", side_effect=_fake_get(list_payload={"result": []})):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("99999", "2026-08-20")
