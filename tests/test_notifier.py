from unittest.mock import MagicMock

from src.models import RebalanceEvent, RebalanceEventType
from src.notifier import MessageFormatter, Notifier
from src.storage import SnapshotRepository


def test_message_formatter_includes_broker_and_etf_sections():
    formatter = MessageFormatter()
    trades = [{"stock_id": "2330", "stock_name": "台積電", "broker_name": "凱基-台北", "net_volume": 1203}]
    events = [
        RebalanceEvent("2026-07-29", "0050", "3231", "緯創", RebalanceEventType.ADDITION, 0, 520, None),
        RebalanceEvent("2026-07-29", "0050", "2408", "南亞科", RebalanceEventType.DELETION, 800, 0, None),
        RebalanceEvent("2026-07-29", "0050", "2317", "鴻海", RebalanceEventType.REBALANCE, 1000, 1150, 15.0),
    ]
    message = formatter.format("2026-07-29", trades, events, threshold=500)

    assert "2330 台積電  凱基-台北  買超 1,203 張" in message
    assert "新建倉：3231 緯創" in message
    assert "完全清倉：2408 南亞科" in message
    assert "調倉加碼：2317 鴻海" in message


def test_message_formatter_handles_no_significant_trades():
    formatter = MessageFormatter()
    message = formatter.format("2026-07-29", [], [], threshold=500)
    assert "（無達門檻標的）" in message


class _FakeConfig:
    def __init__(self, recipients):
        self._recipients = recipients

    def get_broker_net_volume_threshold(self):
        return 500

    def get_enabled_recipients(self):
        return self._recipients

    @staticmethod
    def get_env(key, required=True):
        return "dummy-token"


def test_notifier_retries_then_succeeds(tmp_path, monkeypatch):
    storage = SnapshotRepository(data_dir=tmp_path / "data")
    config = _FakeConfig([{"id": "U1", "type": "USER", "label": "", "enabled": True}])

    line_client = MagicMock()
    line_client.push.side_effect = [Exception("boom"), None]
    monkeypatch.setattr("src.notifier.time.sleep", lambda _seconds: None)

    notifier = Notifier(config, storage, line_client=line_client)
    result = notifier.notify("2026-07-29", [], [])

    assert result is True
    assert line_client.push.call_count == 2


def test_notifier_gives_up_after_max_retries(tmp_path, monkeypatch):
    storage = SnapshotRepository(data_dir=tmp_path / "data")
    config = _FakeConfig([{"id": "U1", "type": "USER", "label": "", "enabled": True}])

    line_client = MagicMock()
    line_client.push.side_effect = Exception("boom")
    monkeypatch.setattr("src.notifier.time.sleep", lambda _seconds: None)

    notifier = Notifier(config, storage, line_client=line_client)
    result = notifier.notify("2026-07-29", [], [])

    assert result is False
    assert line_client.push.call_count == 3


def test_notifier_only_pushes_to_enabled_recipients(tmp_path, monkeypatch):
    storage = SnapshotRepository(data_dir=tmp_path / "data")
    config = _FakeConfig([
        {"id": "U1", "type": "USER", "label": "", "enabled": True},
        {"id": "U2", "type": "USER", "label": "", "enabled": True},
    ])
    line_client = MagicMock()
    monkeypatch.setattr("src.notifier.time.sleep", lambda _seconds: None)

    notifier = Notifier(config, storage, line_client=line_client)
    notifier.notify("2026-07-29", [], [])

    assert line_client.push.call_count == 2
