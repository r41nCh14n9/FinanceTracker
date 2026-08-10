from unittest.mock import MagicMock, patch

import pytest
import requests

from src.fetcher import Fetcher, FinMindClient, TwsePcfClient
from src.models import DataSourceKey, SnapshotStatus, StockCapitalSnapshot
from src.storage import SnapshotRepository


class _FakeConfig:
    def __init__(self, stocks=None, brokers=None, etfs=None, broker_enabled=False):
        self._stocks = stocks or ["2330", "2454"]
        self._brokers = brokers or []
        self._etfs = etfs or ["0050"]
        self._broker_enabled = broker_enabled

    def get_watchlist_stocks(self):
        return list(self._stocks)

    def get_watchlist_brokers(self):
        return list(self._brokers)

    def get_watchlist_etfs(self):
        return list(self._etfs)

    def is_broker_monitoring_enabled(self):
        return self._broker_enabled

    @staticmethod
    def get_env(key, required=True):
        return "dummy-token"


def _make_repo(tmp_path):
    return SnapshotRepository(data_dir=tmp_path / "data")


def _quiet_finmind():
    """回傳一個對每個方法都回應「今天什麼都沒有」的假 FinMindClient，方便測試只關心單一行為。"""
    finmind = MagicMock(spec=FinMindClient)
    finmind.fetch_institutional_trades.return_value = []
    finmind.fetch_stock_trading.return_value = []
    finmind.fetch_capital_stock.return_value = None
    finmind.fetch_market_institutional.return_value = None
    finmind.fetch_broker_trades.return_value = []
    return finmind


def _quiet_twse():
    twse = MagicMock(spec=TwsePcfClient)
    twse.fetch_holdings.return_value = []
    return twse


def test_fetch_all_skips_broker_source_when_disabled(tmp_path):
    storage = _make_repo(tmp_path)
    finmind = _quiet_finmind()
    fetcher = Fetcher(_FakeConfig(broker_enabled=False), storage, finmind_client=finmind, twse_client=_quiet_twse())

    meta = fetcher.fetch_all("2026-08-05")

    assert DataSourceKey.FINMIND_BROKER not in meta.sources
    finmind.fetch_broker_trades.assert_not_called()


def test_fetch_all_includes_broker_source_when_enabled(tmp_path):
    storage = _make_repo(tmp_path)
    finmind = _quiet_finmind()
    fetcher = Fetcher(_FakeConfig(broker_enabled=True), storage, finmind_client=finmind, twse_client=_quiet_twse())

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
    fetcher = Fetcher(_FakeConfig(stocks=["2330"]), storage, finmind_client=finmind, twse_client=_quiet_twse())

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
    fetcher = Fetcher(_FakeConfig(stocks=["2330"]), storage, finmind_client=finmind, twse_client=_quiet_twse())

    meta = fetcher.fetch_all("2026-08-05")

    assert meta.is_trading_day is True


def test_capital_stock_cache_malformed_content_treated_as_stale_not_crash(tmp_path):
    storage = _make_repo(tmp_path)
    # 直接寫壞的快取檔案（缺 fetched_at），模擬人為誤改或寫入中斷；
    # 重點是 fetch_all() 不能被這個壞檔案炸掉，而是把它當「過期」重新抓一次。
    storage._write_json(storage._capital_stock_cache_path("2330"), {"stock_id": "2330"})
    finmind = _quiet_finmind()
    fetcher = Fetcher(_FakeConfig(stocks=["2330"]), storage, finmind_client=finmind, twse_client=_quiet_twse())

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
