from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.issuer_pcf.fubon import FubonPcfAdapter

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "fubon_assets_006208.html"

_NO_STOCK_SECTION_HTML = """
<html><body>
<p class="f13 txt_black_A5A5">資料日期：2026/08/11</p>
<h6>期貨</h6>
<table><tbody><tr class="title"><td>期貨代碼</td></tr></tbody></table>
</body></html>
"""

_NO_DATA_DATE_HTML = """
<html><body>
<h6>股票</h6>
<table><tbody><tr><td>2330</td><td>台積電</td><td>1,000</td></tr></tbody></table>
</body></html>
"""

_STALE_DATA_DATE_HTML = """
<html><body>
<p class="f13 txt_black_A5A5">資料日期：2026/08/08</p>
<h6>股票</h6>
<table><tbody><tr><td>2330</td><td>台積電</td><td>1,000</td></tr></tbody></table>
</body></html>
"""


def _fake_response(html: str):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.text = html
    return resp


def test_supports_backfill_is_enabled():
    assert FubonPcfAdapter.SUPPORTS_BACKFILL is True


def test_fetch_holdings_only_returns_stock_section_rows():
    html = _FIXTURE_PATH.read_text(encoding="utf-8")
    adapter = FubonPcfAdapter()

    with patch("src.issuer_pcf.fubon.requests.get", return_value=_fake_response(html)):
        records = adapter.fetch_holdings("006208", "2026-08-11")

    # fixture 保留了股票區塊 3 筆持股（台積電/聯發科/台達電），期貨與附買回債券不該混進來
    assert records == [
        {"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 108244064},
        {"component_stock_id": "2454", "component_name": "聯發科", "holding_shares": 6521042},
        {"component_stock_id": "2308", "component_name": "台達電", "holding_shares": 8553581},
    ]


def test_fetch_holdings_sends_ddate_query_param():
    html = _FIXTURE_PATH.read_text(encoding="utf-8")
    adapter = FubonPcfAdapter()

    with patch("src.issuer_pcf.fubon.requests.get", return_value=_fake_response(html)) as mock_get:
        adapter.fetch_holdings("006208", "2026-08-11")

    params = mock_get.call_args.kwargs["params"]
    assert params["stkId"] == "006208"
    assert params["ddate"] == "20260811"


def test_fetch_holdings_excludes_header_and_summary_rows():
    html = _FIXTURE_PATH.read_text(encoding="utf-8")
    adapter = FubonPcfAdapter()

    with patch("src.issuer_pcf.fubon.requests.get", return_value=_fake_response(html)):
        records = adapter.fetch_holdings("006208", "2026-08-11")

    stock_ids = [r["component_stock_id"] for r in records]
    assert "股票代碼" not in stock_ids  # 表頭列
    assert "股票合計" not in stock_ids  # 小計列
    assert len(records) == 3


def test_fetch_holdings_raises_when_stock_section_missing():
    adapter = FubonPcfAdapter()

    with patch("src.issuer_pcf.fubon.requests.get", return_value=_fake_response(_NO_STOCK_SECTION_HTML)):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("006208", "2026-08-11")


def test_fetch_holdings_returns_empty_when_data_date_mismatches_snapshot_date():
    """站方查無查詢日期當天的資料時會自動回退到最近一個交易日，「資料日期」欄位會
    誠實反映這件事；日期對不上時視為當日尚未更新，不能誤用回退回來的舊資料。
    """
    adapter = FubonPcfAdapter()

    with patch("src.issuer_pcf.fubon.requests.get", return_value=_fake_response(_STALE_DATA_DATE_HTML)):
        records = adapter.fetch_holdings("006208", "2026-08-11")

    assert records == []


def test_fetch_holdings_returns_empty_when_data_date_missing():
    """頁面連「資料日期」欄位都找不到時，沒有辦法確認資料新鮮度，一律視為當日尚未
    更新，不採用；跟「股票」表格本身缺失（結構異常，直接報錯）是不同情況。
    """
    adapter = FubonPcfAdapter()

    with patch("src.issuer_pcf.fubon.requests.get", return_value=_fake_response(_NO_DATA_DATE_HTML)):
        records = adapter.fetch_holdings("006208", "2026-08-11")

    assert records == []
