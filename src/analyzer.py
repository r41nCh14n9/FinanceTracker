"""依門檻篩選分點買賣超，並比對 ETF 前後日持股，分類為新建倉／清倉／調倉。"""
from __future__ import annotations

from src.config import ConfigLoader
from src.models import RebalanceEvent, RebalanceEventType


class BrokerFilter:
    def __init__(self, config: ConfigLoader):
        self._config = config

    def filter_significant_trades(self, trades: list[dict]) -> list[dict]:
        threshold = self._config.get_broker_net_volume_threshold()
        return [t for t in trades if abs(t["net_volume"]) >= threshold]


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
