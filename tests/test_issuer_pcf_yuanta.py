from unittest.mock import MagicMock, patch

import pytest

from src.issuer_pcf.yuanta import YuantaPcfAdapter

_EMPTY_PAYLOAD = {"PCF": None, "InKind": None, "Cash": None, "FundWeights": None, "Memo": None}


def _announcement_payload(trandate: str):
    return {
        "PCF": {"fundid": "1066", "markcd": "0050", "trandate": trandate},
        "InKind": {
            "FundComposition": [
                {"stkcd": "2330", "name": "台積電", "ename": "TSMC", "qty": 12734, "cashinlieu": "N", "minimum": "Y"},
                {"stkcd": "2454", "name": "聯發科", "ename": "MediaTek", "qty": 800, "cashinlieu": "N", "minimum": "Y"},
            ]
        },
    }


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _fake_get(payload_by_date: dict[str, dict]):
    """依實際打的 date query param 回應對應的假資料，沒對到的日期一律回「查無公告」的空殼。"""
    def side_effect(url, params, headers, timeout):
        return _fake_response(payload_by_date.get(params["date"], _EMPTY_PAYLOAD))

    return side_effect


def test_supports_backfill_is_enabled():
    assert YuantaPcfAdapter.SUPPORTS_BACKFILL is True


def test_fetch_holdings_queries_next_day_announcement_for_todays_holdings():
    """查詢日期 2026-08-17（一）的收盤持股，站方要查隔天（08-18）公告的檔案才拿得到，
    直接查 08-17 本身只會拿到 08-17 的『前一天』（08-14）資料，不是我們要的。
    """
    adapter = YuantaPcfAdapter()
    responses = {"20260818": _announcement_payload("20260817")}
    with patch("src.issuer_pcf.yuanta.requests.get", side_effect=_fake_get(responses)):
        records = adapter.fetch_holdings("0050", "2026-08-17")

    assert records == [
        {"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 12734},
        {"component_stock_id": "2454", "component_name": "聯發科", "holding_shares": 800},
    ]


def test_fetch_holdings_skips_weekend_to_find_next_trading_day_announcement():
    """查詢日期 2026-08-14（五）的收盤持股，隔天公告要等到下一個交易日（08-17，一）
    才有，中間週末（08-15、16）查了都是空殼，要能正確跳過繼續往後找。
    """
    adapter = YuantaPcfAdapter()
    responses = {"20260817": _announcement_payload("20260814")}
    with patch("src.issuer_pcf.yuanta.requests.get", side_effect=_fake_get(responses)) as mock_get:
        records = adapter.fetch_holdings("0050", "2026-08-14")

    assert len(records) == 2
    queried_dates = [call.kwargs["params"]["date"] for call in mock_get.call_args_list]
    assert queried_dates == ["20260815", "20260816", "20260817"]


def test_fetch_holdings_passes_ticker_query_param():
    adapter = YuantaPcfAdapter()
    responses = {"20260818": _announcement_payload("20260817")}
    with patch("src.issuer_pcf.yuanta.requests.get", side_effect=_fake_get(responses)) as mock_get:
        adapter.fetch_holdings("0050", "2026-08-17")

    last_call_params = mock_get.call_args_list[-1].kwargs["params"]
    assert last_call_params["ticker"] == "0050"
    assert last_call_params["FuncId"] == "PCF/Daily"


def test_fetch_holdings_returns_empty_when_no_announcement_matches_within_lookahead():
    """往後找了一輪（10 天）都找不到剛好對上查詢日期的公告，視為當日尚未更新，不是錯誤。"""
    adapter = YuantaPcfAdapter()
    with patch("src.issuer_pcf.yuanta.requests.get", side_effect=_fake_get({})):
        records = adapter.fetch_holdings("0050", "2026-08-17")

    assert records == []


def test_fetch_holdings_raises_when_fund_composition_missing():
    adapter = YuantaPcfAdapter()
    responses = {"20260818": {"PCF": {"trandate": "20260817"}, "InKind": {}}}
    with patch("src.issuer_pcf.yuanta.requests.get", side_effect=_fake_get(responses)):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("0050", "2026-08-17")


def test_fetch_holdings_raises_when_response_is_not_a_dict():
    adapter = YuantaPcfAdapter()

    def side_effect(url, params, headers, timeout):
        return _fake_response(None)

    with patch("src.issuer_pcf.yuanta.requests.get", side_effect=side_effect):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("0050", "2026-08-17")
