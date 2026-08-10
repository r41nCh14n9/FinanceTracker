from unittest.mock import MagicMock

from src.models import (
    AlertScope,
    AlertTriggerType,
    InstitutionalAlert,
    MarketCapTier,
    RebalanceEvent,
    RebalanceEventType,
)
from src.notifier import MessageFormatter, Notifier
from src.storage import SnapshotRepository


def _institutional_trade(stock_id="2330", stock_name="台積電"):
    return {
        "trade_date": "2026-08-05",
        "stock_id": stock_id,
        "stock_name": stock_name,
        "foreign_investor_buy": 0,
        "foreign_investor_sell": 5_000_000,
        "foreign_dealer_self_net": 0,
        "investment_trust_buy": 1_000_000,
        "investment_trust_sell": 0,
        "dealer_self_net": 0,
        "dealer_hedging_net": 0,
        "total_net": -4_000_000,
    }


def test_message_formatter_includes_market_stock_and_etf_sections():
    formatter = MessageFormatter()
    market_alerts = [
        InstitutionalAlert(scope=AlertScope.MARKET, trigger_type=AlertTriggerType.MARKET_FOREIGN, estimated_amount=-25_000_000_000)
    ]
    stock_alerts = [
        InstitutionalAlert(
            scope=AlertScope.STOCK,
            trigger_type=AlertTriggerType.TIERED_AMOUNT,
            stock_id="2330",
            estimated_amount=-800_000_000,
            market_cap_tier=MarketCapTier.LARGE,
            volume_ratio_pct=5.0,
        )
    ]
    institutional_trades = [_institutional_trade()]
    events = [
        RebalanceEvent("2026-08-05", "0050", "3231", "緯創", RebalanceEventType.ADDITION, 0, 520, None),
        RebalanceEvent("2026-08-05", "0050", "2408", "南亞科", RebalanceEventType.DELETION, 800, 0, None),
        RebalanceEvent("2026-08-05", "0050", "2317", "鴻海", RebalanceEventType.REBALANCE, 1000, 1150, 15.0),
    ]
    message = formatter.format("2026-08-05", market_alerts, stock_alerts, institutional_trades, events)

    assert "外資單日賣超 250.0 億元" in message
    assert "2330 台積電 [大額進出]" in message
    assert "市值分級：大型" in message
    assert "新建倉：3231 緯創" in message
    assert "完全清倉：2408 南亞科" in message
    assert "調倉加碼：2317 鴻海" in message


def test_message_formatter_handles_no_alerts():
    formatter = MessageFormatter()
    message = formatter.format("2026-08-05", [], [], [], [])
    assert "（今日大盤三大法人買賣金額均未達門檻）" in message
    assert "（無達門檻標的）" in message


def test_message_formatter_labels_volume_and_amount_trigger_together():
    formatter = MessageFormatter()
    stock_alerts = [
        InstitutionalAlert(
            scope=AlertScope.STOCK,
            trigger_type=AlertTriggerType.VOLUME_AND_AMOUNT,
            stock_id="2330",
            estimated_amount=-800_000_000,
            market_cap_tier=MarketCapTier.LARGE,
            volume_ratio_pct=20.0,
        )
    ]
    message = formatter.format("2026-08-05", [], stock_alerts, [_institutional_trade()], [])
    assert "量能異常＋大額進出" in message


class _FakeConfig:
    def __init__(self, recipients):
        self._recipients = recipients

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
    result = notifier.notify("2026-08-05", [], [], [], [])

    assert result is True
    assert line_client.push.call_count == 2


def test_notifier_gives_up_after_max_retries(tmp_path, monkeypatch):
    storage = SnapshotRepository(data_dir=tmp_path / "data")
    config = _FakeConfig([{"id": "U1", "type": "USER", "label": "", "enabled": True}])

    line_client = MagicMock()
    line_client.push.side_effect = Exception("boom")
    monkeypatch.setattr("src.notifier.time.sleep", lambda _seconds: None)

    notifier = Notifier(config, storage, line_client=line_client)
    result = notifier.notify("2026-08-05", [], [], [], [])

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
    notifier.notify("2026-08-05", [], [], [], [])

    assert line_client.push.call_count == 2
