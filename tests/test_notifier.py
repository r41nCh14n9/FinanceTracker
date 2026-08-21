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


def test_message_formatter_splits_institutional_and_etf_into_separate_messages():
    """三大法人跟 ETF 換倉要各自是獨立的訊息，不能混在同一則裡。"""
    formatter = MessageFormatter()
    events = [RebalanceEvent("2026-08-05", "0050", "3231", "緯創", RebalanceEventType.ADDITION, 0, 520, None)]

    messages = formatter.format("2026-08-05", [], [], [], events)

    assert len(messages) == 2
    assert "◆ 大盤三大法人動態" in messages[0]
    assert "新建倉：3231 緯創" not in messages[0]
    assert "◆ 0050 ETF 換倉動態" in messages[1]
    assert "新建倉：3231 緯創" in messages[1]


def test_message_formatter_paginates_when_single_section_too_long():
    """單一 ETF 換倉筆數多到單則會超過安全長度時，要自動分成好幾則，
    而不是硬塞成一則超過 LINE 上限、整個送不出去的訊息。"""
    formatter = MessageFormatter()
    events = [
        RebalanceEvent("2026-08-05", "0050", f"{2000+i}", "測試股票中文名稱", RebalanceEventType.ADDITION, 0, 12345 + i, None)
        for i in range(200)
    ]

    messages = formatter.format("2026-08-05", [], [], [], events)

    # messages[0] 是三大法人（本例無資料，內容很短，不會被分頁）；
    # ETF 換倉筆數多，接在後面的訊息才會被分頁。
    assert len(messages) > 2
    assert all(len(m) < 5000 for m in messages)
    assert "（1/" in messages[1]


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
    """LINE push API 一次最多帶 5 則訊息，監控的 ETF 一多、產生超過 5 則訊息時，
    要拆成多次 push() 呼叫，而不是硬塞進同一次呼叫被 API 拒絕。"""
    storage = SnapshotRepository(data_dir=tmp_path / "data")
    config = _FakeConfig([{"id": "U1", "type": "USER", "label": "", "enabled": True}])

    line_client = MagicMock()
    monkeypatch.setattr("src.notifier.time.sleep", lambda _seconds: None)

    # 6 檔 ETF 各自都有換倉事件 -> 三大法人 1 則 + 6 則 ETF 訊息 = 7 則，應拆成 2 次 push()（5+2）
    events = [
        RebalanceEvent("2026-08-05", etf_id, "1101", "台泥", RebalanceEventType.ADDITION, 0, 100, None)
        for etf_id in ["0050", "0056", "00940", "006208", "00919", "00878"]
    ]

    notifier = Notifier(config, storage, line_client=line_client)
    result = notifier.notify("2026-08-05", [], [], [], events)

    assert result is True
    assert line_client.push.call_count == 2
    first_batch = line_client.push.call_args_list[0].args[1]
    second_batch = line_client.push.call_args_list[1].args[1]
    assert len(first_batch) == 5
    assert len(second_batch) == 2


def test_notifier_caps_messages_per_day_and_notes_truncation(tmp_path, monkeypatch):
    """假設暫時只有單一收訊者、免費方案配額有限，每天最多送 10 則；
    超過時要看到明確的截斷提示，不能讓人誤以為後面的 ETF 今天沒有異動。"""
    storage = SnapshotRepository(data_dir=tmp_path / "data")
    config = _FakeConfig([{"id": "U1", "type": "USER", "label": "", "enabled": True}])

    line_client = MagicMock()
    monkeypatch.setattr("src.notifier.time.sleep", lambda _seconds: None)

    # 12 檔 ETF 各自有換倉事件 -> 三大法人 1 則 + 12 則 ETF 訊息 = 13 則，應截斷成 10 則
    events = [
        RebalanceEvent("2026-08-05", f"ETF{i}", "1101", "台泥", RebalanceEventType.ADDITION, 0, 100, None)
        for i in range(12)
    ]

    notifier = Notifier(config, storage, line_client=line_client)
    result = notifier.notify("2026-08-05", [], [], [], events)

    assert result is True
    sent_messages = [m for call in line_client.push.call_args_list for m in call.args[1]]
    assert len(sent_messages) == 10
    assert "另有 4 則內容未發送" in sent_messages[-1]


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
