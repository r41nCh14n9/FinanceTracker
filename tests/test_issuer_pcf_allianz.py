import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.issuer_pcf.allianz import AllianzPcfAdapter

_OVERVIEW_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "allianz_overview.json"
_ASSETS_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "allianz_assets_e0001.json"

_TOKEN_PAYLOAD = {"token": "fake-xsrf-token", "maxAgeSeconds": 86400}

_OVERVIEW_PAYLOAD = {
    "TotalItems": 2,
    "Entries": [
        {"CFundNo": "E0001", "CSecuritiesCode": "00984A", "CFullName": "安聯台灣高息成長主動式ETF"},
        {"CFundNo": "E0002", "CSecuritiesCode": "00993A", "CFullName": "安聯台灣主動式ETF"},
    ],
}

_ASSETS_PAYLOAD = {
    "Entries": {
        "FundID": "E0001",
        "Data": {
            "FundAsset": {"NavDate": "2026/08/21", "PCFDate": "2026/08/24"},
            "Table": [
                {
                    # 沒有標題的資產總覽表格，列資料裡本身就有一格字面上是「股票」，
                    # 用來驗證解析邏輯真的是靠 TableTitle 挑表格，不是靠掃描格子內容。
                    "TableTitle": "",
                    "Columns": [{"Name": None, "TextAlign": None}],
                    "Rows": [["股票", "TWD$9,475,097,854", "TWD", "9,475,097,854"]],
                },
                {
                    "TableTitle": "股票 (95.49%)",
                    "Columns": [
                        {"Name": "序號", "TextAlign": "center"},
                        {"Name": "股票代號", "TextAlign": "center"},
                        {"Name": "股票名稱", "TextAlign": "center"},
                        {"Name": "股數", "TextAlign": "center"},
                        {"Name": "權重(%)", "TextAlign": "center"},
                    ],
                    "Rows": [
                        ["1", "2059", "川湖", "41,000", "5.51%"],
                        ["2", "2330", "台積電", "150,000", "3.64%"],
                    ],
                },
                {
                    "TableTitle": "期貨",
                    "Columns": [
                        {"Name": "序號", "TextAlign": "center"},
                        {"Name": "期貨代號", "TextAlign": "center"},
                        {"Name": "期貨名稱", "TextAlign": "center"},
                        {"Name": "口數", "TextAlign": "center"},
                        {"Name": "權重(%)", "TextAlign": "center"},
                        {"Name": "契約年月", "TextAlign": "center"},
                    ],
                    "Rows": [["1", "TX", "台指期貨", "24", "2.16%", "2026/09"]],
                },
            ],
        },
    }
}


def _fake_json_response(payload: dict):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _fake_session(token_payload=None, overview_payload=None, assets_payload=None):
    token_payload = token_payload if token_payload is not None else _TOKEN_PAYLOAD
    overview_payload = overview_payload if overview_payload is not None else _OVERVIEW_PAYLOAD
    assets_payload = assets_payload if assets_payload is not None else _ASSETS_PAYLOAD

    session = MagicMock()
    session.headers = {}
    session.get.return_value = _fake_json_response(token_payload)

    def post_side_effect(url, **kwargs):
        if url == "https://etf.allianzgi.com.tw/webapi/api/Fund/GetFundOverview":
            return _fake_json_response(overview_payload)
        if url == "https://etf.allianzgi.com.tw/webapi/api/Fund/GetFundAssets":
            return _fake_json_response(assets_payload)
        raise AssertionError(f"未預期的請求網址：{url}")

    session.post.side_effect = post_side_effect
    return session


def test_fetch_holdings_maps_fields_and_skips_non_stock_tables():
    """fixture 的 NavDate 是 2026/08/21、PCFDate 是 2026/08/24——查詢日期要對上 NavDate
    （實際持股日）才拿得到資料，這也同時驗證了 PCFDate 不能拿來當比對依據。
    """
    adapter = AllianzPcfAdapter()
    with patch("src.issuer_pcf.allianz.requests.Session", return_value=_fake_session()):
        records = adapter.fetch_holdings("00984A", "2026-08-21")

    assert records == [
        {"component_stock_id": "2059", "component_name": "川湖", "holding_shares": 41000},
        {"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 150000},
    ]


def test_fetch_holdings_sends_xsrf_token_header_and_resolved_fund_id():
    adapter = AllianzPcfAdapter()
    session = _fake_session()
    with patch("src.issuer_pcf.allianz.requests.Session", return_value=session):
        adapter.fetch_holdings("00984A", "2026-08-21")

    assert session.headers["x-xsrf-token"] == "fake-xsrf-token"
    assets_call = session.post.call_args_list[-1]
    assert assets_call.args[0] == "https://etf.allianzgi.com.tw/webapi/api/Fund/GetFundAssets"
    assert assets_call.kwargs["json"] == {"FundID": "E0001"}


def test_fetch_holdings_returns_empty_when_nav_date_mismatches_snapshot_date():
    adapter = AllianzPcfAdapter()
    with patch("src.issuer_pcf.allianz.requests.Session", return_value=_fake_session()):
        records = adapter.fetch_holdings("00984A", "2026-08-23")

    assert records == []


def test_fetch_holdings_returns_empty_when_snapshot_date_matches_pcf_date_not_nav_date():
    """2026-08-24 是 fixture 裡的 PCFDate（公告日，恆比實際持股日晚一天），不是 NavDate；
    誤用 PCFDate 驗證是本次修正前的真實 bug，這裡明確鎖住不能再退回去。
    """
    adapter = AllianzPcfAdapter()
    with patch("src.issuer_pcf.allianz.requests.Session", return_value=_fake_session()):
        records = adapter.fetch_holdings("00984A", "2026-08-24")

    assert records == []


def test_fetch_holdings_raises_when_ticker_not_found_in_overview():
    adapter = AllianzPcfAdapter()
    with patch("src.issuer_pcf.allianz.requests.Session", return_value=_fake_session()):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("99999", "2026-08-21")


def test_fetch_holdings_raises_when_stock_table_missing():
    payload = {
        "Entries": {
            "FundID": "E0001",
            "Data": {
                "FundAsset": {"NavDate": "2026/08/24", "PCFDate": "2026/08/25"},
                "Table": [{"TableTitle": "期貨", "Columns": [], "Rows": []}],
            },
        }
    }
    adapter = AllianzPcfAdapter()
    with patch("src.issuer_pcf.allianz.requests.Session", return_value=_fake_session(assets_payload=payload)):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("00984A", "2026-08-24")


def test_fetch_holdings_raises_when_stock_table_columns_unexpected():
    """欄位名稱跟預期不符時要直接報錯，不能悄悄用錯的欄位位置組出錯誤資料。"""
    payload = {
        "Entries": {
            "FundID": "E0001",
            "Data": {
                "FundAsset": {"NavDate": "2026/08/24", "PCFDate": "2026/08/25"},
                "Table": [
                    {
                        "TableTitle": "股票 (95.49%)",
                        "Columns": [{"Name": "代號"}, {"Name": "名稱"}],
                        "Rows": [["2330", "台積電"]],
                    }
                ],
            },
        }
    }
    adapter = AllianzPcfAdapter()
    with patch("src.issuer_pcf.allianz.requests.Session", return_value=_fake_session(assets_payload=payload)):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("00984A", "2026-08-24")


def test_fetch_holdings_raises_when_fund_data_missing():
    payload = {"Entries": {"FundID": "E0001", "Data": None}}
    adapter = AllianzPcfAdapter()
    with patch("src.issuer_pcf.allianz.requests.Session", return_value=_fake_session(assets_payload=payload)):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("00984A", "2026-08-21")


def test_fetch_holdings_parses_real_captured_response_fixtures():
    """用實際查證時擷取（並裁減筆數）的真實回應驗證解析邏輯，不是憑空捏造的資料形狀；
    fixture 的 NavDate 是 2026/08/21、PCFDate 是 2026/08/24，查詢日期要對上 NavDate。
    """
    overview_payload = json.loads(_OVERVIEW_FIXTURE_PATH.read_text(encoding="utf-8"))
    assets_payload = json.loads(_ASSETS_FIXTURE_PATH.read_text(encoding="utf-8"))

    adapter = AllianzPcfAdapter()
    session = _fake_session(overview_payload=overview_payload, assets_payload=assets_payload)
    with patch("src.issuer_pcf.allianz.requests.Session", return_value=session):
        records = adapter.fetch_holdings("00984A", "2026-08-21")

    assert records == [
        {"component_stock_id": "2059", "component_name": "川湖", "holding_shares": 41000},
        {"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 150000},
        {"component_stock_id": "3008", "component_name": "大立光電", "holding_shares": 50000},
    ]
