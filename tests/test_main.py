from unittest.mock import MagicMock, patch

import pytest

from main import (
    _classify_rebalance_events,
    _fetch_limit_institutional_trades,
    _parse_target_date,
    _resolve_classification_tags,
    _scan_limit_up_down,
    _write_daily_report,
    main,
    run,
    run_notify_only,
    run_purge,
)
from src.fetcher import Fetcher, FinMindClient
from src.issuer_pcf.base import IssuerPcfProvider
from src.models import (
    AlertScope,
    AlertTriggerType,
    DailySnapshotMeta,
    EtfHoldingRecord,
    InstitutionalAlert,
    InstitutionalTradeRecord,
    LimitType,
    LimitUpDownRecord,
    MarketType,
    PurgeResult,
    RebalanceEvent,
    RebalanceEventType,
)
from src.storage import SnapshotRepository


class _FakeConfig:
    def __init__(self, etfs=("0050",), stocks=("2330",), concept_tags=None):
        self._etfs = etfs
        self._stocks = stocks
        self._concept_tags = concept_tags or {}

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

    @staticmethod
    def get_env(key, required=True):
        return "dummy-token"

    def get_concept_tags(self):
        return self._concept_tags


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


def test_resolve_classification_tags_covers_stock_alerts_and_rebalance_events(tmp_path):
    """需要補分類的股票代碼＝達門檻個股 + ETF 換倉成分股的聯集，重複的不會查兩次。"""
    storage = _make_repo(tmp_path)
    storage.write_industry_tags({"半導體業": {"members": [{"stock_id": "2330", "stock_name": "台積電"}]}})
    finmind = MagicMock(spec=FinMindClient)
    finmind.fetch_stock_industry.return_value = {
        "stock_id": "2603", "stock_name": "長榮", "industry_category": "航運業",
    }
    stock_alerts = [InstitutionalAlert(scope=AlertScope.STOCK, trigger_type=AlertTriggerType.VOLUME_RATIO, stock_id="2330")]
    rebalance_events = [
        RebalanceEvent("2026-08-24", "0050", "2603", "長榮", RebalanceEventType.ADDITION, 0, 1000, None)
    ]
    config = _FakeConfig(concept_tags={"IC 製造": {"members": [{"stock_id": "2330", "stock_name": "台積電"}]}})

    with patch("main.FinMindClient", return_value=finmind):
        industry_map, concept_map = _resolve_classification_tags(config, storage, stock_alerts, rebalance_events)

    assert industry_map == {"2330": "半導體業", "2603": "航運業"}
    assert concept_map == {"2330": ["IC 製造"]}
    finmind.fetch_stock_industry.assert_called_once_with("2603")  # 2330 本地已有分類，不重打 API


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
    mock_run.assert_called_once_with("2026-08-24", dry_run=True, skip_notify=False)


def test_main_routes_to_run_notify_only_when_flag_set():
    with patch("main.run_notify_only", return_value=True) as mock_run_notify_only, patch("main.run") as mock_run:
        exit_code = main(["--date", "20260824", "--notify-only", "--report-url", "https://example.com/report.md"])

    assert exit_code == 0
    mock_run_notify_only.assert_called_once_with("2026-08-24", "https://example.com/report.md", dry_run=False)
    mock_run.assert_not_called()


def test_main_passes_skip_notify_flag_through_to_run():
    with patch("main.run", return_value=True) as mock_run:
        exit_code = main(["--date", "20260824", "--skip-notify"])

    assert exit_code == 0
    mock_run.assert_called_once_with("2026-08-24", dry_run=False, skip_notify=True)


def _patch_run_dependencies():
    """run() 內部會先建立 ConfigLoader/SnapshotRepository/Fetcher，並額外呼叫漲跌停掃描／
    報告產出／FinMind 補查這些會打外部 API 的步驟，這裡全部換成假物件，讓測試只關心
    「交易日曆事前檢查」這一段短路邏輯，不用管抓取/分析/報告產出的內部細節，也不會在
    跑測試時真的打到外部服務。
    """
    return (
        patch("main.ConfigLoader"),
        patch("main.SnapshotRepository"),
        patch("main.Fetcher"),
        patch("main._evaluate_institutional_alerts", return_value=([], [], [])),
        patch("main._classify_rebalance_events", return_value=[]),
        patch("main._scan_limit_up_down", return_value=[]),
        patch("main._fetch_limit_institutional_trades", return_value={}),
        patch("main._write_daily_report"),
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
    with (
        patchers[0] as mock_config_cls,
        patchers[1] as mock_storage_cls,
        patchers[2] as mock_fetcher_cls,
        patchers[3],
        patchers[4],
        patchers[5],
        patchers[6],
        patchers[7],
    ):
        mock_config_cls.return_value.get_concept_tags.return_value = {}
        mock_storage_cls.return_value.read_industry_tags.return_value = {}
        mock_fetcher = mock_fetcher_cls.return_value
        mock_fetcher.is_known_trading_day.return_value = True
        mock_fetcher.fetch_all.return_value = MagicMock(is_trading_day=True)

        result = run("2026-08-24", dry_run=True)

    assert result is True


def test_main_routes_to_run_purge_when_purge_flag_set():
    """--purge 要完全走清除那條路，不能跟著跑抓取/分析/推播，--date 也不需要被解析。"""
    with patch("main.run_purge", return_value=True) as mock_run_purge, patch("main.run") as mock_run:
        exit_code = main(["--purge"])

    assert exit_code == 0
    mock_run_purge.assert_called_once_with(dry_run=False)
    mock_run.assert_not_called()


def test_main_passes_dry_run_through_to_run_purge():
    with patch("main.run_purge", return_value=True) as mock_run_purge:
        exit_code = main(["--purge", "--dry-run"])

    assert exit_code == 0
    mock_run_purge.assert_called_once_with(dry_run=True)


def test_main_ignores_date_when_purge_flag_set():
    """--purge --date 併用時，日期驗證完全不該被觸發（清除跟 --date 語意無關）。"""
    with patch("main.run_purge", return_value=True), patch("main._parse_target_date") as mock_parse_date:
        exit_code = main(["--purge", "--date", "not-a-real-date"])

    assert exit_code == 0
    mock_parse_date.assert_not_called()


def test_main_returns_error_when_run_purge_fails():
    with patch("main.run_purge", return_value=False):
        exit_code = main(["--purge"])

    assert exit_code == 1


def test_run_purge_reads_retention_days_and_purges_as_of_today():
    with patch("main.ConfigLoader") as mock_config_cls, patch("main.SnapshotRepository") as mock_storage_cls:
        mock_config = mock_config_cls.return_value
        mock_config.get_snapshot_retention_days.return_value = 180
        mock_storage = mock_storage_cls.return_value
        mock_storage.purge_expired.return_value = PurgeResult(
            cutoff_date="2026-02-25", deleted=["data/snapshots/2025-01-01"],
            skipped_invalid_format=[], failed=[],
        )

        result = run_purge(dry_run=False)

    assert result is True
    mock_storage.purge_expired.assert_called_once()
    call_kwargs = mock_storage.purge_expired.call_args
    assert call_kwargs.args[0] == 180  # retention_days 來自設定檔
    assert call_kwargs.kwargs["dry_run"] is False


def test_run_purge_returns_false_when_any_deletion_failed():
    with patch("main.ConfigLoader") as mock_config_cls, patch("main.SnapshotRepository") as mock_storage_cls:
        mock_config_cls.return_value.get_snapshot_retention_days.return_value = 365
        mock_storage_cls.return_value.purge_expired.return_value = PurgeResult(
            cutoff_date="2025-08-24", deleted=[], skipped_invalid_format=[],
            failed=[("data/snapshots/2025-01-01", "permission denied")],
        )

        result = run_purge(dry_run=False)

    assert result is False


def test_run_purge_returns_false_on_config_error():
    from src.config import ConfigError

    with patch("main.ConfigLoader", side_effect=ConfigError("設定檔不存在")):
        result = run_purge(dry_run=False)

    assert result is False


def test_scan_limit_up_down_writes_records_and_returns_them(tmp_path):
    storage = _make_repo(tmp_path)
    record = LimitUpDownRecord("2026-08-24", "1101", "台泥", MarketType.TWSE, LimitType.UP, 110.0, 100.0, 10.0)
    with patch("main.LimitScanner") as mock_scanner_cls:
        mock_scanner_cls.return_value.scan.return_value = [record]

        records = _scan_limit_up_down(storage, "2026-08-24")

    assert records == [record]
    assert storage.read_limit_up_down("2026-08-24")[0]["stock_id"] == "1101"


def test_fetch_limit_institutional_trades_reuses_watchlist_data_without_calling_finmind(tmp_path):
    """漲跌停股如果本來就在 watchlist 裡、今天已經抓過三大法人資料，不該重打一次 API。"""
    storage = _make_repo(tmp_path)
    storage.write_institutional_trades("2026-08-24", [
        InstitutionalTradeRecord("2026-08-24", "1101", "台泥", 0, 0, 0, 0, 0, 0, 0, 999)
    ])
    config = _FakeConfig()
    records = [LimitUpDownRecord("2026-08-24", "1101", "台泥", MarketType.TWSE, LimitType.UP, 110.0, 100.0, 10.0)]

    with patch("main.FinMindClient") as mock_finmind_cls:
        trades = _fetch_limit_institutional_trades(config, storage, "2026-08-24", records)

    assert trades["1101"]["total_net"] == 999
    mock_finmind_cls.assert_not_called()


def test_fetch_limit_institutional_trades_queries_finmind_for_stocks_outside_watchlist(tmp_path):
    storage = _make_repo(tmp_path)
    config = _FakeConfig()
    records = [LimitUpDownRecord("2026-08-24", "6789", "上櫃甲", MarketType.TPEX, LimitType.UP, 55.0, 50.0, 10.0)]
    finmind = MagicMock(spec=FinMindClient)
    finmind.fetch_institutional_trades.return_value = [{"stock_id": "6789", "total_net": 100}]

    with patch("main.FinMindClient", return_value=finmind):
        trades = _fetch_limit_institutional_trades(config, storage, "2026-08-24", records)

    assert trades["6789"]["total_net"] == 100
    finmind.fetch_institutional_trades.assert_called_once_with("2026-08-24", ["6789"])


def test_fetch_limit_institutional_trades_returns_empty_when_finmind_query_fails(tmp_path):
    """補查失敗不能讓整個報告產出流程炸掉，該股票在報告中顯示查無資料即可。"""
    storage = _make_repo(tmp_path)
    config = _FakeConfig()
    records = [LimitUpDownRecord("2026-08-24", "6789", "上櫃甲", MarketType.TPEX, LimitType.UP, 55.0, 50.0, 10.0)]
    finmind = MagicMock(spec=FinMindClient)
    finmind.fetch_institutional_trades.side_effect = RuntimeError("boom")

    with patch("main.FinMindClient", return_value=finmind):
        trades = _fetch_limit_institutional_trades(config, storage, "2026-08-24", records)

    assert trades == {}


def test_write_daily_report_writes_generated_markdown_to_storage(tmp_path):
    storage = _make_repo(tmp_path)
    with patch("main.ReportGenerator") as mock_generator_cls:
        mock_generator_cls.return_value.generate.return_value = "# 報告內容"

        _write_daily_report(storage, "2026-08-24", [], [], [], {}, [], {}, {})

    path = tmp_path / "data" / "reports" / "2026-08-24" / "daily_report.md"
    assert path.read_text(encoding="utf-8") == "# 報告內容"


def test_write_daily_report_does_not_raise_when_generation_fails(tmp_path):
    """報告產出失敗不能擋住後續推播；本測試只驗證呼叫端不會被拋出的例外中斷。"""
    storage = _make_repo(tmp_path)
    with patch("main.ReportGenerator") as mock_generator_cls:
        mock_generator_cls.return_value.generate.side_effect = RuntimeError("boom")

        _write_daily_report(storage, "2026-08-24", [], [], [], {}, [], {}, {})  # 不應拋出例外


def test_run_notify_only_returns_false_when_no_prior_analysis_exists(tmp_path):
    """--skip-notify 都沒跑過就直接 --notify-only，兩份分析結果都會是空的，
    不該假裝成功推播一份空白訊息出去。
    """
    with patch("main.ConfigLoader"), patch("main.SnapshotRepository") as mock_storage_cls:
        mock_storage = mock_storage_cls.return_value
        mock_storage.read_institutional_trades.return_value = []
        mock_storage.read_institutional_alerts.return_value = []

        result = run_notify_only("2026-08-24", None, dry_run=False)

    assert result is False


def test_run_notify_only_splits_alerts_by_scope_and_notifies_without_link(tmp_path):
    market_alert = InstitutionalAlert(scope=AlertScope.MARKET, trigger_type=AlertTriggerType.MARKET_FOREIGN, estimated_amount=1)
    stock_alert = InstitutionalAlert(scope=AlertScope.STOCK, trigger_type=AlertTriggerType.VOLUME_RATIO, stock_id="2330")

    with (
        patch("main.ConfigLoader"),
        patch("main.SnapshotRepository") as mock_storage_cls,
        patch("main._resolve_classification_tags", return_value=({}, {})),
        patch("main.Notifier") as mock_notifier_cls,
    ):
        mock_storage = mock_storage_cls.return_value
        mock_storage.read_institutional_trades.return_value = [{"stock_id": "2330"}]
        mock_storage.read_institutional_alerts.return_value = [market_alert, stock_alert]
        mock_storage.read_rebalance_events.return_value = []
        mock_notifier_cls.return_value.notify.return_value = True

        result = run_notify_only("2026-08-24", None, dry_run=False)

    assert result is True
    call_args = mock_notifier_cls.return_value.notify.call_args
    assert call_args.args[1] == [market_alert]
    assert call_args.args[2] == [stock_alert]
    assert call_args.args[-1] is None  # 沒帶 --report-url 時 report_link 為 None


def test_run_notify_only_shortens_report_url_before_notifying(tmp_path):
    with (
        patch("main.ConfigLoader"),
        patch("main.SnapshotRepository") as mock_storage_cls,
        patch("main._resolve_classification_tags", return_value=({}, {})),
        patch("main.LinkPublisher") as mock_publisher_cls,
        patch("main.Notifier") as mock_notifier_cls,
    ):
        mock_storage = mock_storage_cls.return_value
        mock_storage.read_institutional_trades.return_value = [{"stock_id": "2330"}]
        mock_storage.read_institutional_alerts.return_value = []
        mock_storage.read_rebalance_events.return_value = []
        mock_publisher_cls.return_value.shorten.return_value = "https://tinyurl.com/abc"
        mock_notifier_cls.return_value.notify.return_value = True

        run_notify_only("2026-08-24", "https://github.com/example/daily_report.md", dry_run=False)

    mock_publisher_cls.return_value.shorten.assert_called_once_with("https://github.com/example/daily_report.md")
    assert mock_notifier_cls.return_value.notify.call_args.args[-1] == "https://tinyurl.com/abc"


def test_run_notify_only_dry_run_prints_without_calling_notifier(tmp_path):
    with (
        patch("main.ConfigLoader"),
        patch("main.SnapshotRepository") as mock_storage_cls,
        patch("main._resolve_classification_tags", return_value=({}, {})),
        patch("main.Notifier") as mock_notifier_cls,
    ):
        mock_storage = mock_storage_cls.return_value
        mock_storage.read_institutional_trades.return_value = [{"stock_id": "2330"}]
        mock_storage.read_institutional_alerts.return_value = []
        mock_storage.read_rebalance_events.return_value = []

        result = run_notify_only("2026-08-24", None, dry_run=True)

    assert result is True
    mock_notifier_cls.return_value.notify.assert_not_called()


def test_run_notify_only_returns_false_on_config_error():
    from src.config import ConfigError

    with patch("main.ConfigLoader", side_effect=ConfigError("設定檔不存在")):
        result = run_notify_only("2026-08-24", None, dry_run=False)

    assert result is False
