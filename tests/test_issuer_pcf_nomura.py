import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.issuer_pcf.nomura import NomuraPcfAdapter

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "nomura_assets_00980a.json"

_SAMPLE_PAYLOAD = {
    "Entries": {
        "FundID": "00980A",
        "Data": {
            "FundAsset": {"NavDate": "2026/08/13"},
            "Table": [
                {
                    "TableTitle": "股票",
                    "Rows": [
                        ["2330", "台積電", "663000", "7.87"],
                        ["2454", "聯發科", "279000", "5.75"],
                    ],
                },
                {"TableTitle": "期貨", "Rows": [["TX", "台股期貨", "158", "7.09"]]},
            ],
        },
    }
}


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def test_fetch_holdings_maps_fields_and_skips_non_stock_tables():
    adapter = NomuraPcfAdapter()
    with patch("src.issuer_pcf.nomura.requests.post", return_value=_fake_response(_SAMPLE_PAYLOAD)):
        records = adapter.fetch_holdings("00980A", "2026-08-13")

    assert records == [
        {"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 663000},
        {"component_stock_id": "2454", "component_name": "聯發科", "holding_shares": 279000},
    ]


def test_fetch_holdings_returns_empty_when_nav_date_not_updated():
    adapter = NomuraPcfAdapter()
    with patch("src.issuer_pcf.nomura.requests.post", return_value=_fake_response(_SAMPLE_PAYLOAD)):
        records = adapter.fetch_holdings("00980A", "2026-08-12")

    assert records == []


def test_fetch_holdings_raises_when_stock_table_missing():
    payload = {
        "Entries": {
            "FundID": "00980A",
            "Data": {
                "FundAsset": {"NavDate": "2026/08/13"},
                "Table": [{"TableTitle": "期貨", "Rows": [["TX", "台股期貨", "158", "7.09"]]}],
            },
        }
    }
    adapter = NomuraPcfAdapter()
    with patch("src.issuer_pcf.nomura.requests.post", return_value=_fake_response(payload)):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("00980A", "2026-08-13")


def test_fetch_holdings_raises_when_fund_id_not_found():
    """FundID 不屬於野村旗下 ETF 時，API 回應 Entries.Data 為空，不能誤判成 0 檔持股。"""
    payload = {"Entries": {"FundID": "99999", "Data": None}}
    adapter = NomuraPcfAdapter()
    with patch("src.issuer_pcf.nomura.requests.post", return_value=_fake_response(payload)):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("99999", "2026-08-13")


def test_fetch_holdings_parses_real_captured_response_fixture():
    """用實際查證時擷取（並裁減筆數）的真實回應驗證解析邏輯，不是憑空捏造的資料形狀。"""
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    adapter = NomuraPcfAdapter()
    with patch("src.issuer_pcf.nomura.requests.post", return_value=_fake_response(payload)):
        records = adapter.fetch_holdings("00980A", "2026-08-13")

    assert records == [
        {"component_stock_id": "2330", "component_name": "台灣積體電路製造", "holding_shares": 663000},
        {"component_stock_id": "2454", "component_name": "聯發科技", "holding_shares": 279000},
        {"component_stock_id": "2059", "component_name": "川湖科技", "holding_shares": 91000},
    ]
