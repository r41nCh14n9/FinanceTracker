from unittest.mock import MagicMock, patch

import pytest

from src.issuer_pcf.capital import CapitalPcfAdapter

_LIST_PAYLOAD = {
    "code": 200,
    "data": {
        "etfFilters": [],
        "funds": [
            {"fundNo": "195", "stockNo": "00919", "shortName": "群益台灣精選高息"},
            {"fundNo": "399", "stockNo": "00982A", "shortName": "主動群益台灣強棒"},
        ],
    },
    "message": "",
}

_BUYBACK_PAYLOAD = {
    "code": 200,
    "data": {
        "pcf": {"fundName": "群益台灣精選高息ETF基金", "date1": "2026-08-11", "date2": "2026-08-10"},
        "stocks": [
            {"date1": "2026/8/11", "stocNo": "2881", "stocName": "富邦金", "share": 608790000.0, "weight": 14.07},
            {"date1": "2026/8/11", "stocNo": "2882", "stocName": "國泰金", "share": 682540000.0, "weight": 12.21},
        ],
        "bonds": [],
        "futures": [],
    },
    "message": "",
}


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _fake_post(list_payload=_LIST_PAYLOAD, buyback_payload=_BUYBACK_PAYLOAD):
    def side_effect(url, **kwargs):
        if url == "https://www.capitalfund.com.tw/CFWeb/api/etf/list":
            return _fake_response(list_payload)
        if url == "https://www.capitalfund.com.tw/CFWeb/api/etf/buyback":
            return _fake_response(buyback_payload)
        raise AssertionError(f"未預期的請求網址：{url}")

    return side_effect


def test_supports_backfill_is_enabled():
    assert CapitalPcfAdapter.SUPPORTS_BACKFILL is True


def test_fetch_holdings_resolves_fund_id_then_maps_stocks_block():
    adapter = CapitalPcfAdapter()
    with patch("src.issuer_pcf.capital.requests.post", side_effect=_fake_post()):
        records = adapter.fetch_holdings("00919", "2026-08-11")

    assert records == [
        {"component_stock_id": "2881", "component_name": "富邦金", "holding_shares": 608790000},
        {"component_stock_id": "2882", "component_name": "國泰金", "holding_shares": 682540000},
    ]


def test_fetch_holdings_ignores_pcf_block_even_if_it_looks_like_holdings():
    """data.pcf 只是基金層級概況（NAV/受益權單位數），不能誤當成持股清單，持股要看 data.stocks。"""
    payload = {
        "code": 200,
        "data": {
            "pcf": {"fundName": "群益台灣精選高息ETF基金", "date1": "2026-08-11", "nav": 556122402662.0},
            "stocks": [
                {"stocNo": "2891", "stocName": "中信金", "share": 918044000.0, "weight": 9.88},
            ],
        },
        "message": "",
    }
    adapter = CapitalPcfAdapter()
    with patch("src.issuer_pcf.capital.requests.post", side_effect=_fake_post(buyback_payload=payload)):
        records = adapter.fetch_holdings("00919", "2026-08-11")

    assert records == [{"component_stock_id": "2891", "component_name": "中信金", "holding_shares": 918044000}]


def test_fetch_holdings_passes_resolved_fund_id_and_date_to_buyback_api():
    adapter = CapitalPcfAdapter()
    with patch("src.issuer_pcf.capital.requests.post", side_effect=_fake_post()) as mock_post:
        adapter.fetch_holdings("00919", "2026-08-11")

    buyback_call = mock_post.call_args_list[1]
    assert buyback_call.args[0] == "https://www.capitalfund.com.tw/CFWeb/api/etf/buyback"
    assert buyback_call.kwargs["json"] == {"fundId": "195", "date": "2026-08-11"}


def test_fetch_holdings_returns_empty_when_non_trading_day():
    """非交易日（週末）站方實測回傳 code:400、data:null，要視為 NO_DATA 而非錯誤。"""
    payload = {"code": 400, "data": None, "message": "沒有查詢到該條件的資料"}
    adapter = CapitalPcfAdapter()
    with patch("src.issuer_pcf.capital.requests.post", side_effect=_fake_post(buyback_payload=payload)):
        records = adapter.fetch_holdings("00919", "2026-08-15")

    assert records == []


def test_fetch_holdings_returns_empty_when_pcf_date_mismatches_snapshot_date():
    """回應日期跟查詢日期對不上時（理論上不該發生，但多一道防呆），不要誤存成當日資料。"""
    payload = {
        "code": 200,
        "data": {
            "pcf": {"fundName": "群益台灣精選高息ETF基金", "date1": "2026-08-10"},
            "stocks": [{"stocNo": "2881", "stocName": "富邦金", "share": 1000.0, "weight": 1.0}],
        },
        "message": "",
    }
    adapter = CapitalPcfAdapter()
    with patch("src.issuer_pcf.capital.requests.post", side_effect=_fake_post(buyback_payload=payload)):
        records = adapter.fetch_holdings("00919", "2026-08-11")

    assert records == []


def test_fetch_holdings_raises_when_ticker_not_found_in_list():
    """查詢清單 API 找不到對應市場代碼時，代表這檔 ETF 根本不屬於群益投信，要直接報錯而非誤查其他基金。"""
    adapter = CapitalPcfAdapter()
    with patch("src.issuer_pcf.capital.requests.post", side_effect=_fake_post(list_payload={"code": 200, "data": {"funds": []}, "message": ""})):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("99999", "2026-08-11")
