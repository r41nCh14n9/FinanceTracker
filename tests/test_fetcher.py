from unittest.mock import MagicMock, patch

import pytest
import requests

from src.fetcher import Fetcher, FinMindClient
from src.issuer_pcf.base import IssuerPcfProvider
from src.models import DailySnapshotMeta, DataSourceKey, EtfHoldingRecord, SnapshotStatus, StockCapitalSnapshot
from src.storage import SnapshotRepository


class _FakeConfig:
    def __init__(
        self, stocks=None, brokers=None, etfs=None, broker_enabled=False, issuer_mappings=None,
        holding_drop_pct_threshold=50.0,
    ):
        self._stocks = stocks or ["2330", "2454"]
        self._brokers = brokers or []
        self._etfs = etfs or ["0050"]
        self._broker_enabled = broker_enabled
        self._issuer_mappings = issuer_mappings or {}
        self._holding_drop_pct_threshold = holding_drop_pct_threshold

    def get_watchlist_stocks(self):
        return list(self._stocks)

    def get_watchlist_brokers(self):
        return list(self._brokers)

    def get_watchlist_etfs(self):
        return list(self._etfs)

    def is_broker_monitoring_enabled(self):
        return self._broker_enabled

    def get_issuer_mapping(self, etf_id):
        return self._issuer_mappings[etf_id]

    def get_etf_holding_count_drop_pct_threshold(self):
        return self._holding_drop_pct_threshold

    @staticmethod
    def get_env(key, required=True):
        return "dummy-token"


def _make_repo(tmp_path):
    return SnapshotRepository(data_dir=tmp_path / "data")


def _make_trading_day_meta(snapshot_date):
    """只是為了讓 find_previous_trading_day() 認得這天是交易日，內容細節不重要。"""
    return DailySnapshotMeta(snapshot_date=snapshot_date, sources={}, is_trading_day=True)


def _holding_record(snapshot_date, stock_id, name, shares):
    return EtfHoldingRecord(
        snapshot_date=snapshot_date, etf_id="0050",
        component_stock_id=stock_id, component_name=name, holding_shares=shares,
    )


def _quiet_finmind():
    """回傳一個對每個方法都回應「今天什麼都沒有」的假 FinMindClient，方便測試只關心單一行為。"""
    finmind = MagicMock(spec=FinMindClient)
    finmind.fetch_institutional_trades.return_value = []
    finmind.fetch_stock_trading.return_value = []
    finmind.fetch_capital_stock.return_value = None
    finmind.fetch_market_institutional.return_value = None
    finmind.fetch_broker_trades.return_value = []
    return finmind


def _quiet_issuer_providers(etf_ids=("0050",)):
    """回傳一個依 ETF 代碼索引的假 provider 對照表，每個都回應「今天沒有資料」。"""
    providers = {}
    for etf_id in etf_ids:
        provider = MagicMock(spec=IssuerPcfProvider)
        provider.fetch_holdings.return_value = []
        providers[etf_id] = provider
    return providers


def test_fetch_all_skips_broker_source_when_disabled(tmp_path):
    storage = _make_repo(tmp_path)
    finmind = _quiet_finmind()
    fetcher = Fetcher(_FakeConfig(broker_enabled=False), storage, finmind_client=finmind, issuer_providers=_quiet_issuer_providers())

    meta = fetcher.fetch_all("2026-08-05")

    assert DataSourceKey.FINMIND_BROKER not in meta.sources
    finmind.fetch_broker_trades.assert_not_called()


def test_fetch_all_includes_broker_source_when_enabled(tmp_path):
    storage = _make_repo(tmp_path)
    finmind = _quiet_finmind()
    fetcher = Fetcher(_FakeConfig(broker_enabled=True), storage, finmind_client=finmind, issuer_providers=_quiet_issuer_providers())

    meta = fetcher.fetch_all("2026-08-05")

    assert DataSourceKey.FINMIND_BROKER in meta.sources
    finmind.fetch_broker_trades.assert_called_once()


def test_is_trading_day_ignores_capital_stock_cache_hit(tmp_path):
    # 模擬假日：股本快取還新鮮（沿用前一個交易日的快取），但當天真正跟市場活動有關的
    # 來源全部是 NO_DATA；is_trading_day 不該被股本快取的 OK 狀態誤導成 True。
    storage = _make_repo(tmp_path)
    storage.write_capital_stock_cache(
        StockCapitalSnapshot("2330", "2026-03-31", 100, 10, "2026-08-05T00:00:00+00:00")
    )
    finmind = _quiet_finmind()
    fetcher = Fetcher(_FakeConfig(stocks=["2330"]), storage, finmind_client=finmind, issuer_providers=_quiet_issuer_providers())

    meta = fetcher.fetch_all("2026-08-08")

    assert meta.sources[DataSourceKey.FINMIND_BALANCE_SHEET].status == SnapshotStatus.OK
    assert meta.is_trading_day is False
    finmind.fetch_capital_stock.assert_not_called()  # 快取新鮮，不應該重打 API


def test_is_trading_day_true_when_institutional_data_present(tmp_path):
    storage = _make_repo(tmp_path)
    finmind = _quiet_finmind()
    finmind.fetch_institutional_trades.return_value = [{
        "stock_id": "2330", "trade_date": "2026-08-05",
        "foreign_investor_buy": 1, "foreign_investor_sell": 0, "foreign_dealer_self_net": 0,
        "investment_trust_buy": 0, "investment_trust_sell": 0,
        "dealer_self_net": 0, "dealer_hedging_net": 0, "total_net": 1,
    }]
    fetcher = Fetcher(_FakeConfig(stocks=["2330"]), storage, finmind_client=finmind, issuer_providers=_quiet_issuer_providers())

    meta = fetcher.fetch_all("2026-08-05")

    assert meta.is_trading_day is True


def test_capital_stock_cache_malformed_content_treated_as_stale_not_crash(tmp_path):
    storage = _make_repo(tmp_path)
    # 直接寫壞的快取檔案（缺 fetched_at），模擬人為誤改或寫入中斷；
    # 重點是 fetch_all() 不能被這個壞檔案炸掉，而是把它當「過期」重新抓一次。
    storage._write_json(storage._capital_stock_cache_path("2330"), {"stock_id": "2330"})
    finmind = _quiet_finmind()
    fetcher = Fetcher(_FakeConfig(stocks=["2330"]), storage, finmind_client=finmind, issuer_providers=_quiet_issuer_providers())

    meta = fetcher.fetch_all("2026-08-05")  # 不應該拋例外

    finmind.fetch_capital_stock.assert_called_once()  # 視為過期，重新抓一次
    assert meta.sources[DataSourceKey.FINMIND_BALANCE_SHEET].status in (SnapshotStatus.OK, SnapshotStatus.NO_DATA)


def test_fetch_institutional_trades_single_stock_failure_keeps_other_stocks_data():
    """直接打 FinMindClient 這一層，確認同一批股票裡單一檔失敗不會拖累其他檔。"""
    client = FinMindClient(token="x")

    def fake_get(url, params, timeout):
        if params["data_id"] == "9999":
            raise requests.exceptions.Timeout("simulated timeout for 9999 only")
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "data": [{
                "date": params["start_date"], "stock_id": params["data_id"],
                "name": "Foreign_Investor", "buy": 100, "sell": 50,
            }]
        }
        return resp

    with patch("src.fetcher.requests.get", side_effect=fake_get):
        result = client.fetch_institutional_trades("2026-08-05", ["2330", "9999", "2454"])

    assert {r["stock_id"] for r in result} == {"2330", "2454"}


def test_fetch_stock_trading_single_stock_failure_keeps_other_stocks_data():
    client = FinMindClient(token="x")

    def fake_get(url, params, timeout):
        if params["data_id"] == "9999":
            raise requests.exceptions.Timeout("simulated timeout for 9999 only")
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "data": [{"date": params["start_date"], "stock_id": params["data_id"], "Trading_Volume": 1000, "close": 100.0}]
        }
        return resp

    with patch("src.fetcher.requests.get", side_effect=fake_get):
        result = client.fetch_stock_trading("2026-08-05", ["2330", "9999", "2454"])

    assert {r["stock_id"] for r in result} == {"2330", "2454"}


def test_fetch_broker_trades_single_stock_failure_keeps_other_stocks_data():
    client = FinMindClient(token="x")

    def fake_get(url, params, timeout):
        if params["data_id"] == "9999":
            raise requests.exceptions.Timeout("simulated timeout for 9999 only")
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "data": [{"stock_id": params["data_id"], "securities_trader": "凱基-台北", "buy": 100, "sell": 0}]
        }
        return resp

    with patch("src.fetcher.requests.get", side_effect=fake_get):
        result = client.fetch_broker_trades("2026-08-05", ["2330", "9999", "2454"], ["凱基-台北"])

    assert {r["stock_id"] for r in result} == {"2330", "2454"}


def test_fetch_etf_holdings_resolves_provider_from_registry_when_not_overridden(tmp_path):
    storage = _make_repo(tmp_path)
    finmind = _quiet_finmind()
    fake_adapter_cls = MagicMock()
    fake_adapter_instance = fake_adapter_cls.return_value
    fake_adapter_instance.fetch_holdings.return_value = [
        {"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 100}
    ]

    config = _FakeConfig(issuer_mappings={"0050": {"adapter": "FakeAdapter"}})
    fetcher = Fetcher(config, storage, finmind_client=finmind)

    with patch.dict("src.fetcher.ADAPTER_REGISTRY", {"FakeAdapter": fake_adapter_cls}, clear=True):
        meta = fetcher.fetch_all("2026-08-05")

    assert meta.sources[DataSourceKey.ISSUER_PCF].status == SnapshotStatus.OK
    fake_adapter_instance.fetch_holdings.assert_called_once_with("0050", "2026-08-05")


def test_fetch_etf_holdings_unknown_adapter_reports_error_without_crashing(tmp_path):
    storage = _make_repo(tmp_path)
    finmind = _quiet_finmind()
    config = _FakeConfig(issuer_mappings={"0050": {"adapter": "NotRegisteredAdapter"}})
    fetcher = Fetcher(config, storage, finmind_client=finmind)

    with patch.dict("src.fetcher.ADAPTER_REGISTRY", {}, clear=True):
        meta = fetcher.fetch_all("2026-08-05")  # 不應該拋例外

    assert meta.sources[DataSourceKey.ISSUER_PCF].status == SnapshotStatus.ERROR
    assert meta.sources[DataSourceKey.ISSUER_PCF].error_message


def test_fetch_etf_holdings_rejects_drastic_drop_from_previous_day(tmp_path):
    """前一天 40 檔，今天只解析到 3 檔（跌幅 92.5% > 50% 門檻），視為網站改版造成的解析異常，不寫入。"""
    storage = _make_repo(tmp_path)
    storage.write_meta(_make_trading_day_meta("2026-08-04"))
    storage.write_etf_holdings(
        "2026-08-04", "0050",
        [_holding_record("2026-08-04", str(i), f"股票{i}", 1000) for i in range(40)],
    )
    finmind = _quiet_finmind()
    provider = MagicMock(spec=IssuerPcfProvider)
    provider.fetch_holdings.return_value = [
        {"component_stock_id": "1", "component_name": "股票1", "holding_shares": 1000},
        {"component_stock_id": "2", "component_name": "股票2", "holding_shares": 1000},
        {"component_stock_id": "3", "component_name": "股票3", "holding_shares": 1000},
    ]
    fetcher = Fetcher(_FakeConfig(), storage, finmind_client=finmind, issuer_providers={"0050": provider})

    meta = fetcher.fetch_all("2026-08-05")

    assert meta.sources[DataSourceKey.ISSUER_PCF].status == SnapshotStatus.ERROR
    assert "異常" in meta.sources[DataSourceKey.ISSUER_PCF].error_message
    assert storage.read_etf_holdings("2026-08-05", "0050") == []


def test_fetch_etf_holdings_accepts_drop_below_threshold(tmp_path):
    """前一天 40 檔、今天 30 檔（跌幅 25% < 50% 門檻），屬正常換倉範圍，照常寫入。"""
    storage = _make_repo(tmp_path)
    storage.write_meta(_make_trading_day_meta("2026-08-04"))
    storage.write_etf_holdings(
        "2026-08-04", "0050",
        [_holding_record("2026-08-04", str(i), f"股票{i}", 1000) for i in range(40)],
    )
    finmind = _quiet_finmind()
    provider = MagicMock(spec=IssuerPcfProvider)
    provider.fetch_holdings.return_value = [
        {"component_stock_id": str(i), "component_name": f"股票{i}", "holding_shares": 1000} for i in range(30)
    ]
    fetcher = Fetcher(_FakeConfig(), storage, finmind_client=finmind, issuer_providers={"0050": provider})

    meta = fetcher.fetch_all("2026-08-05")

    assert meta.sources[DataSourceKey.ISSUER_PCF].status == SnapshotStatus.OK
    assert len(storage.read_etf_holdings("2026-08-05", "0050")) == 30


def test_fetch_etf_holdings_corrupted_previous_snapshot_does_not_crash_whole_fetch(tmp_path):
    """健全性檢查只是輔助讀取，前一天快照檔案本身壞掉（人為誤改/寫入中斷）時不能把整個
    fetch_all() 炸掉，應該當作沒有基準可比對、正常繼續寫入今天的資料。
    """
    storage = _make_repo(tmp_path)
    storage.write_meta(_make_trading_day_meta("2026-08-04"))
    bad_path = storage._snapshot_dir("2026-08-04") / "etf_holdings" / "0050.json"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text("{not valid json", encoding="utf-8")
    finmind = _quiet_finmind()
    provider = MagicMock(spec=IssuerPcfProvider)
    provider.fetch_holdings.return_value = [
        {"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 100}
    ]
    fetcher = Fetcher(_FakeConfig(), storage, finmind_client=finmind, issuer_providers={"0050": provider})

    meta = fetcher.fetch_all("2026-08-05")  # 不應該拋例外

    assert meta.sources[DataSourceKey.ISSUER_PCF].status == SnapshotStatus.OK
    assert len(storage.read_etf_holdings("2026-08-05", "0050")) == 1


def test_fetch_etf_holdings_no_previous_snapshot_does_not_block_new_etf(tmp_path):
    """剛加入監控的新 ETF 沒有前一天快照可比對，不能因為「沒有基準」就誤判成異常。"""
    storage = _make_repo(tmp_path)
    finmind = _quiet_finmind()
    provider = MagicMock(spec=IssuerPcfProvider)
    provider.fetch_holdings.return_value = [
        {"component_stock_id": "2330", "component_name": "台積電", "holding_shares": 100}
    ]
    fetcher = Fetcher(_FakeConfig(), storage, finmind_client=finmind, issuer_providers={"0050": provider})

    meta = fetcher.fetch_all("2026-08-05")

    assert meta.sources[DataSourceKey.ISSUER_PCF].status == SnapshotStatus.OK
    assert len(storage.read_etf_holdings("2026-08-05", "0050")) == 1


def test_get_masks_token_in_raised_exception_message():
    client = FinMindClient(token="SUPER-SECRET-TOKEN")

    def fake_get(url, params, timeout):
        raise requests.exceptions.HTTPError(
            f"400 Client Error: Bad Request for url: {url}?token={params['token']}"
        )

    with patch("src.fetcher.requests.get", side_effect=fake_get):
        with pytest.raises(RuntimeError) as exc_info:
            client.fetch_market_institutional("2026-08-05")

    assert "SUPER-SECRET-TOKEN" not in str(exc_info.value)
    assert "token=***" in str(exc_info.value)
