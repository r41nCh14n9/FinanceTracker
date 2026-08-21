import io
from unittest.mock import MagicMock, patch

import openpyxl
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


def _build_asset_xlsx(date_label="115/08/14", stock_rows=(("2330", "台積電", "12,134,000", "9.30%"),)):
    """模擬統一官網匯出檔的版面：同一張表混了基金概況/期貨/股票好幾個區塊，用來驗證解析邏輯
    真的有挑對「股票」那一段，不是隨便抓第一張表就結束。
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([f"資料日期：{date_label}"])
    ws.append([])
    ws.append(["基金資產"])
    ws.append(["淨資產", "NTD 1"])
    ws.append([])
    ws.append(["項目", "金額", "權重"])
    ws.append(["期貨(名目本金)", "NTD 1", "1%"])
    ws.append([])
    ws.append(["期貨(名目本金)"])
    ws.append(["期貨代號", "期貨名稱", "持股權重", "口數", "契約年月"])
    ws.append(["TX", "台指期貨", "1%", "1", "2026/08"])
    ws.append([])
    ws.append(["股票"])
    ws.append(["股票代號", "股票名稱", "股數", "持股權重"])
    for row in stock_rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _fake_text_response(text: str):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.text = text
    return resp


def _fake_binary_response(content: bytes):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.content = content
    return resp


def _fake_session(list_html=_LIST_HTML, asset_xlsx=None):
    asset_xlsx = asset_xlsx if asset_xlsx is not None else _build_asset_xlsx()
    session = MagicMock()

    def get_side_effect(url, **kwargs):
        if url == "https://www.ezmoney.com.tw/":
            return _fake_text_response("")
        if url == "https://www.ezmoney.com.tw/ETF/Fund/Index":
            return _fake_text_response(list_html)
        if url == "https://www.ezmoney.com.tw/ETF/Fund/AssetExcelNPOI":
            return _fake_binary_response(asset_xlsx)
        raise AssertionError(f"未預期的請求網址：{url}")

    session.get.side_effect = get_side_effect
    return session


def test_fetch_holdings_resolves_fund_code_then_maps_stock_section():
    adapter = UniPcfAdapter()
    with patch("src.issuer_pcf.uni.requests.Session", return_value=_fake_session()):
        records = adapter.fetch_holdings("00981A", "2026-08-14")

    assert records == [{"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 12134000}]


def test_fetch_holdings_ignores_futures_and_fund_asset_sections():
    """基金資產/期貨區塊不能被誤認成股票清單，只有「股票」標題後面那段才算數。"""
    xlsx = _build_asset_xlsx(stock_rows=(
        ("2330", "台積電", "12,134,000", "9.30%"),
        ("2454", "聯發科", "5,448,000", "7.34%"),
    ))
    adapter = UniPcfAdapter()
    with patch("src.issuer_pcf.uni.requests.Session", return_value=_fake_session(asset_xlsx=xlsx)):
        records = adapter.fetch_holdings("00981A", "2026-08-14")

    assert records == [
        {"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 12134000},
        {"component_stock_id": "2454", "component_name": "聯發科", "holding_shares": 5448000},
    ]


def test_fetch_holdings_returns_empty_when_roc_date_mismatches_snapshot_date():
    """Excel 資料日期是民國年格式，換算成西元年後要跟查詢日期比對，對不上視為當日尚未更新。"""
    xlsx = _build_asset_xlsx(date_label="115/08/13")
    adapter = UniPcfAdapter()
    with patch("src.issuer_pcf.uni.requests.Session", return_value=_fake_session(asset_xlsx=xlsx)):
        records = adapter.fetch_holdings("00981A", "2026-08-14")

    assert records == []


def test_fetch_holdings_passes_resolved_fund_code_to_asset_endpoint():
    adapter = UniPcfAdapter()
    session = _fake_session()
    with patch("src.issuer_pcf.uni.requests.Session", return_value=session):
        adapter.fetch_holdings("00981A", "2026-08-14")

    asset_call = session.get.call_args_list[-1]
    assert asset_call.args[0] == "https://www.ezmoney.com.tw/ETF/Fund/AssetExcelNPOI"
    assert asset_call.kwargs["params"] == {"fundCode": "49YTW"}


def test_fetch_holdings_raises_when_ticker_not_found_in_list():
    adapter = UniPcfAdapter()
    with patch("src.issuer_pcf.uni.requests.Session", return_value=_fake_session(list_html="<html></html>")):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("99999", "2026-08-14")


def test_fetch_holdings_raises_when_stock_section_missing():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["資料日期：115/08/14"])
    ws.append(["期貨(名目本金)"])
    ws.append(["期貨代號", "期貨名稱"])
    ws.append(["TX", "台指期貨"])
    buf = io.BytesIO()
    wb.save(buf)

    adapter = UniPcfAdapter()
    with patch("src.issuer_pcf.uni.requests.Session", return_value=_fake_session(asset_xlsx=buf.getvalue())):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("00981A", "2026-08-14")
