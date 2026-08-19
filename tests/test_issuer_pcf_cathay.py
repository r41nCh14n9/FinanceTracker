from unittest.mock import MagicMock, patch

import pytest

from src.issuer_pcf.cathay import CathayPcfAdapter

_LIST_PAYLOAD = {
    "totalCount": 1,
    "totalPage": 1,
    "result": [
        {
            "fundSn": None,
            "fundCode": "CN",
            "stockCode": "00878",
            "stockShortName": "國泰永續高股息(基金之配息來源可能為由基金平準金並無保證收益及配息)",
            "stockShortNameFix": "國泰永續高股息",
            "fundName": "國泰台灣ESG永續高股息ETF基金",
            "fundSName": "國泰永續高股息",
            "fundTypeName": "ETF",
            "fundTypeCode": "25",
            "fundTypeOrderNum": None,
            "etfTypeName": "國內ETF",
        }
    ],
}

_DETAIL_PAYLOAD = {
    "result": [
        {"stockCode": "2891", "stockName": "中信金", "volumn": "918,044,000", "weights": "9.88"},
        {"stockCode": "2382", "stockName": "廣達", "volumn": "176,721,000", "weights": "8.98"},
        {"stockCode": "2882", "stockName": "國泰金", "volumn": "368,639,486", "weights": "5.92"},
    ]
}


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _fake_get(list_payload=_LIST_PAYLOAD, detail_payload=_DETAIL_PAYLOAD):
    def side_effect(url, **kwargs):
        if url == "https://cwapi.cathaysite.com.tw/api/ETF/GetETFList":
            return _fake_response(list_payload)
        if url == "https://cwapi.cathaysite.com.tw/api/ETF/GetETFDetailStockList":
            return _fake_response(detail_payload)
        raise AssertionError(f"未預期的請求網址：{url}")

    return side_effect


def test_fetch_holdings_resolves_fund_code_then_maps_detail_fields():
    adapter = CathayPcfAdapter()
    with patch("src.issuer_pcf.cathay.requests.get", side_effect=_fake_get()):
        records = adapter.fetch_holdings("00878", "2026-08-11")

    assert records == [
        {"component_stock_id": "2891", "component_name": "中信金", "holding_shares": 918044000},
        {"component_stock_id": "2382", "component_name": "廣達", "holding_shares": 176721000},
        {"component_stock_id": "2882", "component_name": "國泰金", "holding_shares": 368639486},
    ]


def test_fetch_holdings_passes_resolved_fund_code_and_search_date_to_detail_api():
    adapter = CathayPcfAdapter()
    with patch("src.issuer_pcf.cathay.requests.get", side_effect=_fake_get()) as mock_get:
        adapter.fetch_holdings("00878", "2026-08-11")

    detail_call = mock_get.call_args_list[1]
    assert detail_call.args[0] == "https://cwapi.cathaysite.com.tw/api/ETF/GetETFDetailStockList"
    assert detail_call.kwargs["params"]["FundCode"] == "CN"
    assert detail_call.kwargs["params"]["SearchDate"] == "2026-08-11"


def test_fetch_holdings_returns_empty_when_detail_has_no_data_for_date():
    """SearchDate 當天國泰站方尚未更新資料時，明細 API 回空陣列，要視為 NO_DATA 而非錯誤。"""
    adapter = CathayPcfAdapter()
    with patch("src.issuer_pcf.cathay.requests.get", side_effect=_fake_get(detail_payload={"result": []})):
        records = adapter.fetch_holdings("00878", "2026-08-11")

    assert records == []


def test_fetch_holdings_returns_empty_when_detail_result_is_null():
    """非交易日（週末/國定假日）站方實測回傳 result: null（而非前一日舊資料），同樣要視為 NO_DATA。"""
    payload = {"result": None, "returnCode": "4005", "success": False, "returnMessage": "查無資料"}
    adapter = CathayPcfAdapter()
    with patch("src.issuer_pcf.cathay.requests.get", side_effect=_fake_get(detail_payload=payload)):
        records = adapter.fetch_holdings("00878", "2026-08-15")

    assert records == []


def test_fetch_holdings_raises_when_ticker_not_found_in_list():
    """查詢清單 API 找不到對應市場代碼時，代表這檔 ETF 根本不屬於國泰投信，要直接報錯而非誤查其他基金。"""
    adapter = CathayPcfAdapter()
    with patch("src.issuer_pcf.cathay.requests.get", side_effect=_fake_get(list_payload={"result": []})):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("99999", "2026-08-11")
