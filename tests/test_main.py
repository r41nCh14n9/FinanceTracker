from unittest.mock import MagicMock, patch

import pytest

from main import _classify_rebalance_events, _parse_target_date, main, run
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

    @staticmethod
    def get_issuer_name(etf_id):
        return f"測試投信（{etf_id}）"


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


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("20260824", "2026-08-24"),
        ("2026-08-24", "2026-08-24"),
        ("2026/08/24", "2026-08-24"),
    ],
)
def test_parse_target_date_accepts_supported_formats(raw, expected):
    assert _parse_target_date(raw) == expected


@pytest.mark.parametrize("raw", ["2026.08.24", "2026年08月24日", "abc", "2026-13-01", ""])
def test_parse_target_date_rejects_unsupported_formats(raw):
    """不管是分隔符號不對、日期本身不合法、還是隨便亂打的字串，都要用同一句「日期格式
    輸入錯誤」擋下來，不能讓錯的字串流進 FinMind／各投信官網才用不同方式各自炸開。
    """
    with pytest.raises(ValueError, match="日期格式輸入錯誤"):
        _parse_target_date(raw)


def test_main_aborts_before_run_when_date_format_invalid():
    """格式錯誤要在真正呼叫 run() 之前就擋下來，不能讓錯的日期字串流進抓取流程。"""
    with patch("main.run") as mock_run:
        exit_code = main(["--date", "2026/8/24/"])

    assert exit_code == 1
    mock_run.assert_not_called()


def test_main_normalizes_date_before_calling_run():
    with patch("main.run", return_value=True) as mock_run:
        exit_code = main(["--date", "20260824", "--dry-run"])

    assert exit_code == 0
    mock_run.assert_called_once_with("2026-08-24", dry_run=True)


def _patch_run_dependencies():
    """run() 內部會先建立 ConfigLoader/SnapshotRepository/Fetcher，這裡把三者換成假物件，
    讓測試只關心「交易日曆事前檢查」這一段新增的短路邏輯，不用管抓取/分析內部細節。
    """
    return (
        patch("main.ConfigLoader"),
        patch("main.SnapshotRepository"),
        patch("main.Fetcher"),
        patch("main._evaluate_institutional_alerts", return_value=([], [], [])),
        patch("main._classify_rebalance_events", return_value=[]),
    )


def test_run_skips_fetch_when_trading_day_calendar_confirms_non_trading_day():
    """非 dry-run 時，交易日曆一旦確認非交易日，就不該再呼叫 fetch_all()，省下白白抓一輪的成本。"""
    patchers = _patch_run_dependencies()
    with patchers[0], patchers[1], patchers[2] as mock_fetcher_cls, patchers[3], patchers[4]:
        mock_fetcher = mock_fetcher_cls.return_value
        mock_fetcher.is_known_trading_day.return_value = False

        result = run("2026-08-23", dry_run=False)

    assert result is True
    mock_fetcher.is_known_trading_day.assert_called_once_with("2026-08-23")
    mock_fetcher.fetch_all.assert_not_called()


def test_run_falls_back_to_fetch_all_when_trading_day_unknown():
    """交易日曆查詢失敗（回傳 None）時要退回原本「抓了才知道」的流程，不能直接當非交易日跳過。"""
    patchers = _patch_run_dependencies()
    with patchers[0], patchers[1], patchers[2] as mock_fetcher_cls, patchers[3], patchers[4]:
        mock_fetcher = mock_fetcher_cls.return_value
        mock_fetcher.is_known_trading_day.return_value = None
        mock_fetcher.fetch_all.return_value = MagicMock(is_trading_day=False)

        result = run("2026-08-23", dry_run=False)

    assert result is True
    mock_fetcher.fetch_all.assert_called_once_with("2026-08-23")


def test_run_skips_fetch_on_non_trading_day_even_in_dry_run_mode():
    """非交易日本來就不會有任何有意義的資料，不管是不是 dry-run 都一樣，不能為了「預覽」
    就放行去白白多打一輪外部 API，只為了印出同一則空洞的「無達門檻標的」。
    """
    patchers = _patch_run_dependencies()
    with patchers[0], patchers[1], patchers[2] as mock_fetcher_cls, patchers[3], patchers[4]:
        mock_fetcher = mock_fetcher_cls.return_value
        mock_fetcher.is_known_trading_day.return_value = False

        result = run("2026-08-23", dry_run=True)

    assert result is True
    mock_fetcher.fetch_all.assert_not_called()


def test_run_still_previews_normally_in_dry_run_mode_on_trading_day():
    """dry-run 的用途是「抓真資料但不推播」，交易日的預覽功能不受本次調整影響。"""
    patchers = _patch_run_dependencies()
    with patchers[0], patchers[1], patchers[2] as mock_fetcher_cls, patchers[3], patchers[4]:
        mock_fetcher = mock_fetcher_cls.return_value
        mock_fetcher.is_known_trading_day.return_value = True
        mock_fetcher.fetch_all.return_value = MagicMock(is_trading_day=True)

        result = run("2026-08-24", dry_run=True)

    assert result is True
    mock_fetcher.fetch_all.assert_called_once_with("2026-08-24")
