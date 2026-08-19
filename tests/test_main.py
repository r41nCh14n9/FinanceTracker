from main import _classify_rebalance_events
from src.models import DailySnapshotMeta, EtfHoldingRecord
from src.storage import SnapshotRepository


class _FakeConfig:
    def __init__(self, etfs=("0050",)):
        self._etfs = etfs

    def get_watchlist_etfs(self):
        return list(self._etfs)

    @staticmethod
    def get_etf_rebalance_pct_threshold(etf_id):
        return 10.0


def _make_repo(tmp_path):
    return SnapshotRepository(data_dir=tmp_path / "data")


def _trading_day_meta(snapshot_date):
    return DailySnapshotMeta(snapshot_date=snapshot_date, sources={}, is_trading_day=True)


def _holding(etf_id, stock_id, name, shares):
    return EtfHoldingRecord(
        snapshot_date="unused", etf_id=etf_id,
        component_stock_id=stock_id, component_name=name, holding_shares=shares,
    )


def test_classify_rebalance_events_returns_empty_when_no_previous_trading_day(tmp_path):
    storage = _make_repo(tmp_path)
    events = _classify_rebalance_events(_FakeConfig(), storage, "2026-08-17")
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

    events = _classify_rebalance_events(_FakeConfig(), storage, "2026-08-17")

    assert events == []


def test_classify_rebalance_events_generates_events_when_todays_holdings_present(tmp_path):
    storage = _make_repo(tmp_path)
    storage.write_meta(_trading_day_meta("2026-08-14"))
    storage.write_etf_holdings("2026-08-14", "0050", [_holding("0050", "2330", "台積電", 1000)])
    storage.write_etf_holdings("2026-08-17", "0050", [
        _holding("0050", "2330", "台積電", 1000),
        _holding("0050", "2454", "聯發科", 500),
    ])

    events = _classify_rebalance_events(_FakeConfig(), storage, "2026-08-17")

    assert len(events) == 1
    assert events[0].component_stock_id == "2454"
