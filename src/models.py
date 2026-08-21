"""共用的資料結構與列舉定義，供 fetcher / analyzer / notifier / storage 共用。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SnapshotStatus(str, Enum):
    OK = "OK"
    NO_DATA = "NO_DATA"
    ERROR = "ERROR"


class RebalanceEventType(str, Enum):
    ADDITION = "ADDITION"
    DELETION = "DELETION"
    REBALANCE = "REBALANCE"


class SendStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class DataSourceKey(str, Enum):
    """_meta.json 內 sources 物件的 key，取代原本寫死的字串，避免各處打字不一致。"""

    FINMIND_INSTITUTIONAL = "FINMIND_INSTITUTIONAL"
    FINMIND_PRICE = "FINMIND_PRICE"
    FINMIND_BALANCE_SHEET = "FINMIND_BALANCE_SHEET"
    FINMIND_MARKET = "FINMIND_MARKET"
    FINMIND_BROKER = "FINMIND_BROKER"
    ISSUER_PCF = "ISSUER_PCF"


class AlertScope(str, Enum):
    """告警的適用範圍：單一股票，或整體大盤。"""

    STOCK = "STOCK"
    MARKET = "MARKET"


class AlertTriggerType(str, Enum):
    """告警是被哪個門檻觸發的，供訊息呈現時挑選對應文案。"""

    VOLUME_RATIO = "VOLUME_RATIO"
    TIERED_AMOUNT = "TIERED_AMOUNT"
    VOLUME_AND_AMOUNT = "VOLUME_AND_AMOUNT"
    MARKET_FOREIGN = "MARKET_FOREIGN"
    MARKET_TRUST = "MARKET_TRUST"
    MARKET_DEALER = "MARKET_DEALER"


class MarketCapTier(str, Enum):
    """個股市值分級，決定門檻2要套用哪一組金額門檻。"""

    LARGE = "LARGE"
    MID = "MID"
    SMALL = "SMALL"


@dataclass
class SourceStatus:
    status: SnapshotStatus
    fetched_at: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class DailySnapshotMeta:
    snapshot_date: str
    sources: dict[str, SourceStatus]
    is_trading_day: bool


@dataclass
class BrokerTradeRecord:
    trade_date: str
    stock_id: str
    stock_name: str
    broker_name: str
    buy_volume: int
    sell_volume: int
    net_volume: int


@dataclass
class EtfHoldingRecord:
    snapshot_date: str
    etf_id: str
    component_stock_id: str
    component_name: str
    holding_shares: int


@dataclass
class RebalanceEvent:
    event_date: str
    etf_id: str
    component_stock_id: str
    component_name: str
    event_type: RebalanceEventType
    prev_shares: int
    curr_shares: int
    change_pct: Optional[float]


@dataclass
class NotificationLogEntry:
    sent_at: str
    recipient_id: str
    message_content: str
    send_status: SendStatus
    retry_count: int
    error_message: Optional[str] = None


@dataclass
class InstitutionalTradeRecord:
    """單一股票當日的三大法人買賣超原始資料，五個法人類別分開存放，total_net 是寫入時就算好的合計。"""

    trade_date: str
    stock_id: str
    stock_name: str
    foreign_investor_buy: int
    foreign_investor_sell: int
    foreign_dealer_self_net: int
    investment_trust_buy: int
    investment_trust_sell: int
    dealer_self_net: int
    dealer_hedging_net: int
    total_net: int


@dataclass
class StockDailyTrading:
    """個股當日成交量與收盤價，供成交量佔比門檻與金額估算共用。"""

    trade_date: str
    stock_id: str
    trading_volume: int
    close_price: float


@dataclass
class StockCapitalSnapshot:
    """個股股本快取，不分日期、單檔覆寫；report_date 是資料本身對應的財報季底日期。"""

    stock_id: str
    report_date: str
    capital_stock: int
    estimated_shares: int
    fetched_at: str


@dataclass
class MarketInstitutionalRecord:
    """大盤（市場整體）當日三大法人買賣金額，跟個股資料無關，每天只有一筆。"""

    trade_date: str
    foreign_net_amount: int
    trust_net_amount: int
    dealer_net_amount: int


@dataclass
class InstitutionalAlert:
    """門檻判斷後的達標結果，只有達標的項目才會存在；scope 決定要看 stock_id 還是純大盤層級。"""

    scope: AlertScope
    trigger_type: AlertTriggerType
    stock_id: Optional[str] = None
    estimated_amount: Optional[int] = None
    market_cap_tier: Optional[MarketCapTier] = None
    volume_ratio_pct: Optional[float] = None
