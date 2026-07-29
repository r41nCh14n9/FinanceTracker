from src.analyzer import BrokerFilter, RebalanceClassifier
from src.models import RebalanceEventType


class _FakeConfig:
    def __init__(self, broker_threshold=500, etf_threshold=10.0, overrides=None):
        self._broker_threshold = broker_threshold
        self._etf_threshold = etf_threshold
        self._overrides = overrides or {}

    def get_broker_net_volume_threshold(self):
        return self._broker_threshold

    def get_etf_rebalance_pct_threshold(self, etf_id):
        return self._overrides.get(etf_id, self._etf_threshold)


def test_broker_filter_keeps_only_trades_above_threshold():
    trades = [{"net_volume": 600}, {"net_volume": -700}, {"net_volume": 100}]
    result = BrokerFilter(_FakeConfig(broker_threshold=500)).filter_significant_trades(trades)
    assert [t["net_volume"] for t in result] == [600, -700]


def test_rebalance_classifier_detects_addition():
    classifier = RebalanceClassifier(_FakeConfig())
    events = classifier.classify(
        "0050", "2026-07-29",
        prev_holdings=[],
        curr_holdings=[{"component_stock_id": "3231", "component_name": "緯創", "holding_shares": 500}],
    )
    assert len(events) == 1
    assert events[0].event_type == RebalanceEventType.ADDITION
    assert events[0].curr_shares == 500


def test_rebalance_classifier_detects_deletion():
    classifier = RebalanceClassifier(_FakeConfig())
    events = classifier.classify(
        "0050", "2026-07-29",
        prev_holdings=[{"component_stock_id": "2408", "component_name": "南亞科", "holding_shares": 1000}],
        curr_holdings=[],
    )
    assert events[0].event_type == RebalanceEventType.DELETION
    assert events[0].curr_shares == 0


def test_rebalance_classifier_detects_rebalance_above_threshold():
    classifier = RebalanceClassifier(_FakeConfig(etf_threshold=10.0))
    events = classifier.classify(
        "0050", "2026-07-29",
        prev_holdings=[{"component_stock_id": "2317", "component_name": "鴻海", "holding_shares": 1000}],
        curr_holdings=[{"component_stock_id": "2317", "component_name": "鴻海", "holding_shares": 1200}],
    )
    assert events[0].event_type == RebalanceEventType.REBALANCE
    assert events[0].change_pct == 20.0


def test_rebalance_classifier_skips_change_below_threshold():
    classifier = RebalanceClassifier(_FakeConfig(etf_threshold=10.0))
    events = classifier.classify(
        "0050", "2026-07-29",
        prev_holdings=[{"component_stock_id": "2317", "component_name": "鴻海", "holding_shares": 1000}],
        curr_holdings=[{"component_stock_id": "2317", "component_name": "鴻海", "holding_shares": 1030}],
    )
    assert events == []


def test_rebalance_classifier_uses_per_etf_override():
    classifier = RebalanceClassifier(_FakeConfig(etf_threshold=10.0, overrides={"0050": 50.0}))
    events = classifier.classify(
        "0050", "2026-07-29",
        prev_holdings=[{"component_stock_id": "2317", "component_name": "鴻海", "holding_shares": 1000}],
        curr_holdings=[{"component_stock_id": "2317", "component_name": "鴻海", "holding_shares": 1200}],
    )
    assert events == []  # 20% 變動未達覆寫後的 50% 門檻


def test_rebalance_classifier_no_change_yields_no_events():
    classifier = RebalanceClassifier(_FakeConfig())
    events = classifier.classify(
        "0050", "2026-07-29",
        prev_holdings=[{"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 1000}],
        curr_holdings=[{"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 1000}],
    )
    assert events == []
