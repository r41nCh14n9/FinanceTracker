"""將分析結果格式化為簡報文字，並透過 LINE Messaging API 推播給設定檔中的收訊名單。

簡報拆成多則訊息：三大法人（大盤＋個股）一組、每檔有換倉的 ETF 各自一組；任一組內容
逼近 LINE 文字訊息長度上限時會自動分頁成好幾則，避免監控標的一多就整批送不出去。
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

# LINE 文字訊息型別實際上限是 5000 字元，這裡抓一個有緩衝的安全值來分頁，
# 不要讓內容剛好卡在邊界（例如換行符號、分頁標記本身也會佔掉一點長度）。
_SAFE_MESSAGE_CHARS = 4500
# LINE push API 一次最多能帶 5 則訊息（一個 messages 陣列），超過要拆成多次呼叫。
_MAX_MESSAGES_PER_PUSH = 5
# 假設目前只有單一收訊者、免費方案月配額 200 則、每月約 20 個交易日概算出來的
# 每日安全上限；超過時寧可截斷並附上明確提示，也不要真的把月配額用爆。
# 收訊者數量增加或改用付費方案時，這個值需要重新評估。
_MAX_MESSAGES_PER_DAY = 10

_MARKET_LABELS = {
    AlertTriggerType.MARKET_FOREIGN: "外資",
    AlertTriggerType.MARKET_TRUST: "投信",
    AlertTriggerType.MARKET_DEALER: "自營商",
}

_STOCK_TRIGGER_TAGS = {
    AlertTriggerType.VOLUME_RATIO: ["量能"],
    AlertTriggerType.TIERED_AMOUNT: ["大額"],
    AlertTriggerType.VOLUME_AND_AMOUNT: ["量能", "大額"],
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
    ) -> list[str]:
        """回傳這次要推播的訊息清單，依序為：三大法人一組（大盤＋個股，可能因內容過長
        被自動分頁成好幾則），接著是每檔「當天有換倉」的 ETF 各自一組（同樣可能被分頁）。
        清單的順序就是實際推播順序。
        """
        messages = self._format_institutional_messages(
            report_date, market_alerts, stock_alerts, institutional_trades
        )
        for etf_id, events in self._group_by_etf(rebalance_events).items():
            messages.extend(self._format_etf_messages(report_date, etf_id, events))
        return messages

    def _format_institutional_messages(
        self,
        report_date: str,
        market_alerts: list[InstitutionalAlert],
        stock_alerts: list[InstitutionalAlert],
        institutional_trades: list[dict],
    ) -> list[str]:
        header_lines = [f"【籌碼監控日報】{report_date}", "", "◆ 大盤三大法人動態"]
        header_lines.extend(self._format_market_alerts(market_alerts))
        header_lines.extend(["", "◆ 三大法人買賣超（個股）"])

        stock_blocks = self._format_stock_alert_blocks(stock_alerts, institutional_trades)
        if not stock_blocks:
            stock_blocks = [["  （無達門檻標的）"]]

        return self._paginate(header_lines, stock_blocks)

    def _format_etf_messages(self, report_date: str, etf_id: str, events: list[RebalanceEvent]) -> list[str]:
        header_lines = [f"【籌碼監控日報】{report_date}", "", f"◆ {etf_id} ETF 換倉動態"]
        body_blocks = [[line] for line in self._format_events(events)]
        return self._paginate(header_lines, body_blocks)

    @staticmethod
    def _paginate(header_lines: list[str], body_blocks: list[list[str]]) -> list[str]:
        """把 body_blocks（每個 block 是不能攔腰拆開的幾行文字，例如一檔股票的標題行＋
        明細行，或一筆換倉事件）盡量塞進同一則訊息；累積長度逼近安全上限時另起一則，
        並在標題後面加上（n/總頁數）分頁標記，讓收訊者知道還有後續訊息。
        """
        header_text = "\n".join(header_lines)

        pages: list[list[list[str]]] = [[]]
        current_len = len(header_text)
        for block in body_blocks:
            block_len = sum(len(line) + 1 for line in block)
            if pages[-1] and current_len + block_len > _SAFE_MESSAGE_CHARS:
                pages.append([])
                current_len = len(header_text)
            pages[-1].append(block)
            current_len += block_len

        total_pages = len(pages)
        messages = []
        for page_num, page_blocks in enumerate(pages, start=1):
            page_header = header_text if total_pages == 1 else f"{header_text}（{page_num}/{total_pages}）"
            lines = [page_header]
            for block in page_blocks:
                lines.extend(block)
            messages.append("\n".join(lines))
        return messages

    @staticmethod
    def _format_market_alerts(alerts: list[InstitutionalAlert]) -> list[str]:
        if not alerts:
            return ["  （今日大盤三大法人買賣金額均未達門檻）"]
        lines = []
        for alert in alerts:
            label = _MARKET_LABELS[alert.trigger_type]
            direction = "買超" if alert.estimated_amount > 0 else "賣超"
            amount_yi = abs(alert.estimated_amount) / 1e8
            lines.append(f"  {label}{direction}{amount_yi:,.1f}億")
        return lines

    def _format_stock_alert_blocks(
        self, alerts: list[InstitutionalAlert], institutional_trades: list[dict]
    ) -> list[list[str]]:
        """每檔觸發門檻的股票各自組成一個 block（標題行＋明細行），分頁時才不會把
        同一檔股票的標題跟明細拆到不同則訊息裡。
        """
        if not alerts:
            return []
        trades_by_stock = {t["stock_id"]: t for t in institutional_trades}
        blocks = []
        for alert in alerts:
            trade = trades_by_stock.get(alert.stock_id)
            if trade is None:
                continue
            blocks.append([self._format_stock_alert_line(trade, alert)])
        return blocks

    @staticmethod
    def _format_stock_alert_line(trade: dict, alert: InstitutionalAlert) -> str:
        tier_label = _TIER_LABELS.get(alert.market_cap_tier, "未知")
        tags = [tier_label] + _STOCK_TRIGGER_TAGS[alert.trigger_type]

        foreign_net = (trade["foreign_investor_buy"] - trade["foreign_investor_sell"]) + trade["foreign_dealer_self_net"]
        trust_net = trade["investment_trust_buy"] - trade["investment_trust_sell"]
        dealer_net = trade["dealer_self_net"] + trade["dealer_hedging_net"]
        breakdown = (
            f"外 {foreign_net / _SHARES_PER_LOT:+,.0f} 張 / "
            f"投 {trust_net / _SHARES_PER_LOT:+,.0f} 張 / "
            f"自 {dealer_net / _SHARES_PER_LOT:+,.0f} 張"
        )

        amount_part = ""
        if alert.estimated_amount is not None:
            direction = "買超" if alert.estimated_amount > 0 else "賣超"
            amount_yi = abs(alert.estimated_amount) / 1e8
            amount_part = f":{direction} {amount_yi:,.1f} 億元"

        return f"  {trade['stock_id']} {trade['stock_name']} [{', '.join(tags)}]{amount_part} ({breakdown})"

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

    def push(self, recipient_id: str, messages: list[str]) -> None:
        """一次呼叫可以帶多則訊息（LINE 上限 5 則），會依序顯示成好幾個訊息泡泡；
        呼叫端自己要保證不超過這個上限，這裡不重複檢查。
        """
        resp = requests.post(
            self._PUSH_URL,
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            json={"to": recipient_id, "messages": [{"type": "text", "text": m} for m in messages]},
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
        messages = self._formatter.format(
            report_date, market_alerts, stock_alerts, institutional_trades, rebalance_events
        )
        messages = self._cap_daily_messages(messages)
        batches = self._batch_messages(messages)

        all_succeeded = True
        for recipient in self._config.get_enabled_recipients():
            for batch in batches:
                succeeded = self._push_with_retry(report_date, recipient["id"], batch)
                all_succeeded = all_succeeded and succeeded
        return all_succeeded

    @staticmethod
    def _cap_daily_messages(messages: list[str]) -> list[str]:
        """每天最多送 _MAX_MESSAGES_PER_DAY 則，避免單日異動過多把月配額一次用光。
        超過上限時保留最前面的（三大法人優先，其餘依 ETF 順序），最後一則換成明確的
        截斷提示，而不是靜靜把後面的內容丟掉、讓人誤以為那些 ETF today 沒有異動。
        """
        if len(messages) <= _MAX_MESSAGES_PER_DAY:
            return messages
        dropped_count = len(messages) - (_MAX_MESSAGES_PER_DAY - 1)
        kept = messages[: _MAX_MESSAGES_PER_DAY - 1]
        kept.append(
            f"⚠️ 今日異動項目較多，訊息已達每日上限，另有 {dropped_count} 則內容未發送。"
            "完整資料請查看 data/snapshots/ 快照紀錄。"
        )
        return kept

    @staticmethod
    def _batch_messages(messages: list[str]) -> list[list[str]]:
        """LINE push API 一次最多帶 5 則訊息，超過的部分要拆成好幾次呼叫。"""
        return [
            messages[i : i + _MAX_MESSAGES_PER_PUSH]
            for i in range(0, len(messages), _MAX_MESSAGES_PER_PUSH)
        ]

    def _push_with_retry(self, report_date: str, recipient_id: str, batch: list[str]) -> bool:
        last_error = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                self._line_client.push(recipient_id, batch)
                self._log_result(report_date, recipient_id, batch, SendStatus.SUCCESS, attempt, None)
                return True
            except Exception as exc:  # noqa: BLE001 - 一位收訊者失敗不能擋住其他人
                last_error = str(exc)
                logger.warning("LINE 推播失敗（第 %d 次，%s）：%s", attempt + 1, recipient_id, exc)
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(_RETRY_BACKOFF_SECONDS[attempt])

        self._log_result(report_date, recipient_id, batch, SendStatus.FAILED, _MAX_ATTEMPTS - 1, last_error)
        return False

    def _log_result(
        self,
        report_date: str,
        recipient_id: str,
        batch: list[str],
        status: SendStatus,
        retry_count: int,
        error_message: str | None,
    ) -> None:
        entry = NotificationLogEntry(
            sent_at=datetime.now(timezone.utc).isoformat(),
            recipient_id=recipient_id,
            message_content="\n\n---\n\n".join(batch),
            send_status=status,
            retry_count=retry_count,
            error_message=error_message,
        )
        self._storage.append_notification_log(report_date, entry)
