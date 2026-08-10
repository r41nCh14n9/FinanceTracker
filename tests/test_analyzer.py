from src.analyzer import BrokerFilter, InstitutionalTieredFilter, MarketInstitutionalFilter, RebalanceClassifier
from src.models import AlertScope, AlertTriggerType, MarketCapTier, RebalanceEventType


class _FakeConfig:
    def __init__(
        self,
        broker_threshold=500,
        etf_threshold=10.0,
        overrides=None,
        volume_ratio_pct=15.0,
        tier_bounds=(100_000_000_000, 10_000_000_000),
        amount_thresholds=None,
        market_thresholds=None,
    ):
        self._broker_threshold = broker_threshold
        self._etf_threshold = etf_threshold
        self._overrides = overrides or {}
        self._volume_ratio_pct = volume_ratio_pct
        self._tier_bounds = tier_bounds
        self._amount_thresholds = amount_thresholds or {
            MarketCapTier.LARGE: 3_000_000_000,
            MarketCapTier.MID: 500_000_000,
            MarketCapTier.SMALL: 100_000_000,
        }
        self._market_thresholds = market_thresholds or {
            "foreign": 20_000_000_000,
            "trust": 3_000_000_000,
            "dealer": 5_000_000_000,
        }

    def get_broker_net_volume_threshold(self):
        return self._broker_threshold

    def get_etf_rebalance_pct_threshold(self, etf_id):
        return self._overrides.get(etf_id, self._etf_threshold)

    def get_volume_ratio_threshold(self):
        return self._volume_ratio_pct

    def get_market_cap_tier_bounds(self):
        return self._tier_bounds

    def get_tiered_amount_threshold(self, tier):
        return self._amount_thresholds[tier]

    def get_market_institutional_threshold(self, investor_type):
        return self._market_thresholds[investor_type]


class _FakeStorage:
    def __init__(self, capital_cache=None):
        self._capital_cache = capital_cache or {}

    def read_capital_stock_cache(self, stock_id):
        return self._capital_cache.get(stock_id)


def test_broker_filter_keeps_only_trades_above_threshold():
    trades = [{"net_volume": 600}, {"net_volume": -700}, {"net_volume": 100}]
    result = BrokerFilter(_FakeConfig(broker_threshold=500)).filter_significant_trades(trades)
    assert [t["net_volume"] for t in result] == [600, -700]


def _institutional_trade(stock_id="2330", total_net=0):
    return {"stock_id": stock_id, "total_net": total_net}


def _stock_trading(stock_id="2330", trading_volume=10_000_000, close_price=500.0):
    return {"stock_id": stock_id, "trading_volume": trading_volume, "close_price": close_price}


def test_institutional_tiered_filter_triggers_on_volume_ratio_only():
    # 低股價、小型股，買賣超佔成交量達 20% 但估算金額遠低於門檻2，只會觸發門檻1
    storage = _FakeStorage({"2330": {"estimated_shares": 100_000, "report_date": "2026-03-31"}})
    trade = _institutional_trade(total_net=200_000)  # 佔成交量 20%
    trading = _stock_trading(trading_volume=1_000_000, close_price=10.0)

    alerts = InstitutionalTieredFilter(_FakeConfig(), storage).filter_significant_trades([trade], [trading])

    assert len(alerts) == 1
    assert alerts[0].trigger_type == AlertTriggerType.VOLUME_RATIO
    assert alerts[0].scope == AlertScope.STOCK


def test_institutional_tiered_filter_triggers_on_tiered_amount_only():
    # 大型股（市值遠超千億），買賣超金額達 30 億但佔成交量比例很低
    storage = _FakeStorage({"2330": {"estimated_shares": 25_930_000_000, "report_date": "2026-03-31"}})
    trade = _institutional_trade(total_net=-6_000_000)  # 6,000,000 股 × 500 元 = 30 億
    trading = _stock_trading(trading_volume=100_000_000, close_price=500.0)

    alerts = InstitutionalTieredFilter(_FakeConfig(), storage).filter_significant_trades([trade], [trading])

    assert len(alerts) == 1
    assert alerts[0].trigger_type == AlertTriggerType.TIERED_AMOUNT
    assert alerts[0].market_cap_tier == MarketCapTier.LARGE


def test_institutional_tiered_filter_skips_when_neither_threshold_hit():
    storage = _FakeStorage({"2330": {"estimated_shares": 25_930_000_000, "report_date": "2026-03-31"}})
    trade = _institutional_trade(total_net=1_000)
    trading = _stock_trading(trading_volume=100_000_000, close_price=500.0)

    alerts = InstitutionalTieredFilter(_FakeConfig(), storage).filter_significant_trades([trade], [trading])
    assert alerts == []


def test_institutional_tiered_filter_skips_amount_check_when_no_capital_cache():
    storage = _FakeStorage({})  # 無股本快取
    trade = _institutional_trade(total_net=-6_000_000)
    trading = _stock_trading(trading_volume=100_000_000, close_price=500.0)

    alerts = InstitutionalTieredFilter(_FakeConfig(), storage).filter_significant_trades([trade], [trading])
    assert alerts == []  # 沒有股本快取，門檻2 一律判定不達標；門檻1 佔成交量僅 6%，也未達標


def test_institutional_tiered_filter_skips_stock_without_trading_data():
    storage = _FakeStorage()
    trade = _institutional_trade(stock_id="9999", total_net=5_000_000)
    alerts = InstitutionalTieredFilter(_FakeConfig(), storage).filter_significant_trades([trade], [])
    assert alerts == []


def test_market_institutional_filter_triggers_only_matching_investor_types():
    market_record = {
        "foreign_net_amount": -25_000_000_000,  # 達門檻
        "trust_net_amount": 1_000_000_000,  # 未達門檻
        "dealer_net_amount": -6_000_000_000,  # 達門檻
    }
    alerts = MarketInstitutionalFilter(_FakeConfig()).filter_significant_trades(market_record)

    trigger_types = {a.trigger_type for a in alerts}
    assert trigger_types == {AlertTriggerType.MARKET_FOREIGN, AlertTriggerType.MARKET_DEALER}
    assert all(a.scope == AlertScope.MARKET for a in alerts)


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
