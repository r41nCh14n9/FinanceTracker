import json

from src.models import (
    BrokerTradeRecord,
    DailySnapshotMeta,
    EtfHoldingRecord,
    NotificationLogEntry,
    SendStatus,
    SnapshotStatus,
    SourceStatus,
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
