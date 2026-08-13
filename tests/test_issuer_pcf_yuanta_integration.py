"""端對端驗證：實際呼叫 node 執行 extract_nuxt_state.js 去解析一份真實下載存檔的
元大 PCF 頁面，確保子行程解析這件事本身是真的可行，不是只有 mock 測試「看起來」會動。
本機沒有 node 的話直接跳過，不擋掉其他測試。
"""
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.issuer_pcf.yuanta import YuantaPcfAdapter

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "yuanta_pcf_0050.html"


@pytest.mark.skipif(shutil.which("node") is None, reason="本機找不到 node，跳過端對端解析驗證")
def test_yuanta_adapter_parses_real_fixture_html():
    html = _FIXTURE_PATH.read_text(encoding="utf-8")
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.text = html

    adapter = YuantaPcfAdapter()
    with patch("src.issuer_pcf.yuanta.requests.get", return_value=resp):
        records = adapter.fetch_holdings("0050", "2026-08-12")

    assert len(records) == 50
    assert all(
        {"component_stock_id", "component_name", "holding_shares"} <= r.keys()
        for r in records
    )
    stock_ids = {r["component_stock_id"] for r in records}
    assert "2330" in stock_ids
