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
