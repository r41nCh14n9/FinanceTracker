from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.issuer_pcf.kgi import KgiPcfAdapter

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "kgi_detail_j023.html"

_NO_STOCK_SECTION_HTML = """
<html><body>
<div class="fund-asset__title index-title">持股比重</div>
<p class="fund-asset__date">(2026/08/24)</p>
<h4 class="fund-asset__sub-title">淨值</h4>
<table><tbody><tr name="content"><td>2026/08/24</td></tr></tbody></table>
</body></html>
"""

_NO_HOLDINGS_DATE_HTML = """
<html><body>
<h4 class="fund-asset__sub-title">股票</h4>
<table><tbody>
<tr name="content"><td>2330</td><td>台積電</td><td>1,000</td><td>10.0</td></tr>
</tbody></table>
</body></html>
"""

_STALE_HOLDINGS_DATE_HTML = """
<html><body>
<div class="fund-asset__title index-title">持股比重</div>
<p class="fund-asset__date">(2026/08/21)</p>
<h4 class="fund-asset__sub-title">股票</h4>
<table><tbody>
<tr name="content"><td>2330</td><td>台積電</td><td>1,000</td><td>10.0</td></tr>
</tbody></table>
</body></html>
"""


def _fake_response(html: str):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.text = html
    return resp


def test_fetch_holdings_resolves_fund_id_and_decodes_entities():
    html = _FIXTURE_PATH.read_text(encoding="utf-8")
    adapter = KgiPcfAdapter()

    with patch("src.issuer_pcf.kgi.requests.get", return_value=_fake_response(html)) as mock_get:
        records = adapter.fetch_holdings("009816", "2026-08-24")

    # fixture 桌面版/行動版兩個區塊各放了一份一模一樣的 3 檔持股，去重後應只剩 3 筆，
    # 且 HTML 數值字元參照（&#x53F0;&#x7A4D;&#x96FB;）要正確還原成中文
    assert records == [
        {"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 31956000},
        {"component_stock_id": "2454", "component_name": "聯發科", "holding_shares": 2600000},
        {"component_stock_id": "2308", "component_name": "台達電", "holding_shares": 3600000},
    ]
    requested_url = mock_get.call_args.args[0]
    assert requested_url == "https://www.kgifund.com.tw/Fund/Detail?fundID=J023"


def test_fetch_holdings_resolves_second_registered_ticker_to_its_own_fund_id():
    # 復用同一份 fixture：解析邏輯跟股票代碼無關，這裡只驗證第二檔（00407A）能查到
    # 正確的內部代碼 J024，不會誤用 009816 的 J023
    html = _FIXTURE_PATH.read_text(encoding="utf-8")
    adapter = KgiPcfAdapter()

    with patch("src.issuer_pcf.kgi.requests.get", return_value=_fake_response(html)) as mock_get:
        adapter.fetch_holdings("00407A", "2026-08-24")

    requested_url = mock_get.call_args.args[0]
    assert requested_url == "https://www.kgifund.com.tw/Fund/Detail?fundID=J024"


def test_fetch_holdings_raises_when_ticker_not_in_internal_code_table():
    adapter = KgiPcfAdapter()

    with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
        adapter.fetch_holdings("00999Z", "2026-08-24")


def test_fetch_holdings_raises_when_stock_section_missing():
    adapter = KgiPcfAdapter()

    with patch("src.issuer_pcf.kgi.requests.get", return_value=_fake_response(_NO_STOCK_SECTION_HTML)):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("009816", "2026-08-24")


def test_fetch_holdings_returns_empty_when_holdings_date_mismatches_snapshot_date():
    """「持股比重」標題下的日期跟查詢日期對不上時，視為當日尚未更新，不採用這批資料。"""
    adapter = KgiPcfAdapter()

    with patch("src.issuer_pcf.kgi.requests.get", return_value=_fake_response(_STALE_HOLDINGS_DATE_HTML)):
        records = adapter.fetch_holdings("009816", "2026-08-24")

    assert records == []


def test_fetch_holdings_returns_empty_when_holdings_date_section_missing():
    """頁面連「持股比重」日期區塊都找不到時，沒有辦法確認資料新鮮度，一律視為當日尚未
    更新，不採用；跟「股票」表格本身缺失（結構異常，直接報錯）是不同情況，不能混為一談。
    """
    adapter = KgiPcfAdapter()

    with patch("src.issuer_pcf.kgi.requests.get", return_value=_fake_response(_NO_HOLDINGS_DATE_HTML)):
        records = adapter.fetch_holdings("009816", "2026-08-24")

    assert records == []
