"""將分析結果格式化為簡報文字，並透過 LINE Messaging API 推播給設定檔中的收訊名單。

簡報分三個區塊：大盤三大法人動態、個股三大法人買賣超（含觸發原因標示）、ETF 換倉動態。
推播失敗會重試幾次，仍失敗就放棄並記錄下來，不無限重試以免觸發 LINE 的頻率限流。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from src.config import ConfigLoader
from src.models import (
    AlertTriggerType,
    InstitutionalAlert,
    MarketCapTier,
    NotificationLogEntry,
    RebalanceEvent,
    RebalanceEventType,
    SendStatus,
)
from src.storage import SnapshotRepository

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (5, 15, 30)

_SHARES_PER_LOT = 1000  # 股 -> 張

_MARKET_LABELS = {
    AlertTriggerType.MARKET_FOREIGN: "外資",
    AlertTriggerType.MARKET_TRUST: "投信",
    AlertTriggerType.MARKET_DEALER: "自營商",
}

_STOCK_TRIGGER_LABELS = {
    AlertTriggerType.VOLUME_RATIO: "量能異常",
    AlertTriggerType.TIERED_AMOUNT: "大額進出",
    AlertTriggerType.VOLUME_AND_AMOUNT: "量能異常＋大額進出",
}

_TIER_LABELS = {
    MarketCapTier.LARGE: "大型",
    MarketCapTier.MID: "中型",
    MarketCapTier.SMALL: "中小型",
}


class MessageFormatter:
    def format(
        self,
        report_date: str,
        market_alerts: list[InstitutionalAlert],
        stock_alerts: list[InstitutionalAlert],
        institutional_trades: list[dict],
        rebalance_events: list[RebalanceEvent],
    ) -> str:
        lines = [f"【籌碼監控日報】{report_date}", ""]

        lines.append("◆ 大盤三大法人動態")
        lines.extend(self._format_market_alerts(market_alerts))
        lines.append("")

        lines.append("◆ 三大法人買賣超（個股）")
        lines.extend(self._format_stock_alerts(stock_alerts, institutional_trades))
        lines.append("")

        for etf_id, events in self._group_by_etf(rebalance_events).items():
            lines.append(f"◆ {etf_id} ETF 換倉動態")
            lines.extend(self._format_events(events))
            lines.append("")

        lines.append("（本訊息由籌碼監控引擎自動產生，個股金額為估算值）")
        return "\n".join(lines)

    @staticmethod
    def _format_market_alerts(alerts: list[InstitutionalAlert]) -> list[str]:
        if not alerts:
            return ["  （今日大盤三大法人買賣金額均未達門檻）"]
        lines = []
        for alert in alerts:
            label = _MARKET_LABELS[alert.trigger_type]
            direction = "買超" if alert.estimated_amount > 0 else "賣超"
            amount_yi = abs(alert.estimated_amount) / 1e8
            lines.append(f"  {label}單日{direction} {amount_yi:,.1f} 億元")
        return lines

    def _format_stock_alerts(self, alerts: list[InstitutionalAlert], institutional_trades: list[dict]) -> list[str]:
        if not alerts:
            return ["  （無達門檻標的）"]
        trades_by_stock = {t["stock_id"]: t for t in institutional_trades}
        lines = []
        for alert in alerts:
            trade = trades_by_stock.get(alert.stock_id)
            if trade is None:
                continue
            label = _STOCK_TRIGGER_LABELS[alert.trigger_type]
            lines.append(f"  {trade['stock_id']} {trade['stock_name']} [{label}]")
            lines.extend(self._format_institutional_detail(trade, alert))
        return lines

    @staticmethod
    def _format_institutional_detail(trade: dict, alert: InstitutionalAlert) -> list[str]:
        foreign_net = (trade["foreign_investor_buy"] - trade["foreign_investor_sell"]) + trade["foreign_dealer_self_net"]
        trust_net = trade["investment_trust_buy"] - trade["investment_trust_sell"]
        dealer_net = trade["dealer_self_net"] + trade["dealer_hedging_net"]
        lines = [
            f"    外資 {foreign_net / _SHARES_PER_LOT:+,.0f} 張／"
            f"投信 {trust_net / _SHARES_PER_LOT:+,.0f} 張／"
            f"自營商 {dealer_net / _SHARES_PER_LOT:+,.0f} 張"
        ]
        if alert.estimated_amount is not None:
            direction = "買超" if alert.estimated_amount > 0 else "賣超"
            amount_yi = abs(alert.estimated_amount) / 1e8
            tier_label = _TIER_LABELS.get(alert.market_cap_tier, "未知")
            lines.append(f"    估算金額：{direction} {amount_yi:,.1f} 億元（市值分級：{tier_label}）")
        return lines

    @staticmethod
    def _group_by_etf(events: list[RebalanceEvent]) -> dict[str, list[RebalanceEvent]]:
        grouped: dict[str, list[RebalanceEvent]] = {}
        for event in events:
            grouped.setdefault(event.etf_id, []).append(event)
        return grouped

    @staticmethod
    def _format_events(events: list[RebalanceEvent]) -> list[str]:
        lines = []
        for e in events:
            if e.event_type == RebalanceEventType.ADDITION:
                lines.append(f"  新建倉：{e.component_stock_id} {e.component_name}（+{e.curr_shares:,} 股）")
            elif e.event_type == RebalanceEventType.DELETION:
                lines.append(f"  完全清倉：{e.component_stock_id} {e.component_name}")
            else:
                action = "加碼" if e.change_pct >= 0 else "減碼"
                sign = "+" if e.change_pct >= 0 else ""
                lines.append(
                    f"  調倉{action}：{e.component_stock_id} {e.component_name}"
                    f"（{sign}{e.curr_shares - e.prev_shares:,} 股，{sign}{e.change_pct:.1f}%）"
                )
        return lines


class LineClient:
    _PUSH_URL = "https://api.line.me/v2/bot/message/push"

    def __init__(self, channel_access_token: str):
        self._token = channel_access_token

    def push(self, recipient_id: str, message: str) -> None:
        resp = requests.post(
            self._PUSH_URL,
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            json={"to": recipient_id, "messages": [{"type": "text", "text": message}]},
            timeout=30,
        )
        resp.raise_for_status()


class Notifier:
    def __init__(self, config: ConfigLoader, storage: SnapshotRepository, line_client: LineClient | None = None):
        self._config = config
        self._storage = storage
        self._formatter = MessageFormatter()
        self._line_client = line_client or LineClient(config.get_env("LINE_CHANNEL_ACCESS_TOKEN"))

    def notify(
        self,
        report_date: str,
        market_alerts: list[InstitutionalAlert],
        stock_alerts: list[InstitutionalAlert],
        institutional_trades: list[dict],
        rebalance_events: list[RebalanceEvent],
    ) -> bool:
        message = self._formatter.format(
            report_date, market_alerts, stock_alerts, institutional_trades, rebalance_events
        )

        all_succeeded = True
        for recipient in self._config.get_enabled_recipients():
            succeeded = self._push_with_retry(report_date, recipient["id"], message)
            all_succeeded = all_succeeded and succeeded
        return all_succeeded

    def _push_with_retry(self, report_date: str, recipient_id: str, message: str) -> bool:
        last_error = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                self._line_client.push(recipient_id, message)
                self._log_result(report_date, recipient_id, message, SendStatus.SUCCESS, attempt, None)
                return True
            except Exception as exc:  # noqa: BLE001 - 一位收訊者失敗不能擋住其他人
                last_error = str(exc)
                logger.warning("LINE 推播失敗（第 %d 次，%s）：%s", attempt + 1, recipient_id, exc)
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(_RETRY_BACKOFF_SECONDS[attempt])

        self._log_result(report_date, recipient_id, message, SendStatus.FAILED, _MAX_ATTEMPTS - 1, last_error)
        return False

    def _log_result(
        self,
        report_date: str,
        recipient_id: str,
        message: str,
        status: SendStatus,
        retry_count: int,
        error_message: str | None,
    ) -> None:
        entry = NotificationLogEntry(
            sent_at=datetime.now(timezone.utc).isoformat(),
            recipient_id=recipient_id,
            message_content=message,
            send_status=status,
            retry_count=retry_count,
            error_message=error_message,
        )
        self._storage.append_notification_log(report_date, entry)
