import html
import json
from unittest.mock import MagicMock, patch

import pytest

from src.issuer_pcf.uni import UniPcfAdapter

_LIST_HTML = """
<html><body>
<table>
<tr><td class="title"><a title="00981A 主動統一台股增長" href="/ETF/Fund/Info?fundCode=49YTW">00981A 主動統一台股增長</a></td></tr>
<tr><td class="title"><a title="00403A 主動統一升級50" href="/ETF/Fund/Info?fundCode=63YTW">00403A 主動統一升級50</a></td></tr>
</table>
</body></html>
"""


def _build_info_html(
    tran_date="2026-08-14T00:00:00",
    stock_rows=(
        {"DetailCode": "2330", "DetailName": "台積電", "Share": 12134000.0},
    ),
    include_stock_group=True,
):
    """模擬統一官網基金明細頁：資產組合以 JSON 形式內嵌在 id="DataAsset" 的
    data-content 屬性裡，依 AssetCode 分成好幾類，用來驗證解析邏輯真的有挑對
    AssetCode == "ST" 那一組，不是隨便抓第一組就結束。
    """
    asset_groups = [
        {"AssetCode": "NAV", "AssetName": "淨資產", "Details": None},
        {"AssetCode": "CASH", "AssetName": "現金", "Details": None},
    ]
    if include_stock_group:
        asset_groups.append({
            "AssetCode": "ST",
            "AssetName": "股票",
            "Details": [
                {**row, "TranDate": tran_date, "FundCode": "49YTW"}
                for row in stock_rows
            ],
        })
    data_content = html.escape(json.dumps(asset_groups, ensure_ascii=False), quote=True)
    return f'<html><body><div id="DataAsset" data-content="{data_content}"></div></body></html>'


def _fake_text_response(text: str):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.text = text
    return resp


def _fake_session(list_html=_LIST_HTML, info_html=None):
    info_html = info_html if info_html is not None else _build_info_html()
    session = MagicMock()

    def get_side_effect(url, **kwargs):
        if url == "https://www.ezmoney.com.tw/":
            return _fake_text_response("")
        if url == "https://www.ezmoney.com.tw/ETF/Fund/Index":
            return _fake_text_response(list_html)
        if url == "https://www.ezmoney.com.tw/ETF/Fund/Info":
            return _fake_text_response(info_html)
        raise AssertionError(f"未預期的請求網址：{url}")

    session.get.side_effect = get_side_effect
    return session


def test_fetch_holdings_resolves_fund_code_then_maps_stock_group():
    adapter = UniPcfAdapter()
    with patch("src.issuer_pcf.uni.requests.Session", return_value=_fake_session()):
        records = adapter.fetch_holdings("00981A", "2026-08-14")

    assert records == [{"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 12134000}]


def test_fetch_holdings_ignores_non_stock_asset_groups():
    """NAV/現金等其他 AssetCode 群組不能被誤認成股票清單，只有 AssetCode == "ST" 那組才算數。"""
    html_page = _build_info_html(stock_rows=(
        {"DetailCode": "2330", "DetailName": "台積電", "Share": 12134000.0},
        {"DetailCode": "2454", "DetailName": "聯發科", "Share": 5448000.0},
    ))
    adapter = UniPcfAdapter()
    with patch("src.issuer_pcf.uni.requests.Session", return_value=_fake_session(info_html=html_page)):
        records = adapter.fetch_holdings("00981A", "2026-08-14")

    assert records == [
        {"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 12134000},
        {"component_stock_id": "2454", "component_name": "聯發科", "holding_shares": 5448000},
    ]


def test_fetch_holdings_returns_empty_when_tran_date_mismatches_snapshot_date():
    html_page = _build_info_html(tran_date="2026-08-13T00:00:00")
    adapter = UniPcfAdapter()
    with patch("src.issuer_pcf.uni.requests.Session", return_value=_fake_session(info_html=html_page)):
        records = adapter.fetch_holdings("00981A", "2026-08-14")

    assert records == []


def test_fetch_holdings_returns_empty_when_stock_group_missing():
    """跟「股票」表格本身缺失（結構異常，直接報錯）不同：這裡是資產組合本身結構正常，
    只是剛好沒有 ST 這個群組（例如當天尚未結算），視為當日尚未更新，不採用、不報錯。
    """
    html_page = _build_info_html(include_stock_group=False)
    adapter = UniPcfAdapter()
    with patch("src.issuer_pcf.uni.requests.Session", return_value=_fake_session(info_html=html_page)):
        records = adapter.fetch_holdings("00981A", "2026-08-14")

    assert records == []


def test_fetch_holdings_passes_resolved_fund_code_to_info_endpoint():
    adapter = UniPcfAdapter()
    session = _fake_session()
    with patch("src.issuer_pcf.uni.requests.Session", return_value=session):
        adapter.fetch_holdings("00981A", "2026-08-14")

    info_call = session.get.call_args_list[-1]
    assert info_call.args[0] == "https://www.ezmoney.com.tw/ETF/Fund/Info"
    assert info_call.kwargs["params"] == {"fundCode": "49YTW"}


def test_fetch_holdings_raises_when_ticker_not_found_in_list():
    adapter = UniPcfAdapter()
    with patch("src.issuer_pcf.uni.requests.Session", return_value=_fake_session(list_html="<html></html>")):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("99999", "2026-08-14")


def test_fetch_holdings_raises_when_data_asset_container_missing():
    adapter = UniPcfAdapter()
    with patch("src.issuer_pcf.uni.requests.Session", return_value=_fake_session(info_html="<html><body></body></html>")):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("00981A", "2026-08-14")


def test_fetch_holdings_raises_when_data_content_is_not_valid_json():
    broken_html = '<html><body><div id="DataAsset" data-content="not json"></div></body></html>'
    adapter = UniPcfAdapter()
    with patch("src.issuer_pcf.uni.requests.Session", return_value=_fake_session(info_html=broken_html)):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("00981A", "2026-08-14")
