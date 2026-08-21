from unittest.mock import MagicMock

from main import _classify_rebalance_events
from src.fetcher import Fetcher, FinMindClient
from src.issuer_pcf.base import IssuerPcfProvider
from src.models import DailySnapshotMeta, EtfHoldingRecord
from src.storage import SnapshotRepository


class _FakeConfig:
    def __init__(self, etfs=("0050",), stocks=("2330",)):
        self._etfs = etfs
        self._stocks = stocks

    def get_watchlist_etfs(self):
        return list(self._etfs)

    def get_watchlist_stocks(self):
        return list(self._stocks)

    @staticmethod
    def get_etf_rebalance_pct_threshold(etf_id):
        return 10.0

    @staticmethod
    def get_etf_holding_count_drop_pct_threshold():
        return 50.0


def _make_repo(tmp_path):
    return SnapshotRepository(data_dir=tmp_path / "data")


def _trading_day_meta(snapshot_date):
    return DailySnapshotMeta(snapshot_date=snapshot_date, sources={}, is_trading_day=True)


def _holding(etf_id, stock_id, name, shares):
    return EtfHoldingRecord(
        snapshot_date="unused", etf_id=etf_id,
        component_stock_id=stock_id, component_name=name, holding_shares=shares,
    )


def _quiet_finmind():
    """回傳一個對 fetch_institutional_trades 一律回應「什麼都沒有」的假 FinMindClient，
    模擬本地完全無歷史快照時，逐日輕量確認交易日的探測全部落空。
    """
    finmind = MagicMock(spec=FinMindClient)
    finmind.fetch_institutional_trades.return_value = []
    return finmind


def _make_fetcher(config, storage, finmind=None, issuer_providers=None):
    return Fetcher(
        config, storage,
        finmind_client=finmind or _quiet_finmind(),
        issuer_providers=issuer_providers or {},
    )


def test_classify_rebalance_events_returns_empty_when_no_previous_trading_day(tmp_path):
    storage = _make_repo(tmp_path)
    config = _FakeConfig()
    fetcher = _make_fetcher(config, storage)

    events = _classify_rebalance_events(config, storage, fetcher, "2026-08-17")

    assert events == []


def test_classify_rebalance_events_skips_etf_when_todays_holdings_missing(tmp_path):
    """今天沒抓到這檔 ETF 的持股資料（檔案不存在）時，不能把「查無資料」誤判成「持股歸零」，
    否則既有持股全部會被誤判成清倉事件推播出去——這是本次修正的重點行為。
    """
    storage = _make_repo(tmp_path)
    storage.write_meta(_trading_day_meta("2026-08-14"))
    storage.write_etf_holdings("2026-08-14", "0050", [
        _holding("0050", "2330", "台積電", 1000),
        _holding("0050", "2454", "聯發科", 500),
    ])
    # 2026-08-17 這天沒有寫入 0050.json（模擬 Fetcher 當天沒抓到資料）
    config = _FakeConfig()
    fetcher = _make_fetcher(config, storage)

    events = _classify_rebalance_events(config, storage, fetcher, "2026-08-17")

    assert events == []


def test_classify_rebalance_events_generates_events_when_todays_holdings_present(tmp_path):
    storage = _make_repo(tmp_path)
    storage.write_meta(_trading_day_meta("2026-08-14"))
    storage.write_etf_holdings("2026-08-14", "0050", [_holding("0050", "2330", "台積電", 1000)])
    storage.write_etf_holdings("2026-08-17", "0050", [
        _holding("0050", "2330", "台積電", 1000),
        _holding("0050", "2454", "聯發科", 500),
    ])
    config = _FakeConfig()
    fetcher = _make_fetcher(config, storage)

    events = _classify_rebalance_events(config, storage, fetcher, "2026-08-17")

    assert len(events) == 1
    assert events[0].component_stock_id == "2454"


def test_classify_rebalance_events_backfills_missing_previous_day_via_supported_adapter(tmp_path):
    """本地缺前一天快照，但對應投信 SUPPORTS_BACKFILL=True 時，應即時補抓並照常產生換倉事件。"""
    storage = _make_repo(tmp_path)
    storage.write_meta(_trading_day_meta("2026-08-14"))  # 前一交易日已知，但沒有 0050 的持股快照
    storage.write_etf_holdings("2026-08-17", "0050", [
        _holding("0050", "2330", "台積電", 1000),
        _holding("0050", "2454", "聯發科", 500),
    ])
    provider = MagicMock(spec=IssuerPcfProvider)
    provider.SUPPORTS_BACKFILL = True
    provider.fetch_holdings.return_value = [
        {"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 1000},
    ]
    config = _FakeConfig()
    fetcher = _make_fetcher(config, storage, issuer_providers={"0050": provider})

    events = _classify_rebalance_events(config, storage, fetcher, "2026-08-17")

    assert len(events) == 1
    assert events[0].component_stock_id == "2454"
    provider.fetch_holdings.assert_called_once_with("0050", "2026-08-14")
    assert storage.read_etf_holdings("2026-08-14", "0050") != []  # 回補成功後應落地存檔


def test_classify_rebalance_events_skips_when_adapter_does_not_support_backfill(tmp_path):
    storage = _make_repo(tmp_path)
    storage.write_meta(_trading_day_meta("2026-08-14"))
    storage.write_etf_holdings("2026-08-17", "0050", [_holding("0050", "2330", "台積電", 1000)])
    provider = MagicMock(spec=IssuerPcfProvider)
    provider.SUPPORTS_BACKFILL = False
    config = _FakeConfig()
    fetcher = _make_fetcher(config, storage, issuer_providers={"0050": provider})

    events = _classify_rebalance_events(config, storage, fetcher, "2026-08-17")

    assert events == []
    provider.fetch_holdings.assert_not_called()
