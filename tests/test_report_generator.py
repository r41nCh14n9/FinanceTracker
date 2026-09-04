from src.models import (
    AlertScope,
    AlertTriggerType,
    InstitutionalAlert,
    LimitType,
    LimitUpDownRecord,
    MarketCapTier,
    MarketType,
    RebalanceEvent,
    RebalanceEventType,
)
from src.report_generator import ReportGenerator


def _trade(stock_id="2330", stock_name="台積電", total_net=5_965_000):
    return {
        "trade_date": "2026-09-01",
        "stock_id": stock_id,
        "stock_name": stock_name,
        "foreign_investor_buy": 26_000_000,
        "foreign_investor_sell": 20_000_000,
        "foreign_dealer_self_net": 0,
        "investment_trust_buy": 600_000,
        "investment_trust_sell": 500_000,
        "dealer_self_net": -50_000,
        "dealer_hedging_net": 180_000,
        "total_net": total_net,
    }


def test_generate_includes_title_with_report_date():
    report = ReportGenerator().generate("2026-09-01", [], [], [], {}, [], {}, {})
    assert report.startswith("# 籌碼監控完整日報 2026-09-01")


def test_generate_groups_watchlist_by_concept_and_marks_alerted_stocks():
    trades = [_trade("2330", "台積電"), _trade("2049", "上銀", total_net=700_000)]
    stock_alerts = [
        InstitutionalAlert(
            scope=AlertScope.STOCK, trigger_type=AlertTriggerType.TIERED_AMOUNT,
            stock_id="2330", estimated_amount=800_000_000, market_cap_tier=MarketCapTier.LARGE,
        )
    ]
    concept_map = {"2330": ["半導體"]}

    report = ReportGenerator().generate("2026-09-01", trades, stock_alerts, [], {}, [], {}, concept_map)

    assert "### [半導體]" in report
    assert "### [未分類]" in report
    assert report.index("### [半導體]") < report.index("### [未分類]")
    assert "| 2330 | 台積電 | 大型, 半導體 |" in report
    assert report.index("2330") < report.index("2049")


def test_generate_marks_stock_without_alert_as_not_reaching_threshold():
    trades = [_trade("2049", "上銀", total_net=700_000)]
    report = ReportGenerator().generate("2026-09-01", trades, [], [], {}, [], {}, {})

    # 沒有對應 alert 的股票：不顯示市值分級標籤（不是硬標「未知」），達門檻欄顯示「—」
    assert "| 2049 | 上銀 |  | +6,000 | +100 | +130 | +700 | — |" in report


def test_generate_shows_placeholder_when_no_limit_records():
    report = ReportGenerator().generate("2026-09-01", [], [], [], {}, [], {}, {})
    assert "今日無個股觸及漲跌停" in report


def test_generate_formats_limit_row_with_institutional_breakdown_when_available():
    records = [LimitUpDownRecord("2026-09-01", "1101", "台泥", MarketType.TWSE, LimitType.UP, 110.0, 100.0, 10.0)]
    limit_trades = {"1101": _trade("1101", "台泥", total_net=1_000_000)}

    report = ReportGenerator().generate("2026-09-01", [], [], records, limit_trades, [], {}, {})

    assert "| 1101 | 台泥 | 上市 | 漲停 | 110.00 | +6,000 | +100 | +130 |" in report


def test_generate_shows_query_no_data_when_limit_stock_has_no_institutional_trade():
    records = [LimitUpDownRecord("2026-09-01", "6789", "上櫃甲", MarketType.TPEX, LimitType.DOWN, 45.0, 50.0, -10.0)]

    report = ReportGenerator().generate("2026-09-01", [], [], records, {}, [], {}, {})

    assert "| 6789 | 上櫃甲 | 上櫃 | 跌停 | 45.00 | 查無資料 | 查無資料 | 查無資料 |" in report


def test_generate_shows_placeholder_when_no_rebalance_events():
    report = ReportGenerator().generate("2026-09-01", [], [], [], {}, [], {}, {})
    assert "今日無 ETF 換倉" in report


def test_generate_groups_rebalance_events_by_etf_then_concept():
    events = [
        RebalanceEvent("2026-09-01", "0050", "3231", "緯創", RebalanceEventType.ADDITION, 0, 520, None),
        RebalanceEvent("2026-09-01", "0050", "2317", "鴻海", RebalanceEventType.DELETION, 800, 0, None),
    ]
    concept_map = {"3231": ["伺服器代工"], "2317": ["伺服器代工"]}

    report = ReportGenerator().generate("2026-09-01", [], [], [], {}, events, {}, concept_map)

    assert "### 0050" in report
    assert "#### [伺服器代工]" in report
    assert "| 3231 | 緯創 | 伺服器代工 | 新建倉 +520 股 |" in report
    assert "| 2317 | 鴻海 | 伺服器代工 | 完全清倉 |" in report
