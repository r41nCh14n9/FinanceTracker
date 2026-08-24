from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.issuer_pcf.kgi import KgiPcfAdapter

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "kgi_detail_j023.html"

_NO_STOCK_SECTION_HTML = """
<html><body>
<h4 class="fund-asset__sub-title">淨值</h4>
<table><tbody><tr name="content"><td>2026/08/24</td></tr></tbody></table>
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


def test_fetch_holdings_raises_when_ticker_not_in_internal_code_table():
    adapter = KgiPcfAdapter()

    with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
        adapter.fetch_holdings("00999Z", "2026-08-24")


def test_fetch_holdings_raises_when_stock_section_missing():
    adapter = KgiPcfAdapter()

    with patch("src.issuer_pcf.kgi.requests.get", return_value=_fake_response(_NO_STOCK_SECTION_HTML)):
        with pytest.raises(RuntimeError, match="FETCH_ISSUER_PCF_PARSE_ERROR"):
            adapter.fetch_holdings("009816", "2026-08-24")
