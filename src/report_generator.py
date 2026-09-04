"""彙整 watchlist 全量三大法人買賣超（不篩門檻）、漲跌停清單、ETF 換倉動態，
產出人類可讀的完整版 Markdown 日報，供使用者透過 GitHub 網頁檢視。

LINE 簡報仍然維持門檻篩選過的精簡版，兩者互不取代：LINE 給第一時間掃過重點用，
這份報告給事後想確認「沒達標的股票當天實際數字是多少」用。
"""
from __future__ import annotations

from src.classification import TIER_LABELS, build_classification_tags, group_by_first_concept
from src.models import (
    AlertScope,
    InstitutionalAlert,
    LimitType,
    LimitUpDownRecord,
    MarketType,
    RebalanceEvent,
    RebalanceEventType,
)

_SHARES_PER_LOT = 1000  # 股 -> 張
_REPORT_TITLE_PREFIX = "籌碼監控完整日報"

_WATCHLIST_TABLE_HEADER = (
    "| 股票代碼 | 名稱 | 產業/市值/概念標籤 | 外資買賣超(張) | 投信買賣超(張) | 自營商買賣超(張) | 合計(張) | 是否達門檻 |",
    "|---|---|---|---|---|---|---|---|",
)
_LIMIT_TABLE_HEADER = (
    "| 股票代碼 | 名稱 | 市場 | 漲/跌停 | 收盤價 | 外資(張) | 投信(張) | 自營商(張) |",
    "|---|---|---|---|---|---|---|---|",
)
_REBALANCE_TABLE_HEADER = (
    "| 股票代碼 | 名稱 | 標籤 | 異動內容 |",
    "|---|---|---|---|",
)

_MARKET_LABELS = {MarketType.TWSE: "上市", MarketType.TPEX: "上櫃"}
_LIMIT_TYPE_LABELS = {LimitType.UP: "漲停", LimitType.DOWN: "跌停"}


class ReportGenerator:
    def generate(
        self,
        report_date: str,
        institutional_trades: list[dict],
        stock_alerts: list[InstitutionalAlert],
        limit_records: list[LimitUpDownRecord],
        limit_institutional_trades: dict[str, dict],
        rebalance_events: list[RebalanceEvent],
        industry_map: dict[str, str],
        concept_map: dict[str, list[str]],
    ) -> str:
        """limit_institutional_trades 是「漲跌停股票代碼 -> 三大法人買賣超原始資料」的
        對照表（呼叫端已補查好，watchlist 內本來就有資料的股票會直接沿用，不重打
        API），查無資料的漲跌停股在報告中該欄顯示「查無資料」而非直接漏掉整筆。
        """
        lines: list[str] = [f"# {_REPORT_TITLE_PREFIX} {report_date}", ""]
        lines += self._build_watchlist_section(institutional_trades, stock_alerts, industry_map, concept_map)
        lines += self._build_limit_section(limit_records, limit_institutional_trades)
        lines += self._build_rebalance_section(rebalance_events, industry_map, concept_map)
        lines += ["---", "*本報告由籌碼監控推播引擎自動產生*"]
        return "\n".join(lines)

    def _build_watchlist_section(
        self,
        institutional_trades: list[dict],
        stock_alerts: list[InstitutionalAlert],
        industry_map: dict[str, str],
        concept_map: dict[str, list[str]],
    ) -> list[str]:
        lines = ["## Watchlist 三大法人買賣超（全量，不篩門檻）", ""]
        tier_by_stock = {alert.stock_id: alert.market_cap_tier for alert in stock_alerts if alert.scope == AlertScope.STOCK}
        alerted_stock_ids = set(tier_by_stock.keys())

        for concept_name, trades in group_by_first_concept(institutional_trades, lambda t: t["stock_id"], concept_map):
            lines.append(f"### [{concept_name or '未分類'}]")
            lines.append("")
            lines.extend(_WATCHLIST_TABLE_HEADER)
            for trade in trades:
                lines.append(self._format_watchlist_row(trade, tier_by_stock, alerted_stock_ids, industry_map, concept_map))
            lines.append("")
        return lines

    def _format_watchlist_row(
        self,
        trade: dict,
        tier_by_stock: dict,
        alerted_stock_ids: set,
        industry_map: dict[str, str],
        concept_map: dict[str, list[str]],
    ) -> str:
        stock_id = trade["stock_id"]
        # 只有這檔股票有觸發過門檻，才會有市值分級資料；沒有就單純不顯示這個標籤，
        # 不是每檔股票都硬要標成「未知」——絕大多數股票根本沒被評估過市值分級。
        tier_label = TIER_LABELS.get(tier_by_stock.get(stock_id))
        tags = build_classification_tags(tier_label, stock_id, industry_map, concept_map)
        tag_text = ", ".join(tags)

        foreign_net, trust_net, dealer_net = self._institutional_net_lots(trade)
        total_net = trade["total_net"] / _SHARES_PER_LOT
        checked = "✅" if stock_id in alerted_stock_ids else "—"

        return (
            f"| {stock_id} | {trade['stock_name']} | {tag_text} | "
            f"{foreign_net:+,.0f} | {trust_net:+,.0f} | {dealer_net:+,.0f} | {total_net:+,.0f} | {checked} |"
        )

    def _build_limit_section(
        self, limit_records: list[LimitUpDownRecord], limit_institutional_trades: dict[str, dict]
    ) -> list[str]:
        lines = ["## 今日漲跌停股票", ""]
        if not limit_records:
            lines.append("今日無個股觸及漲跌停")
            lines.append("")
            return lines

        lines.extend(_LIMIT_TABLE_HEADER)
        for record in limit_records:
            trade = limit_institutional_trades.get(record.stock_id)
            lines.append(self._format_limit_row(record, trade))
        lines.append("")
        return lines

    def _format_limit_row(self, record: LimitUpDownRecord, trade: dict | None) -> str:
        market_label = _MARKET_LABELS[record.market]
        limit_label = _LIMIT_TYPE_LABELS[record.limit_type]
        if trade is None:
            foreign_text = trust_text = dealer_text = "查無資料"
        else:
            foreign_net, trust_net, dealer_net = self._institutional_net_lots(trade)
            foreign_text, trust_text, dealer_text = f"{foreign_net:+,.0f}", f"{trust_net:+,.0f}", f"{dealer_net:+,.0f}"

        return (
            f"| {record.stock_id} | {record.stock_name} | {market_label} | {limit_label} | "
            f"{record.close_price:,.2f} | {foreign_text} | {trust_text} | {dealer_text} |"
        )

    def _build_rebalance_section(
        self,
        rebalance_events: list[RebalanceEvent],
        industry_map: dict[str, str],
        concept_map: dict[str, list[str]],
    ) -> list[str]:
        lines = ["## ETF 換倉動態", ""]
        grouped_by_etf = self._group_by_etf(rebalance_events)
        if not grouped_by_etf:
            lines.append("今日無 ETF 換倉")
            lines.append("")
            return lines

        for etf_id, events in grouped_by_etf.items():
            lines.append(f"### {etf_id}")
            lines.append("")
            for concept_name, concept_events in group_by_first_concept(events, lambda e: e.component_stock_id, concept_map):
                lines.append(f"#### [{concept_name or '未分類'}]")
                lines.append("")
                lines.extend(_REBALANCE_TABLE_HEADER)
                for event in concept_events:
                    lines.append(self._format_rebalance_row(event, industry_map, concept_map))
                lines.append("")
        return lines

    @staticmethod
    def _group_by_etf(events: list[RebalanceEvent]) -> dict[str, list[RebalanceEvent]]:
        grouped: dict[str, list[RebalanceEvent]] = {}
        for event in events:
            grouped.setdefault(event.etf_id, []).append(event)
        return grouped

    def _format_rebalance_row(
        self, event: RebalanceEvent, industry_map: dict[str, str], concept_map: dict[str, list[str]]
    ) -> str:
        tags = build_classification_tags(None, event.component_stock_id, industry_map, concept_map)
        tag_text = ", ".join(tags)
        return f"| {event.component_stock_id} | {event.component_name} | {tag_text} | {self._describe_rebalance(event)} |"

    @staticmethod
    def _describe_rebalance(event: RebalanceEvent) -> str:
        if event.event_type == RebalanceEventType.ADDITION:
            return f"新建倉 +{event.curr_shares:,} 股"
        if event.event_type == RebalanceEventType.DELETION:
            return "完全清倉"
        action = "加碼" if event.change_pct >= 0 else "減碼"
        sign = "+" if event.change_pct >= 0 else ""
        return f"調倉{action} {sign}{event.curr_shares - event.prev_shares:,} 股，{sign}{event.change_pct:.1f}%"

    @staticmethod
    def _institutional_net_lots(trade: dict) -> tuple[float, float, float]:
        foreign_net = (trade["foreign_investor_buy"] - trade["foreign_investor_sell"]) + trade["foreign_dealer_self_net"]
        trust_net = trade["investment_trust_buy"] - trade["investment_trust_sell"]
        dealer_net = trade["dealer_self_net"] + trade["dealer_hedging_net"]
        return foreign_net / _SHARES_PER_LOT, trust_net / _SHARES_PER_LOT, dealer_net / _SHARES_PER_LOT
