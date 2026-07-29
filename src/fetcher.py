"""對接 FinMind API 與證交所 PCF API，抓取分點買賣超與 ETF 持股資料。

任何單一資料源失敗（逾時、假日無資料、格式異常）都只會記錄下來，
不會讓整個抓取流程中斷；其餘可用資料仍照常寫入快照供後續分析使用。

注意：FinMind / 證交所 PCF 的實際 dataset 名稱與回傳欄位需在串接時對照官方文件確認，
下方欄位名稱為目前最佳猜測，尚待實際呼叫驗證。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from src.config import ConfigLoader
from src.models import (
    BrokerTradeRecord,
    DailySnapshotMeta,
    EtfHoldingRecord,
    SnapshotStatus,
    SourceStatus,
)
from src.storage import SnapshotRepository

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30


class FinMindClient:
    """FinMind API 的薄封裝，只回傳這個系統需要的欄位。"""

    _BASE_URL = "https://api.finmindtrade.com/api/v4/data"

    def __init__(self, token: str):
        self._token = token

    def fetch_broker_trades(self, trade_date: str, stock_ids: list[str], broker_names: list[str]) -> list[dict]:
        records = []
        for stock_id in stock_ids:
            resp = requests.get(
                self._BASE_URL,
                params={
                    "dataset": "TaiwanStockTradingDailyReportSecIdAgg",
                    "data_id": stock_id,
                    "start_date": trade_date,
                    "end_date": trade_date,
                    "token": self._token,
                },
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            rows = resp.json().get("data", [])
            records.extend(row for row in rows if row.get("securities_trader") in broker_names)
        return records


class TwsePcfClient:
    """證交所 PCF API 的薄封裝，取得 ETF 當日成分股清單。"""

    _BASE_URL = "https://www.twse.com.tw/rwd/zh/ETF/pcf"

    def fetch_holdings(self, etf_id: str, snapshot_date: str) -> list[dict]:
        resp = requests.get(
            self._BASE_URL,
            params={"stockNo": etf_id, "date": snapshot_date.replace("-", "")},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])


class Fetcher:
    def __init__(
        self,
        config: ConfigLoader,
        storage: SnapshotRepository,
        finmind_client: FinMindClient | None = None,
        twse_client: TwsePcfClient | None = None,
    ):
        self._config = config
        self._storage = storage
        self._finmind_client = finmind_client or FinMindClient(config.get_env("FINMIND_TOKEN"))
        self._twse_client = twse_client or TwsePcfClient()

    def fetch_all(self, snapshot_date: str) -> DailySnapshotMeta:
        sources = {
            "FINMIND": self._fetch_broker_trades(snapshot_date),
            "TWSE_PCF": self._fetch_etf_holdings(snapshot_date),
        }
        meta = DailySnapshotMeta(
            snapshot_date=snapshot_date,
            sources=sources,
            is_trading_day=any(s.status == SnapshotStatus.OK for s in sources.values()),
        )
        self._storage.write_meta(meta)
        return meta

    def _fetch_broker_trades(self, snapshot_date: str) -> SourceStatus:
        try:
            raw_rows = self._finmind_client.fetch_broker_trades(
                snapshot_date,
                self._config.get_watchlist_stocks(),
                self._config.get_watchlist_brokers(),
            )
        except Exception as exc:  # noqa: BLE001 - 單一來源失敗不能讓整體流程中斷
            logger.warning("FinMind 抓取失敗：%s", exc)
            return SourceStatus(status=SnapshotStatus.ERROR, error_message=str(exc))

        if not raw_rows:
            return SourceStatus(status=SnapshotStatus.NO_DATA)

        records = [self._to_broker_trade_record(snapshot_date, row) for row in raw_rows]
        self._storage.write_broker_trades(snapshot_date, records)
        return SourceStatus(status=SnapshotStatus.OK, fetched_at=self._now())

    def _fetch_etf_holdings(self, snapshot_date: str) -> SourceStatus:
        fetched_any = False
        last_error = None
        for etf_id in self._config.get_watchlist_etfs():
            try:
                raw_rows = self._twse_client.fetch_holdings(etf_id, snapshot_date)
            except Exception as exc:  # noqa: BLE001
                logger.warning("證交所 PCF 抓取失敗（%s）：%s", etf_id, exc)
                last_error = str(exc)
                continue
            if not raw_rows:
                continue
            records = [self._to_etf_holding_record(snapshot_date, etf_id, row) for row in raw_rows]
            self._storage.write_etf_holdings(snapshot_date, etf_id, records)
            fetched_any = True

        if fetched_any:
            return SourceStatus(status=SnapshotStatus.OK, fetched_at=self._now())
        if last_error:
            return SourceStatus(status=SnapshotStatus.ERROR, error_message=last_error)
        return SourceStatus(status=SnapshotStatus.NO_DATA)

    @staticmethod
    def _to_broker_trade_record(trade_date: str, row: dict) -> BrokerTradeRecord:
        buy_volume = int(row.get("buy", 0))
        sell_volume = int(row.get("sell", 0))
        return BrokerTradeRecord(
            trade_date=trade_date,
            stock_id=str(row.get("stock_id", "")),
            stock_name=str(row.get("stock_name", "")),
            broker_name=str(row.get("securities_trader", "")),
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            net_volume=buy_volume - sell_volume,
        )

    @staticmethod
    def _to_etf_holding_record(snapshot_date: str, etf_id: str, row: dict) -> EtfHoldingRecord:
        return EtfHoldingRecord(
            snapshot_date=snapshot_date,
            etf_id=etf_id,
            component_stock_id=str(row.get("component_stock_id", "")),
            component_name=str(row.get("component_name", "")),
            holding_shares=int(row.get("holding_shares", 0)),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
