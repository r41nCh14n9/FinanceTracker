from unittest.mock import MagicMock

from src.models import (
    AlertScope,
    AlertTriggerType,
    InstitutionalAlert,
    MarketCapTier,
    RebalanceEvent,
    RebalanceEventType,
)
from src.notifier import MessageFormatter, Notifier
from src.storage import SnapshotRepository


def _institutional_trade(stock_id="2330", stock_name="台積電"):
    return {
        "trade_date": "2026-08-05",
        "stock_id": stock_id,
        "stock_name": stock_name,
        "foreign_investor_buy": 0,
        "foreign_investor_sell": 5_000_000,
        "foreign_dealer_self_net": 0,
        "investment_trust_buy": 1_000_000,
        "investment_trust_sell": 0,
        "dealer_self_net": 0,
        "dealer_hedging_net": 0,
        "total_net": -4_000_000,
    }


def test_message_formatter_includes_market_stock_and_etf_sections():
    formatter = MessageFormatter()
    market_alerts = [
        InstitutionalAlert(scope=AlertScope.MARKET, trigger_type=AlertTriggerType.MARKET_FOREIGN, estimated_amount=-25_000_000_000)
    ]
    stock_alerts = [
        InstitutionalAlert(
            scope=AlertScope.STOCK,
            trigger_type=AlertTriggerType.TIERED_AMOUNT,
            stock_id="2330",
            estimated_amount=-800_000_000,
            market_cap_tier=MarketCapTier.LARGE,
            volume_ratio_pct=5.0,
        )
    ]
    institutional_trades = [_institutional_trade()]
    events = [
        RebalanceEvent("2026-08-05", "0050", "3231", "緯創", RebalanceEventType.ADDITION, 0, 520, None),
        RebalanceEvent("2026-08-05", "0050", "2408", "南亞科", RebalanceEventType.DELETION, 800, 0, None),
        RebalanceEvent("2026-08-05", "0050", "2317", "鴻海", RebalanceEventType.REBALANCE, 1000, 1150, 15.0),
    ]
    messages = formatter.format("2026-08-05", market_alerts, stock_alerts, institutional_trades, events)
    combined = "\n".join(messages)

    assert "外資賣超250.0億" in combined
    assert "2330 台積電 [大型, 大額]:賣超 8.0 億元" in combined
    assert "新建倉：3231 緯創" in combined
    assert "完全清倉：2408 南亞科" in combined
    assert "調倉加碼：2317 鴻海" in combined


def test_message_formatter_groups_multiple_etfs_under_one_shared_heading():
    """多檔 ETF 有換倉時，只能有一個「◆ ETF 換倉動態」大標題，每檔 ETF 各自用
    「- {etf_id}:」子標題區隔，不能像以前那樣每檔各自起一個「◆」大標題。
    """
    formatter = MessageFormatter()
    events = [
        RebalanceEvent("2026-08-05", "00981A", "2360", "致茂", RebalanceEventType.REBALANCE, 1000, 100, -90.0),
        RebalanceEvent("2026-08-05", "0050", "3231", "緯創", RebalanceEventType.ADDITION, 0, 520, None),
    ]

    messages = formatter.format("2026-08-05", [], [], [], events)
    combined = "\n".join(messages)

    assert combined.count("◆ ETF 換倉動態") == 1
    assert "- 00981A:" in combined
    assert "- 0050:" in combined
    assert "調倉減碼：2360 致茂" in combined
    assert "新建倉：3231 緯創" in combined


def test_message_formatter_combines_institutional_and_etf_into_one_message_when_short():
    """三大法人跟 ETF 換倉內容都不長時要合併成同一則訊息，不要為了分主題硬拆成多則——
    訊息則數會計入每日/每月推播配額，能合併就合併。"""
    formatter = MessageFormatter()
    events = [RebalanceEvent("2026-08-05", "0050", "3231", "緯創", RebalanceEventType.ADDITION, 0, 520, None)]

    messages = formatter.format("2026-08-05", [], [], [], events)

    assert len(messages) == 1
    assert "◆ 大盤三大法人動態" in messages[0]
    assert "◆ ETF 換倉動態" in messages[0]
    assert "- 0050:" in messages[0]
    assert "新建倉：3231 緯創" in messages[0]


def test_message_formatter_paginates_when_combined_content_too_long():
    """整份簡報（大盤＋個股＋各 ETF 換倉）合併後長度逼近安全上限時，要自動分成好幾則，
    而不是硬塞成一則超過 LINE 上限、整個送不出去的訊息；分頁是跨主題統一計算，不是
    每個主題各自獨立分頁，所以連內容很短的大盤區塊也會出現在每一頁裡。"""
    formatter = MessageFormatter()
    events = [
        RebalanceEvent("2026-08-05", "0050", f"{2000+i}", "測試股票中文名稱", RebalanceEventType.ADDITION, 0, 12345 + i, None)
        for i in range(200)
    ]

    messages = formatter.format("2026-08-05", [], [], [], events)

    assert len(messages) > 1
    assert all(len(m) < 5000 for m in messages)
    assert "（1/" in messages[0]  # 合併分頁後，連第一則都要看得出目前是第幾頁


def test_message_formatter_handles_no_alerts():
    formatter = MessageFormatter()
    messages = formatter.format("2026-08-05", [], [], [], [])
    combined = "\n".join(messages)
    assert "（今日大盤三大法人買賣金額均未達門檻）" in combined
    assert "（無達門檻標的）" in combined


def test_message_formatter_labels_volume_and_amount_trigger_together():
    formatter = MessageFormatter()
    stock_alerts = [
        InstitutionalAlert(
            scope=AlertScope.STOCK,
            trigger_type=AlertTriggerType.VOLUME_AND_AMOUNT,
            stock_id="2330",
            estimated_amount=-800_000_000,
            market_cap_tier=MarketCapTier.LARGE,
            volume_ratio_pct=20.0,
        )
    ]
    messages = formatter.format("2026-08-05", [], stock_alerts, [_institutional_trade()], [])
    assert "[大型, 量能, 大額]" in "\n".join(messages)


class _FakeConfig:
    def __init__(self, recipients):
        self._recipients = recipients

    def get_enabled_recipients(self):
        return self._recipients

    @staticmethod
    def get_env(key, required=True):
        return "dummy-token"


def test_notifier_retries_then_succeeds(tmp_path, monkeypatch):
    storage = SnapshotRepository(data_dir=tmp_path / "data")
    config = _FakeConfig([{"id": "U1", "type": "USER", "label": "", "enabled": True}])

    line_client = MagicMock()
    line_client.push.side_effect = [Exception("boom"), None]
    monkeypatch.setattr("src.notifier.time.sleep", lambda _seconds: None)

    notifier = Notifier(config, storage, line_client=line_client)
    result = notifier.notify("2026-08-05", [], [], [], [])

    assert result is True
    assert line_client.push.call_count == 2


def test_notifier_gives_up_after_max_retries(tmp_path, monkeypatch):
    storage = SnapshotRepository(data_dir=tmp_path / "data")
    config = _FakeConfig([{"id": "U1", "type": "USER", "label": "", "enabled": True}])

    line_client = MagicMock()
    line_client.push.side_effect = Exception("boom")
    monkeypatch.setattr("src.notifier.time.sleep", lambda _seconds: None)

    notifier = Notifier(config, storage, line_client=line_client)
    result = notifier.notify("2026-08-05", [], [], [], [])

    assert result is False
    assert line_client.push.call_count == 3


def test_notifier_batches_more_than_five_messages_into_multiple_push_calls(tmp_path, monkeypatch):
    """LINE push API 一次最多帶 5 則訊息。訊息合併設計下，主題數量本身不會撐出多則訊息
    （例如 6 檔 ETF 各一筆換倉事件全部塞得進同一則），要靠內容長度逼出分頁才會產生
    超過 5 則的情況，這裡用單一 ETF 大量換倉事件撐出 6 頁來驗證 batching 邏輯仍正常。
    """
    storage = SnapshotRepository(data_dir=tmp_path / "data")
    config = _FakeConfig([{"id": "U1", "type": "USER", "label": "", "enabled": True}])

    line_client = MagicMock()
    monkeypatch.setattr("src.notifier.time.sleep", lambda _seconds: None)

    events = [
        RebalanceEvent("2026-08-05", "0050", f"{2000+i}", "測試股票中文名稱", RebalanceEventType.ADDITION, 0, 12345 + i, None)
        for i in range(800)  # 撐出 6 頁（超過單次 push 上限 5 則）
    ]

    notifier = Notifier(config, storage, line_client=line_client)
    result = notifier.notify("2026-08-05", [], [], [], events)

    assert result is True
    assert line_client.push.call_count == 2
    first_batch = line_client.push.call_args_list[0].args[1]
    second_batch = line_client.push.call_args_list[1].args[1]
    assert len(first_batch) == 5
    assert len(second_batch) == 1


def test_notifier_caps_messages_per_day_and_notes_truncation(tmp_path, monkeypatch):
    """假設暫時只有單一收訊者、免費方案配額有限，每天最多送 10 則；訊息合併設計下要靠
    內容長度撐出超過 10 頁，這裡用單一 ETF 大量換倉事件（撐出 11 頁）驗證截斷提示仍
    正常運作，不能讓人誤以為後面的換倉今天沒有異動。"""
    storage = SnapshotRepository(data_dir=tmp_path / "data")
    config = _FakeConfig([{"id": "U1", "type": "USER", "label": "", "enabled": True}])

    line_client = MagicMock()
    monkeypatch.setattr("src.notifier.time.sleep", lambda _seconds: None)

    events = [
        RebalanceEvent("2026-08-05", "0050", f"{2000+i}", "測試股票中文名稱", RebalanceEventType.ADDITION, 0, 12345 + i, None)
        for i in range(1500)  # 撐出 11 頁（超過每日上限 10 則）
    ]

    notifier = Notifier(config, storage, line_client=line_client)
    result = notifier.notify("2026-08-05", [], [], [], events)

    assert result is True
    sent_messages = [m for call in line_client.push.call_args_list for m in call.args[1]]
    assert len(sent_messages) == 10
    assert "另有 2 則內容未發送" in sent_messages[-1]


def test_notifier_only_pushes_to_enabled_recipients(tmp_path, monkeypatch):
    storage = SnapshotRepository(data_dir=tmp_path / "data")
    config = _FakeConfig([
        {"id": "U1", "type": "USER", "label": "", "enabled": True},
        {"id": "U2", "type": "USER", "label": "", "enabled": True},
    ])
    line_client = MagicMock()
    monkeypatch.setattr("src.notifier.time.sleep", lambda _seconds: None)

    notifier = Notifier(config, storage, line_client=line_client)
    notifier.notify("2026-08-05", [], [], [], [])

    assert line_client.push.call_count == 2
