import json
from unittest.mock import MagicMock, patch

import pytest

from src.issuer_pcf.yuanta import YuantaPcfAdapter

_SAMPLE_NUXT_STATE = {
    "fetch": [
        {"dev": True},
        {
            "pcfData": {
                "PCF": {"markcd": "0050", "trandate": "20260812"},
                "InKind": {
                    "FundComposition": [
                        {"stkcd": "2330", "name": "台積電", "ename": "TSMC", "qty": 12734, "cashinlieu": "N", "minimum": "Y"},
                        {"stkcd": "2454", "name": "聯發科", "ename": "MediaTek", "qty": 800, "cashinlieu": "N", "minimum": "Y"},
                    ]
                },
            }
        },
    ]
}


def _fake_html_response():
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.text = "<html>...window.__NUXT__=(function(){})();...</html>"
    return resp


def _fake_node_result(returncode=0, stdout="", stderr=""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_fetch_holdings_maps_fields_from_nuxt_state():
    adapter = YuantaPcfAdapter()
    with patch("src.issuer_pcf.yuanta.requests.get", return_value=_fake_html_response()), \
         patch("src.issuer_pcf.yuanta.subprocess.run", return_value=_fake_node_result(stdout=json.dumps(_SAMPLE_NUXT_STATE))):
        records = adapter.fetch_holdings("0050", "2026-08-12")

    assert records == [
        {"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 12734},
        {"component_stock_id": "2454", "component_name": "聯發科", "holding_shares": 800},
    ]


def test_fetch_holdings_returns_empty_when_trade_date_not_updated():
    adapter = YuantaPcfAdapter()
    with patch("src.issuer_pcf.yuanta.requests.get", return_value=_fake_html_response()), \
         patch("src.issuer_pcf.yuanta.subprocess.run", return_value=_fake_node_result(stdout=json.dumps(_SAMPLE_NUXT_STATE))):
        records = adapter.fetch_holdings("0050", "2026-08-11")

    assert records == []


def test_fetch_holdings_raises_when_node_not_installed():
    adapter = YuantaPcfAdapter()
    with patch("src.issuer_pcf.yuanta.requests.get", return_value=_fake_html_response()), \
         patch("src.issuer_pcf.yuanta.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_NODE_UNAVAILABLE"):
            adapter.fetch_holdings("0050", "2026-08-12")


def test_fetch_holdings_raises_when_node_script_exits_nonzero():
    adapter = YuantaPcfAdapter()
    with patch("src.issuer_pcf.yuanta.requests.get", return_value=_fake_html_response()), \
         patch("src.issuer_pcf.yuanta.subprocess.run", return_value=_fake_node_result(returncode=1, stderr="找不到 window.__NUXT__")):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_NUXT_EXTRACT_ERROR"):
            adapter.fetch_holdings("0050", "2026-08-12")


def test_fetch_holdings_raises_when_node_output_is_not_json():
    adapter = YuantaPcfAdapter()
    with patch("src.issuer_pcf.yuanta.requests.get", return_value=_fake_html_response()), \
         patch("src.issuer_pcf.yuanta.subprocess.run", return_value=_fake_node_result(stdout="not json")):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_NUXT_EXTRACT_ERROR"):
            adapter.fetch_holdings("0050", "2026-08-12")


def test_fetch_holdings_raises_when_pcf_data_missing():
    adapter = YuantaPcfAdapter()
    empty_state = json.dumps({"fetch": [{"dev": True}, {"other": 1}]})
    with patch("src.issuer_pcf.yuanta.requests.get", return_value=_fake_html_response()), \
         patch("src.issuer_pcf.yuanta.subprocess.run", return_value=_fake_node_result(stdout=empty_state)):
        with pytest.raises(RuntimeError, match="pcfData"):
            adapter.fetch_holdings("0050", "2026-08-12")
