"""設定檔裡 adapter 欄位的字串鍵對應到實際的 Adapter 類別，Fetcher 靠這張表決定要
用哪一個 Adapter 去抓某檔 ETF，新增投信只要在這裡多登記一筆就好。
"""
from __future__ import annotations

from src.issuer_pcf.base import IssuerPcfProvider
from src.issuer_pcf.fubon import FubonPcfAdapter
from src.issuer_pcf.yuanta import YuantaPcfAdapter

ADAPTER_REGISTRY: dict[str, type[IssuerPcfProvider]] = {
    "YuantaPcfAdapter": YuantaPcfAdapter,
    "FubonPcfAdapter": FubonPcfAdapter,
}
