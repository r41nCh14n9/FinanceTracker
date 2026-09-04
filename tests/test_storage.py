import json
import shutil
from datetime import date
from unittest.mock import patch

from src.models import (
    AlertScope,
    AlertTriggerType,
    BrokerTradeRecord,
    DailySnapshotMeta,
    EtfHoldingRecord,
    InstitutionalAlert,
    InstitutionalTradeRecord,
    LimitType,
    LimitUpDownRecord,
    MarketCapTier,
    MarketInstitutionalRecord,
    MarketType,
    NotificationLogEntry,
    RebalanceEvent,
    RebalanceEventType,
    SendStatus,
    SnapshotStatus,
    SourceStatus,
    StockCapitalSnapshot,
    StockDailyTrading,
)
from src.storage import SnapshotRepository


def _make_repo(tmp_path):
    return SnapshotRepository(data_dir=tmp_path / "data")


def test_write_and_read_meta_roundtrip(tmp_path):
    repo = _make_repo(tmp_path)
    meta = DailySnapshotMeta(
        snapshot_date="2026-07-29",
        sources={
            "FINMIND": SourceStatus(status=SnapshotStatus.OK, fetched_at="2026-07-29T10:00:00+00:00"),
            "TWSE_PCF": SourceStatus(status=SnapshotStatus.NO_DATA),
        },
        is_trading_day=True,
    )
    repo.write_meta(meta)

    loaded = repo.read_meta("2026-07-29")
    assert loaded["is_trading_day"] is True
    assert loaded["sources"]["FINMIND"]["status"] == "OK"
    assert loaded["sources"]["TWSE_PCF"]["status"] == "NO_DATA"


def test_upsert_meta_source_merges_without_overwriting_other_sources(tmp_path):
    repo = _make_repo(tmp_path)
    repo.write_meta(DailySnapshotMeta(
        snapshot_date="2026-07-29",
        sources={"FINMIND_INSTITUTIONAL": SourceStatus(status=SnapshotStatus.OK)},
        is_trading_day=False,
    ))

    repo.upsert_meta_source(
        "2026-07-29", "ISSUER_PCF",
        SourceStatus(status=SnapshotStatus.OK, fetched_at="2026-07-29T10:00:00+00:00"),
        is_trading_day=True,
    )

    loaded = repo.read_meta("2026-07-29")
    assert loaded["sources"]["FINMIND_INSTITUTIONAL"]["status"] == "OK"  # 既有來源狀態不受影響
    assert loaded["sources"]["ISSUER_PCF"]["status"] == "OK"
    assert loaded["is_trading_day"] is True


def test_upsert_meta_source_does_not_downgrade_trading_day_flag(tmp_path):
    repo = _make_repo(tmp_path)
    repo.write_meta(DailySnapshotMeta("2026-07-29", {}, True))

    repo.upsert_meta_source("2026-07-29", "ISSUER_PCF", SourceStatus(status=SnapshotStatus.NO_DATA), is_trading_day=False)

    assert repo.read_meta("2026-07-29")["is_trading_day"] is True


def test_upsert_meta_source_creates_meta_when_missing(tmp_path):
    repo = _make_repo(tmp_path)

    repo.upsert_meta_source("2026-07-29", "ISSUER_PCF", SourceStatus(status=SnapshotStatus.OK), is_trading_day=True)

    loaded = repo.read_meta("2026-07-29")
    assert loaded["sources"]["ISSUER_PCF"]["status"] == "OK"
    assert loaded["is_trading_day"] is True


def test_broker_trades_roundtrip(tmp_path):
    repo = _make_repo(tmp_path)
    records = [BrokerTradeRecord("2026-07-29", "2330", "台積電", "凱基-台北", 1500, 300, 1200)]
    repo.write_broker_trades("2026-07-29", records)

    loaded = repo.read_broker_trades("2026-07-29")
    assert loaded[0]["net_volume"] == 1200


def test_etf_holdings_roundtrip(tmp_path):
    repo = _make_repo(tmp_path)
    records = [EtfHoldingRecord("2026-07-29", "0050", "2330", "台積電", 10000)]
    repo.write_etf_holdings("2026-07-29", "0050", records)

    loaded = repo.read_etf_holdings("2026-07-29", "0050")
    assert loaded[0]["holding_shares"] == 10000


def test_read_missing_files_returns_empty_defaults(tmp_path):
    repo = _make_repo(tmp_path)
    assert repo.read_broker_trades("2026-07-29") == []
    assert repo.read_etf_holdings("2026-07-29", "0050") == []
    assert repo.read_meta("2026-07-29") is None
    assert repo.read_institutional_trades("2026-07-29") == []
    assert repo.read_stock_trading("2026-07-29") == []
    assert repo.read_market_institutional("2026-07-29") is None
    assert repo.read_capital_stock_cache("2330") is None
    assert repo.read_limit_up_down("2026-07-29") == []
    assert repo.read_institutional_alerts("2026-07-29") == []
    assert repo.read_rebalance_events("2026-07-29") == []


def test_institutional_trades_roundtrip(tmp_path):
    repo = _make_repo(tmp_path)
    records = [
        InstitutionalTradeRecord(
            "2026-07-29", "2330", "台積電", 0, 5_000_000, 0, 1_000_000, 0, 0, 0, -4_000_000
        )
    ]
    repo.write_institutional_trades("2026-07-29", records)

    loaded = repo.read_institutional_trades("2026-07-29")
    assert loaded[0]["total_net"] == -4_000_000


def test_stock_trading_roundtrip(tmp_path):
    repo = _make_repo(tmp_path)
    records = [StockDailyTrading("2026-07-29", "2330", 41_000_000, 2320.0)]
    repo.write_stock_trading("2026-07-29", records)

    loaded = repo.read_stock_trading("2026-07-29")
    assert loaded[0]["close_price"] == 2320.0


def test_capital_stock_cache_roundtrip_independent_of_date(tmp_path):
    repo = _make_repo(tmp_path)
    snapshot = StockCapitalSnapshot("2330", "2026-03-31", 259_323_701_000, 25_932_370_100, "2026-08-05T10:00:00+00:00")
    repo.write_capital_stock_cache(snapshot)

    loaded = repo.read_capital_stock_cache("2330")
    assert loaded["capital_stock"] == 259_323_701_000
    assert loaded["estimated_shares"] == 25_932_370_100
    # 快取檔案路徑不含日期，寫入後不論查詢哪個 stock_id 都直接拿到同一份
    assert repo.read_capital_stock_cache("2454") is None


def test_industry_tags_roundtrip(tmp_path):
    repo = _make_repo(tmp_path)
    table = {"半導體業": {"members": [{"stock_id": "2330", "stock_name": "台積電"}], "updated_at": "2026-08-26T00:00:00+00:00"}}
    repo.write_industry_tags(table)

    loaded = repo.read_industry_tags()
    assert loaded == table


def test_read_industry_tags_returns_empty_dict_when_missing(tmp_path):
    repo = _make_repo(tmp_path)
    assert repo.read_industry_tags() == {}


def test_market_institutional_roundtrip(tmp_path):
    repo = _make_repo(tmp_path)
    record = MarketInstitutionalRecord("2026-07-29", -25_000_000_000, 1_000_000_000, -6_000_000_000)
    repo.write_market_institutional("2026-07-29", record)

    loaded = repo.read_market_institutional("2026-07-29")
    assert loaded["foreign_net_amount"] == -25_000_000_000


def test_institutional_alerts_roundtrip(tmp_path):
    repo = _make_repo(tmp_path)
    alerts = [
        InstitutionalAlert(scope=AlertScope.MARKET, trigger_type=AlertTriggerType.MARKET_FOREIGN, estimated_amount=-25_000_000_000),
        InstitutionalAlert(scope=AlertScope.STOCK, trigger_type=AlertTriggerType.VOLUME_RATIO, stock_id="2330"),
    ]
    repo.write_institutional_alerts("2026-07-29", alerts)

    path = tmp_path / "data" / "reports" / "2026-07-29" / "institutional_alerts.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(saved) == 2
    assert saved[0]["scope"] == "MARKET"
    assert saved[1]["stock_id"] == "2330"


def test_read_institutional_alerts_restores_dataclasses_with_enums(tmp_path):
    """--notify-only 讀回這份資料時要拿到真正的 InstitutionalAlert（含列舉），
    不是原始 dict，才能直接餵給 MessageFormatter 重新組版，不用重跑一次門檻判斷。
    """
    repo = _make_repo(tmp_path)
    alerts = [
        InstitutionalAlert(scope=AlertScope.MARKET, trigger_type=AlertTriggerType.MARKET_FOREIGN, estimated_amount=-25_000_000_000),
        InstitutionalAlert(
            scope=AlertScope.STOCK, trigger_type=AlertTriggerType.VOLUME_AND_AMOUNT, stock_id="2330",
            estimated_amount=-800_000_000, market_cap_tier=MarketCapTier.LARGE, volume_ratio_pct=18.7,
        ),
    ]
    repo.write_institutional_alerts("2026-07-29", alerts)

    loaded = repo.read_institutional_alerts("2026-07-29")

    assert loaded == alerts


def test_limit_up_down_roundtrip(tmp_path):
    repo = _make_repo(tmp_path)
    records = [
        LimitUpDownRecord("2026-08-31", "1101", "台泥", MarketType.TWSE, LimitType.UP, 110.0, 100.0, 10.0),
        LimitUpDownRecord("2026-08-31", "6789", "上櫃甲", MarketType.TPEX, LimitType.DOWN, 45.0, 50.0, -10.0),
    ]
    repo.write_limit_up_down("2026-08-31", records)

    loaded = repo.read_limit_up_down("2026-08-31")
    assert [r["stock_id"] for r in loaded] == ["1101", "6789"]
    assert loaded[0]["market"] == "TWSE"
    assert loaded[1]["limit_type"] == "DOWN"


def test_daily_report_md_write_then_read_back_from_disk(tmp_path):
    """daily_report.md 是純文字檔，沒有對應的 read 方法（呼叫端不需要讀回，短網址
    只是把路徑組成 GitHub 網址），這裡直接驗證檔案內容確實落地在預期路徑。
    """
    repo = _make_repo(tmp_path)
    repo.write_daily_report_md("2026-08-31", "# 籌碼監控完整日報 2026-08-31\n\n內容")

    path = tmp_path / "data" / "reports" / "2026-08-31" / "daily_report.md"
    assert path.read_text(encoding="utf-8") == "# 籌碼監控完整日報 2026-08-31\n\n內容"


def test_read_rebalance_events_restores_dataclasses_with_enum(tmp_path):
    """--notify-only 讀回這份資料時要拿到真正的 RebalanceEvent（含 event_type 列舉），
    不是原始 dict，才能直接餵給 MessageFormatter 重新組版，不用重跑一次換倉比對。
    """
    repo = _make_repo(tmp_path)
    events = [
        RebalanceEvent("2026-07-29", "0050", "3231", "緯創", RebalanceEventType.ADDITION, 0, 520, None),
        RebalanceEvent("2026-07-29", "0050", "2317", "鴻海", RebalanceEventType.REBALANCE, 1000, 1150, 15.0),
    ]
    repo.write_rebalance_events("2026-07-29", events)

    loaded = repo.read_rebalance_events("2026-07-29")

    assert loaded == events


def test_append_notification_log_accumulates_entries(tmp_path):
    repo = _make_repo(tmp_path)
    entry1 = NotificationLogEntry("2026-07-29T18:00:00+00:00", "U1", "訊息一", SendStatus.SUCCESS, 0)
    entry2 = NotificationLogEntry("2026-07-29T18:00:05+00:00", "U2", "訊息一", SendStatus.FAILED, 3, "timeout")
    repo.append_notification_log("2026-07-29", entry1)
    repo.append_notification_log("2026-07-29", entry2)

    path = tmp_path / "data" / "reports" / "2026-07-29" / "notification_log.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(saved) == 2
    assert saved[1]["send_status"] == "FAILED"
    assert saved[1]["error_message"] == "timeout"


def test_find_previous_trading_day_skips_non_trading_days(tmp_path):
    repo = _make_repo(tmp_path)
    repo.write_meta(DailySnapshotMeta("2026-07-27", {"FINMIND": SourceStatus(SnapshotStatus.OK)}, True))
    repo.write_meta(DailySnapshotMeta("2026-07-28", {"FINMIND": SourceStatus(SnapshotStatus.NO_DATA)}, False))

    assert repo.find_previous_trading_day("2026-07-29") == "2026-07-27"


def test_find_previous_trading_day_returns_none_when_no_history(tmp_path):
    repo = _make_repo(tmp_path)
    assert repo.find_previous_trading_day("2026-07-29") is None


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")


def test_purge_expired_removes_only_directories_older_than_retention(tmp_path):
    repo = _make_repo(tmp_path)
    _touch(tmp_path / "data" / "snapshots" / "2025-01-01" / "_meta.json")  # 超出範圍
    _touch(tmp_path / "data" / "snapshots" / "2026-08-01" / "_meta.json")  # 仍在範圍內
    _touch(tmp_path / "data" / "reports" / "2025-01-01" / "rebalance_events.json")  # 超出範圍
    _touch(tmp_path / "data" / "reports" / "2026-08-01" / "rebalance_events.json")  # 仍在範圍內

    result = repo.purge_expired(retention_days=365, as_of_date=date(2026, 8, 24))

    assert not (tmp_path / "data" / "snapshots" / "2025-01-01").exists()
    assert (tmp_path / "data" / "snapshots" / "2026-08-01").exists()
    assert not (tmp_path / "data" / "reports" / "2025-01-01").exists()
    assert (tmp_path / "data" / "reports" / "2026-08-01").exists()
    assert len(result.deleted) == 2
    assert result.failed == []


def test_purge_expired_keeps_directory_exactly_at_cutoff(tmp_path):
    """截止日當天本身仍算保留範圍內，只有嚴格早於截止日才刪除。"""
    repo = _make_repo(tmp_path)
    cutoff_dir = tmp_path / "data" / "snapshots" / "2025-08-24"
    _touch(cutoff_dir / "_meta.json")

    result = repo.purge_expired(retention_days=365, as_of_date=date(2026, 8, 24))

    assert cutoff_dir.exists()
    assert result.deleted == []


def test_purge_expired_ignores_non_date_directories(tmp_path):
    """名稱不是合法 YYYY-MM-DD 的目錄一律略過，不猜測、不嘗試處理。"""
    repo = _make_repo(tmp_path)
    junk_dir = tmp_path / "data" / "snapshots" / "2026"
    _touch(junk_dir / "08" / "24" / "_meta.json")
    bogus_date_dir = tmp_path / "data" / "snapshots" / "9999-99-99"
    _touch(bogus_date_dir / "_meta.json")

    result = repo.purge_expired(retention_days=365, as_of_date=date(2026, 8, 24))

    assert junk_dir.exists()
    assert bogus_date_dir.exists()
    assert str(junk_dir) in result.skipped_invalid_format
    assert str(bogus_date_dir) in result.skipped_invalid_format
    assert result.deleted == []


def test_purge_expired_dry_run_does_not_delete(tmp_path):
    repo = _make_repo(tmp_path)
    expired_dir = tmp_path / "data" / "snapshots" / "2025-01-01"
    _touch(expired_dir / "_meta.json")

    result = repo.purge_expired(retention_days=365, as_of_date=date(2026, 8, 24), dry_run=True)

    assert expired_dir.exists()  # dry-run 不能真的刪
    assert str(expired_dir) in result.deleted  # 但要回報「本次會清除」


def test_purge_expired_does_not_touch_reference_dir(tmp_path):
    repo = _make_repo(tmp_path)
    reference_file = tmp_path / "data" / "reference" / "capital_stock" / "2330.json"
    _touch(reference_file)
    _touch(tmp_path / "data" / "snapshots" / "2025-01-01" / "_meta.json")

    repo.purge_expired(retention_days=365, as_of_date=date(2026, 8, 24))

    assert reference_file.exists()


def test_purge_expired_continues_after_single_directory_failure(tmp_path):
    """其中一個目錄刪除失敗，不能擋住其餘應清除目錄的處理。"""
    repo = _make_repo(tmp_path)
    broken_dir = tmp_path / "data" / "snapshots" / "2025-01-01"
    ok_dir = tmp_path / "data" / "snapshots" / "2025-02-01"
    _touch(broken_dir / "_meta.json")
    _touch(ok_dir / "_meta.json")

    real_rmtree = shutil.rmtree

    def fake_rmtree(path):
        if str(path) == str(broken_dir):
            raise OSError("simulated permission error")
        real_rmtree(path)

    with patch("src.storage.shutil.rmtree", side_effect=fake_rmtree):
        result = repo.purge_expired(retention_days=365, as_of_date=date(2026, 8, 24))

    assert broken_dir.exists()  # 刪除失敗，維持原樣
    assert not ok_dir.exists()  # 其餘目錄仍正常刪除
    assert str(ok_dir) in result.deleted
    assert any(path == str(broken_dir) for path, _error in result.failed)
