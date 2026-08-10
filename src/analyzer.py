"""依門檻篩選三大法人買賣超（個股分級門檻＋大盤門檻），並比對 ETF 前後日持股，分類為新建倉／清倉／調倉。"""
from __future__ import annotations

from src.config import ConfigLoader
from src.models import (
    AlertScope,
    AlertTriggerType,
    InstitutionalAlert,
    MarketCapTier,
    RebalanceEvent,
    RebalanceEventType,
)
from src.storage import SnapshotRepository


class BrokerFilter:
    """分點買賣超門檻篩選，保留供分點功能日後復用；目前預設不會被呼叫。"""

    def __init__(self, config: ConfigLoader):
        self._config = config

    def filter_significant_trades(self, trades: list[dict]) -> list[dict]:
        threshold = self._config.get_broker_net_volume_threshold()
        return [t for t in trades if abs(t["net_volume"]) >= threshold]


class InstitutionalTieredFilter:
    """個股三大法人合計買賣超的雙門檻判斷：佔成交量比例、或依市值分級的估算金額，任一達標即列入報告。

    金額與市值都是估算值（股數 × 收盤價），因為 FinMind 免費層的三大法人資料只有股數沒有金額；
    市值分級要靠股本快取才能算，快取缺失時只看得到門檻1（成交量佔比），門檻2 直接判定不達標。
    """

    def __init__(self, config: ConfigLoader, storage: SnapshotRepository):
        self._config = config
        self._storage = storage

    def filter_significant_trades(
        self, institutional_trades: list[dict], stock_trading: list[dict]
    ) -> list[InstitutionalAlert]:
        trading_by_stock = {t["stock_id"]: t for t in stock_trading}
        alerts = []
        for trade in institutional_trades:
            trading = trading_by_stock.get(trade["stock_id"])
            if trading is None or not trading.get("trading_volume"):
                continue
            alert = self._evaluate_one(trade, trading)
            if alert is not None:
                alerts.append(alert)
        return alerts

    def _evaluate_one(self, trade: dict, trading: dict) -> InstitutionalAlert | None:
        total_net = trade["total_net"]
        close_price = trading["close_price"]
        volume_ratio_pct = abs(total_net) / trading["trading_volume"] * 100
        estimated_amount = int(total_net * close_price)

        tier = self._classify_tier(trade["stock_id"], close_price)
        volume_hit = volume_ratio_pct >= self._config.get_volume_ratio_threshold()
        amount_hit = tier is not None and abs(estimated_amount) >= self._config.get_tiered_amount_threshold(tier)

        trigger_type = self._pick_trigger_type(volume_hit, amount_hit)
        if trigger_type is None:
            return None

        return InstitutionalAlert(
            scope=AlertScope.STOCK,
            trigger_type=trigger_type,
            stock_id=trade["stock_id"],
            estimated_amount=estimated_amount,
            market_cap_tier=tier,
            volume_ratio_pct=volume_ratio_pct,
        )

    @staticmethod
    def _pick_trigger_type(volume_hit: bool, amount_hit: bool) -> AlertTriggerType | None:
        if volume_hit and amount_hit:
            return AlertTriggerType.VOLUME_AND_AMOUNT
        if volume_hit:
            return AlertTriggerType.VOLUME_RATIO
        if amount_hit:
            return AlertTriggerType.TIERED_AMOUNT
        return None

    def _classify_tier(self, stock_id: str, close_price: float) -> MarketCapTier | None:
        cached = self._storage.read_capital_stock_cache(stock_id)
        if cached is None:
            return None
        try:
            estimated_market_cap = cached["estimated_shares"] * close_price
        except (KeyError, TypeError):
            # 快取內容格式異常時當成沒有快取處理：門檻2 直接判定不達標，
            # 不能因為一份壞掉的快取檔案就讓整批股票的門檻判斷都跑不動。
            return None
        large_min, mid_min = self._config.get_market_cap_tier_bounds()
        if estimated_market_cap >= large_min:
            return MarketCapTier.LARGE
        if estimated_market_cap >= mid_min:
            return MarketCapTier.MID
        return MarketCapTier.SMALL


class MarketInstitutionalFilter:
    """大盤外資／投信／自營商三類買賣金額，各自獨立與門檻比較，互不影響、也互不要求同時成立。"""

    _CHECKS = (
        ("foreign", AlertTriggerType.MARKET_FOREIGN, "foreign_net_amount"),
        ("trust", AlertTriggerType.MARKET_TRUST, "trust_net_amount"),
        ("dealer", AlertTriggerType.MARKET_DEALER, "dealer_net_amount"),
    )

    def __init__(self, config: ConfigLoader):
        self._config = config

    def filter_significant_trades(self, market_record: dict) -> list[InstitutionalAlert]:
        alerts = []
        for investor_type, trigger_type, field in self._CHECKS:
            net_amount = market_record[field]
            threshold = self._config.get_market_institutional_threshold(investor_type)
            if abs(net_amount) >= threshold:
                alerts.append(
                    InstitutionalAlert(
                        scope=AlertScope.MARKET,
                        trigger_type=trigger_type,
                        estimated_amount=net_amount,
                    )
                )
        return alerts


class RebalanceClassifier:
    def __init__(self, config: ConfigLoader):
        self._config = config

    def classify(
        self,
        etf_id: str,
        event_date: str,
        prev_holdings: list[dict],
        curr_holdings: list[dict],
    ) -> list[RebalanceEvent]:
        prev_by_stock = {h["component_stock_id"]: h for h in prev_holdings}
        curr_by_stock = {h["component_stock_id"]: h for h in curr_holdings}

        events = []
        for stock_id in prev_by_stock.keys() | curr_by_stock.keys():
            event = self._classify_one(
                etf_id, event_date, stock_id, prev_by_stock.get(stock_id), curr_by_stock.get(stock_id)
            )
            if event is not None:
                events.append(event)
        return events

    def _classify_one(
        self,
        etf_id: str,
        event_date: str,
        stock_id: str,
        prev: dict | None,
        curr: dict | None,
    ) -> RebalanceEvent | None:
        prev_shares = prev["holding_shares"] if prev else 0
        curr_shares = curr["holding_shares"] if curr else 0
        component_name = (curr or prev)["component_name"]

        if prev_shares == 0 and curr_shares > 0:
            return RebalanceEvent(
                event_date, etf_id, stock_id, component_name,
                RebalanceEventType.ADDITION, prev_shares, curr_shares, None,
            )
        if prev_shares > 0 and curr_shares == 0:
            return RebalanceEvent(
                event_date, etf_id, stock_id, component_name,
                RebalanceEventType.DELETION, prev_shares, curr_shares, None,
            )
        if prev_shares > 0 and curr_shares > 0:
            change_pct = (curr_shares - prev_shares) / prev_shares * 100
            threshold = self._config.get_etf_rebalance_pct_threshold(etf_id)
            if abs(change_pct) >= threshold:
                return RebalanceEvent(
                    event_date, etf_id, stock_id, component_name,
                    RebalanceEventType.REBALANCE, prev_shares, curr_shares, change_pct,
                )
        return None
