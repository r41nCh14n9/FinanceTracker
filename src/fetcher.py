"""對接 FinMind API 與證交所 PCF API，抓取三大法人買賣超、成交量、股本、ETF 持股等資料。

任何單一資料源失敗（逾時、假日無資料、格式異常）都只會記錄下來，
不會讓整個抓取流程中斷；其餘可用資料仍照常寫入快照供後續分析使用。

分點買賣超（FinMindClient.fetch_broker_trades）維持原樣保留，但預設不會被呼叫，
由 ConfigLoader.is_broker_monitoring_enabled() 這個旗標決定；該資料集目前用的
dataset 名稱已知有誤且分點資料在 FinMind 免費層本來就拿不到，保留程式碼只是
避免以後真的要復用時要整個重寫。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import requests

from src.config import ConfigLoader
from src.issuer_pcf.base import IssuerPcfProvider
from src.issuer_pcf.registry import ADAPTER_REGISTRY
from src.models import (
    BrokerTradeRecord,
    DailySnapshotMeta,
    DataSourceKey,
    EtfHoldingRecord,
    InstitutionalTradeRecord,
    MarketInstitutionalRecord,
    SnapshotStatus,
    SourceStatus,
    StockCapitalSnapshot,
    StockDailyTrading,
)
from src.storage import SnapshotRepository

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30
_PAR_VALUE = 10  # 台股面額多為 10 元，估算發行股數＝股本 ÷ 面額
_CAPITAL_STOCK_LOOKBACK_DAYS = 400  # 抓股本時往回查的天數，確保能撈到最近一次公告的財報
_CAPITAL_STOCK_CACHE_TTL_DAYS = 90  # 股本快取視為新鮮的天數，季更新資料不需要每天重打 API

# requests 的例外訊息預設會帶完整請求 URL，裡面含明文 token；一律先過濾掉再往外拋，
# 避免這段訊息被存進 _meta.json 後又被排程流程 commit 進版控。
_TOKEN_MASK_PATTERN = re.compile(r"(token=)[^&\s]+")

# 判斷「今天是不是交易日」只看跟當日市場活動有關的來源；股本是季更新的靜態參考資料，
# 快取命中時一律回傳 OK 跟當天有沒有開盤完全無關，不能算進來，否則假日會被誤判成交易日。
_TRADING_DAY_SOURCES = frozenset({
    DataSourceKey.FINMIND_INSTITUTIONAL,
    DataSourceKey.FINMIND_PRICE,
    DataSourceKey.FINMIND_MARKET,
    DataSourceKey.FINMIND_BROKER,
    DataSourceKey.ISSUER_PCF,
})

# TaiwanStockInstitutionalInvestorsBuySell / TaiwanStockTotalInstitutionalInvestors
# 都不含股票中文名稱欄位，暫時用這張表頂著；監控清單擴增時要換成動態查詢（見 IMPL 文件待辦）。
_STOCK_NAME_FALLBACK = {"2330": "台積電", "2454": "聯發科"}


class FinMindClient:
    """FinMind API 的薄封裝，只回傳這個系統需要的欄位。"""

    _BASE_URL = "https://api.finmindtrade.com/api/v4/data"

    def __init__(self, token: str):
        self._token = token

    def fetch_broker_trades(self, trade_date: str, stock_ids: list[str], broker_names: list[str]) -> list[dict]:
        records = []
        for stock_id in stock_ids:
            try:
                resp = self._get(
                    dataset="TaiwanStockTradingDailyReportSecIdAgg",
                    data_id=stock_id,
                    start_date=trade_date,
                    end_date=trade_date,
                )
            except Exception as exc:  # noqa: BLE001 - 單一股票查詢失敗不能拖累其他股票
                logger.warning("分點買賣超查詢失敗（%s）：%s", stock_id, exc)
                continue
            rows = resp.get("data", [])
            records.extend(row for row in rows if row.get("securities_trader") in broker_names)
        return records

    def fetch_institutional_trades(self, trade_date: str, stock_ids: list[str]) -> list[dict]:
        """逐股查詢三大法人買賣超，回傳每股一筆已彙整五個法人類別的字典。"""
        records = []
        for stock_id in stock_ids:
            try:
                resp = self._get(
                    dataset="TaiwanStockInstitutionalInvestorsBuySell",
                    data_id=stock_id,
                    start_date=trade_date,
                    end_date=trade_date,
                )
            except Exception as exc:  # noqa: BLE001 - 單一股票查詢失敗不能拖累其他股票
                logger.warning("三大法人買賣超查詢失敗（%s）：%s", stock_id, exc)
                continue
            rows = resp.get("data", [])
            if rows:
                records.append(self._aggregate_institutional_rows(stock_id, trade_date, rows))
        return records

    def fetch_stock_trading(self, trade_date: str, stock_ids: list[str]) -> list[dict]:
        """逐股查詢當日成交量與收盤價。"""
        records = []
        for stock_id in stock_ids:
            try:
                resp = self._get(
                    dataset="TaiwanStockPrice",
                    data_id=stock_id,
                    start_date=trade_date,
                    end_date=trade_date,
                )
            except Exception as exc:  # noqa: BLE001 - 單一股票查詢失敗不能拖累其他股票
                logger.warning("成交量/收盤價查詢失敗（%s）：%s", stock_id, exc)
                continue
            rows = resp.get("data", [])
            if rows:
                row = rows[-1]
                records.append({
                    "trade_date": trade_date,
                    "stock_id": stock_id,
                    "trading_volume": int(row.get("Trading_Volume", 0)),
                    "close_price": float(row.get("close", 0)),
                })
        return records

    def fetch_capital_stock(self, stock_id: str, as_of_date: str) -> dict | None:
        """查詢股本，取回溯期間內最新一筆 CapitalStock 科目；查無資料回傳 None。"""
        start_date = (
            datetime.fromisoformat(as_of_date) - timedelta(days=_CAPITAL_STOCK_LOOKBACK_DAYS)
        ).date().isoformat()
        resp = self._get(
            dataset="TaiwanStockBalanceSheet",
            data_id=stock_id,
            start_date=start_date,
            end_date=as_of_date,
        )
        capital_rows = [row for row in resp.get("data", []) if row.get("type") == "CapitalStock"]
        if not capital_rows:
            return None

        latest = max(capital_rows, key=lambda row: row["date"])
        capital_stock = int(latest["value"])
        return {
            "stock_id": stock_id,
            "report_date": latest["date"],
            "capital_stock": capital_stock,
            "estimated_shares": capital_stock // _PAR_VALUE,
        }

    def fetch_market_institutional(self, trade_date: str) -> dict | None:
        """查詢大盤三大法人買賣金額，不帶 data_id，每次執行只需要呼叫一次。"""
        resp = self._get(
            dataset="TaiwanStockTotalInstitutionalInvestors",
            start_date=trade_date,
            end_date=trade_date,
        )
        rows = [row for row in resp.get("data", []) if row.get("date") == trade_date]
        if not rows:
            return None
        return self._aggregate_market_rows(trade_date, rows)

    def _get(self, **params: str) -> dict:
        try:
            resp = requests.get(
                self._BASE_URL,
                params={**params, "token": self._token},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            # 用 from None 徹底切斷與原始例外的關聯，避免帶 token 的原始訊息透過
            # traceback chain（例如未來有人呼叫 logger.exception）被間接印出來。
            raise RuntimeError(_TOKEN_MASK_PATTERN.sub(r"\1***", str(exc))) from None

    @staticmethod
    def _aggregate_institutional_rows(stock_id: str, trade_date: str, rows: list[dict]) -> dict:
        by_name = {row["name"]: row for row in rows}

        def net(name: str) -> int:
            row = by_name.get(name, {})
            return int(row.get("buy", 0)) - int(row.get("sell", 0))

        foreign_investor = by_name.get("Foreign_Investor", {})
        investment_trust = by_name.get("Investment_Trust", {})
        foreign_investor_buy = int(foreign_investor.get("buy", 0))
        foreign_investor_sell = int(foreign_investor.get("sell", 0))
        foreign_dealer_self_net = net("Foreign_Dealer_Self")
        investment_trust_buy = int(investment_trust.get("buy", 0))
        investment_trust_sell = int(investment_trust.get("sell", 0))
        dealer_self_net = net("Dealer_self")
        dealer_hedging_net = net("Dealer_Hedging")

        total_net = (
            (foreign_investor_buy - foreign_investor_sell)
            + foreign_dealer_self_net
            + (investment_trust_buy - investment_trust_sell)
            + dealer_self_net
            + dealer_hedging_net
        )
        return {
            "stock_id": stock_id,
            "trade_date": trade_date,
            "foreign_investor_buy": foreign_investor_buy,
            "foreign_investor_sell": foreign_investor_sell,
            "foreign_dealer_self_net": foreign_dealer_self_net,
            "investment_trust_buy": investment_trust_buy,
            "investment_trust_sell": investment_trust_sell,
            "dealer_self_net": dealer_self_net,
            "dealer_hedging_net": dealer_hedging_net,
            "total_net": total_net,
        }

    @staticmethod
    def _aggregate_market_rows(trade_date: str, rows: list[dict]) -> dict:
        by_name = {row["name"]: row for row in rows}

        def net(name: str) -> int:
            row = by_name.get(name, {})
            return int(row.get("buy", 0)) - int(row.get("sell", 0))

        return {
            "trade_date": trade_date,
            "foreign_net_amount": net("Foreign_Investor") + net("Foreign_Dealer_Self"),
            "trust_net_amount": net("Investment_Trust"),
            "dealer_net_amount": net("Dealer_self") + net("Dealer_Hedging"),
        }


class Fetcher:
    def __init__(
        self,
        config: ConfigLoader,
        storage: SnapshotRepository,
        finmind_client: FinMindClient | None = None,
        issuer_providers: dict[str, IssuerPcfProvider] | None = None,
    ):
        self._config = config
        self._storage = storage
        self._finmind_client = finmind_client or FinMindClient(config.get_env("FINMIND_TOKEN"))
        # 依 ETF 代碼覆寫要用哪個 provider，主要給測試用假物件取代真正的爬蟲；
        # 沒被覆寫的 ETF 一律依設定檔查 ADAPTER_REGISTRY 動態決定。
        self._issuer_providers = issuer_providers or {}

    def fetch_all(self, snapshot_date: str) -> DailySnapshotMeta:
        sources: dict[str, SourceStatus] = {
            DataSourceKey.FINMIND_INSTITUTIONAL: self._fetch_institutional_trades(snapshot_date),
            DataSourceKey.FINMIND_PRICE: self._fetch_stock_trading(snapshot_date),
            DataSourceKey.FINMIND_BALANCE_SHEET: self._fetch_capital_stock(snapshot_date),
            DataSourceKey.FINMIND_MARKET: self._fetch_market_institutional(snapshot_date),
            DataSourceKey.ISSUER_PCF: self._fetch_etf_holdings(snapshot_date),
        }
        if self._config.is_broker_monitoring_enabled():
            sources[DataSourceKey.FINMIND_BROKER] = self._fetch_broker_trades(snapshot_date)

        meta = DailySnapshotMeta(
            snapshot_date=snapshot_date,
            sources=sources,
            is_trading_day=any(
                status.status == SnapshotStatus.OK
                for key, status in sources.items()
                if key in _TRADING_DAY_SOURCES
            ),
        )
        self._storage.write_meta(meta)
        return meta

    def _fetch_institutional_trades(self, snapshot_date: str) -> SourceStatus:
        try:
            raw_rows = self._finmind_client.fetch_institutional_trades(
                snapshot_date, self._config.get_watchlist_stocks()
            )
        except Exception as exc:  # noqa: BLE001 - 單一來源失敗不能讓整體流程中斷
            logger.warning("FinMind 三大法人買賣超抓取失敗：%s", exc)
            return SourceStatus(status=SnapshotStatus.ERROR, error_message=str(exc))

        if not raw_rows:
            return SourceStatus(status=SnapshotStatus.NO_DATA)

        records = [self._to_institutional_trade_record(row) for row in raw_rows]
        self._storage.write_institutional_trades(snapshot_date, records)
        return SourceStatus(status=SnapshotStatus.OK, fetched_at=self._now())

    def _fetch_stock_trading(self, snapshot_date: str) -> SourceStatus:
        try:
            raw_rows = self._finmind_client.fetch_stock_trading(
                snapshot_date, self._config.get_watchlist_stocks()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("FinMind 成交量/收盤價抓取失敗：%s", exc)
            return SourceStatus(status=SnapshotStatus.ERROR, error_message=str(exc))

        if not raw_rows:
            return SourceStatus(status=SnapshotStatus.NO_DATA)

        records = [StockDailyTrading(**row) for row in raw_rows]
        self._storage.write_stock_trading(snapshot_date, records)
        return SourceStatus(status=SnapshotStatus.OK, fetched_at=self._now())

    def _fetch_capital_stock(self, snapshot_date: str) -> SourceStatus:
        any_success = False
        last_error = None
        for stock_id in self._config.get_watchlist_stocks():
            if self._is_capital_stock_cache_fresh(stock_id):
                any_success = True
                continue
            try:
                data = self._finmind_client.fetch_capital_stock(stock_id, snapshot_date)
            except Exception as exc:  # noqa: BLE001
                logger.warning("FinMind 股本抓取失敗（%s）：%s", stock_id, exc)
                last_error = str(exc)
                continue
            if data is None:
                has_old_cache = self._storage.read_capital_stock_cache(stock_id) is not None
                logger.warning(
                    "FinMind 股本查無資料（%s），%s",
                    stock_id,
                    "沿用舊快取" if has_old_cache else "且無舊快取可沿用，市值分級這次會失效",
                )
                any_success = any_success or has_old_cache
                continue
            self._storage.write_capital_stock_cache(
                StockCapitalSnapshot(
                    stock_id=data["stock_id"],
                    report_date=data["report_date"],
                    capital_stock=data["capital_stock"],
                    estimated_shares=data["estimated_shares"],
                    fetched_at=self._now(),
                )
            )
            any_success = True

        if any_success:
            return SourceStatus(status=SnapshotStatus.OK, fetched_at=self._now())
        if last_error:
            return SourceStatus(status=SnapshotStatus.ERROR, error_message=last_error)
        return SourceStatus(status=SnapshotStatus.NO_DATA)

    def _is_capital_stock_cache_fresh(self, stock_id: str) -> bool:
        cached = self._storage.read_capital_stock_cache(stock_id)
        if not cached:
            return False
        try:
            fetched_at = datetime.fromisoformat(cached["fetched_at"])
        except (KeyError, TypeError, ValueError):
            # 快取檔案格式異常（人為誤改、寫入中斷等），視同「不新鮮」重新抓一次就好，
            # 不能讓格式問題直接把整個 fetch_all() 炸掉。
            logger.warning("股本快取格式異常（%s），視為過期重新抓取", stock_id)
            return False
        age_days = (datetime.now(timezone.utc) - fetched_at).days
        return age_days < _CAPITAL_STOCK_CACHE_TTL_DAYS

    def _fetch_market_institutional(self, snapshot_date: str) -> SourceStatus:
        try:
            data = self._finmind_client.fetch_market_institutional(snapshot_date)
        except Exception as exc:  # noqa: BLE001
            logger.warning("FinMind 大盤三大法人買賣金額抓取失敗：%s", exc)
            return SourceStatus(status=SnapshotStatus.ERROR, error_message=str(exc))

        if data is None:
            return SourceStatus(status=SnapshotStatus.NO_DATA)

        self._storage.write_market_institutional(snapshot_date, MarketInstitutionalRecord(**data))
        return SourceStatus(status=SnapshotStatus.OK, fetched_at=self._now())

    def _fetch_broker_trades(self, snapshot_date: str) -> SourceStatus:
        try:
            raw_rows = self._finmind_client.fetch_broker_trades(
                snapshot_date,
                self._config.get_watchlist_stocks(),
                self._config.get_watchlist_brokers(),
            )
        except Exception as exc:  # noqa: BLE001 - 單一來源失敗不能讓整體流程中斷
            logger.warning("FinMind 分點買賣超抓取失敗：%s", exc)
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
                provider = self._resolve_issuer_provider(etf_id)
                raw_rows = provider.fetch_holdings(etf_id, snapshot_date)
            except Exception as exc:  # noqa: BLE001 - 單一投信失敗不能讓其他 ETF 抓不到
                logger.warning("投信官網 PCF 抓取失敗（%s）：%s", etf_id, exc)
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

    def _resolve_issuer_provider(self, etf_id: str) -> IssuerPcfProvider:
        if etf_id in self._issuer_providers:
            return self._issuer_providers[etf_id]
        mapping = self._config.get_issuer_mapping(etf_id)
        adapter_cls = ADAPTER_REGISTRY[mapping["adapter"]]
        return adapter_cls()

    def _to_institutional_trade_record(self, row: dict) -> InstitutionalTradeRecord:
        return InstitutionalTradeRecord(
            trade_date=row["trade_date"],
            stock_id=row["stock_id"],
            stock_name=_STOCK_NAME_FALLBACK.get(row["stock_id"], row["stock_id"]),
            foreign_investor_buy=row["foreign_investor_buy"],
            foreign_investor_sell=row["foreign_investor_sell"],
            foreign_dealer_self_net=row["foreign_dealer_self_net"],
            investment_trust_buy=row["investment_trust_buy"],
            investment_trust_sell=row["investment_trust_sell"],
            dealer_self_net=row["dealer_self_net"],
            dealer_hedging_net=row["dealer_hedging_net"],
            total_net=row["total_net"],
        )

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
