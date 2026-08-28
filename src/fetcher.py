"""對接 FinMind API 與證交所 PCF API，抓取三大法人買賣超、成交量、股本、ETF 持股等資料。

任何單一資料源失敗（逾時、假日無資料、格式異常）都只會記錄下來，
不會讓整個抓取流程中斷；其餘可用資料仍照常寫入快照供後續分析使用。

分點買賣超（FinMindClient.fetch_broker_trades）維持原樣保留，但預設不會被呼叫，
由 ConfigLoader.is_broker_monitoring_enabled() 這個旗標決定；該資料集目前用的
dataset 名稱已知有誤且分點資料在 FinMind 免費層本來就拿不到，保留程式碼只是
避免以後真的要復用時要整個重寫。
"""
from __future__ import annotations

import dataclasses
import logging
import re
from collections.abc import Iterable
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
_BACKFILL_LOOKBACK_DAYS_MAX = 10  # 本地完全無歷史快照時，逐日輕量確認交易日的回溯天數上限（涵蓋農曆春節等長假）

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

    def fetch_stock_industry(self, stock_id: str) -> dict | None:
        """查詢單一股票的官方產業別／名稱，供分類標籤功能使用；查無資料回傳 None，
        呼叫失敗直接讓例外往外拋，由呼叫端決定要不要略過。
        """
        resp = self._get(dataset="TaiwanStockInfo", data_id=stock_id)
        rows = resp.get("data", [])
        if not rows:
            return None
        row = rows[-1]
        return {
            "stock_id": stock_id,
            "stock_name": row.get("stock_name", ""),
            "industry_category": row.get("industry_category", ""),
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

    def fetch_trading_dates(self, start_date: str, end_date: str) -> list[str]:
        """查詢區間內台股實際有開市的交易日清單（證交所行事曆本身已排除週末與國定假日），
        不需要像其餘資料集一樣逐股查詢，適合拿來單純確認某天是不是交易日。
        """
        resp = self._get(dataset="TaiwanStockTradingDate", start_date=start_date, end_date=end_date)
        return [row["date"] for row in resp.get("data", [])]

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

    def is_known_trading_day(self, target_date: str) -> bool | None:
        """在真正抓一輪三大法人／各投信官網之前，先跟 FinMind 的交易日曆確認 target_date
        是不是交易日，省下非交易日當天白白呼叫一輪外部 API 的成本。查詢本身失敗（逾時、
        API 異常等）時回傳 None，交由呼叫端退回原本「抓了資料才知道是不是交易日」的判斷
        方式，不能讓這個加速用的檢查本身變成整支程式能不能執行的單點故障。
        """
        try:
            trading_dates = self._finmind_client.fetch_trading_dates(target_date, target_date)
        except Exception as exc:  # noqa: BLE001 - 只是想提早判斷，查詢失敗就退回原本流程
            logger.warning("交易日曆查詢失敗，退回抓取後才判斷是否為交易日：%s", exc)
            return None
        return target_date in trading_dates

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

    def resolve_backfill_trading_day(self, target_date: str) -> str | None:
        """找出 target_date 的前一個交易日，供換倉比對使用。本地快照掃得到就直接採用；
        只有本地完全沒有任何歷史快照時（例如系統第一次真正執行、或前一天執行失敗未落地），
        才逐日輕量呼叫 FinMind 確認候選日期是否為交易日，找到就停，避免無界地往前掃描。
        """
        prev_date = self._storage.find_previous_trading_day(target_date)
        if prev_date is not None:
            return prev_date
        return self._probe_previous_trading_day(target_date)

    def _probe_previous_trading_day(self, target_date: str) -> str | None:
        watchlist_stocks = self._config.get_watchlist_stocks()
        if not watchlist_stocks:
            return None
        probe_stock = watchlist_stocks[0]

        candidate = datetime.fromisoformat(target_date).date()
        for _ in range(_BACKFILL_LOOKBACK_DAYS_MAX):
            candidate -= timedelta(days=1)
            candidate_str = candidate.isoformat()
            try:
                rows = self._finmind_client.fetch_institutional_trades(candidate_str, [probe_stock])
            except Exception as exc:  # noqa: BLE001 - 只是想確認是不是交易日，失敗就試下一天
                logger.warning("回補時輕量確認交易日失敗（%s）：%s", candidate_str, exc)
                continue
            if rows:
                return candidate_str

        logger.warning(
            "回補時逐日確認交易日已達上限（%d 天）仍找不到交易日，本次略過換倉比對"
            "（FETCH_ISSUER_PCF_NO_PREVIOUS_DAY）",
            _BACKFILL_LOOKBACK_DAYS_MAX,
        )
        return None

    def ensure_etf_holdings(self, etf_id: str, prev_date: str) -> list[dict]:
        """取得 etf_id 在 prev_date 這天的持股，供換倉比對使用。本地已有快照就直接讀；
        沒有的話，只有在對應投信官網經查證可安全帶入查詢日期時，才即時多打一次請求補回。
        不支援回補、補抓查無資料、逾時、或解析異常，一律回傳空清單，讓呼叫端當成
        「這次沒有前一天資料可比」處理，不需要區分實際成因。
        """
        existing = self._storage.read_etf_holdings(prev_date, etf_id)
        if existing:
            return existing

        provider = self._resolve_issuer_provider(etf_id)
        if not provider.SUPPORTS_BACKFILL:
            logger.info("%s 對應投信不支援查詢非當日資料，%s 這天沒有持股可比對", etf_id, prev_date)
            return []

        try:
            raw_rows = provider.fetch_holdings(etf_id, prev_date)
        except Exception as exc:  # noqa: BLE001 - 回補失敗不能拖累其他 ETF 或整體流程
            logger.warning("回補 %s 於 %s 的持股失敗：%s", etf_id, prev_date, exc)
            return []

        if not raw_rows:
            logger.info("%s 回補 %s 查無資料，這天沒有持股可比對", etf_id, prev_date)
            return []

        earlier_date = self._storage.find_previous_trading_day(prev_date)
        if self._is_holding_count_anomaly(etf_id, earlier_date, len(raw_rows)):
            logger.warning("%s 回補 %s 的持股筆數異常，判定為解析異常，這天沒有持股可比對", etf_id, prev_date)
            return []

        records = [self._to_etf_holding_record(prev_date, etf_id, row) for row in raw_rows]
        self._storage.write_etf_holdings(prev_date, etf_id, records)
        self._storage.upsert_meta_source(
            prev_date, DataSourceKey.ISSUER_PCF,
            SourceStatus(status=SnapshotStatus.OK, fetched_at=self._now()),
            is_trading_day=True,
        )
        return [dataclasses.asdict(r) for r in records]

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

        stock_names = self._resolve_stock_names(row["stock_id"] for row in raw_rows)
        records = [self._to_institutional_trade_record(row, stock_names) for row in raw_rows]
        self._storage.write_institutional_trades(snapshot_date, records)
        return SourceStatus(status=SnapshotStatus.OK, fetched_at=self._now())

    def _resolve_stock_names(self, stock_ids: Iterable[str]) -> dict[str, str]:
        """三大法人買賣超這支 API 本身不含股票中文名稱，需要另外查。優先沿用
        ClassificationService 已經累積在 industry_tags.json 裡的名稱，避免重複打 FinMind；
        本地沒有的才即時查一次，單一股票查詢失敗就跳過，不能讓名稱查詢拖累整批資料。
        """
        known = self._known_stock_names(self._storage.read_industry_tags())
        names: dict[str, str] = {}
        for stock_id in dict.fromkeys(stock_ids):  # 去重，同時保留原始出現順序
            if stock_id in known:
                names[stock_id] = known[stock_id]
                continue
            try:
                info = self._finmind_client.fetch_stock_industry(stock_id)
            except Exception as exc:  # noqa: BLE001 - 名稱查詢失敗不能拖累其他股票或整批資料
                logger.warning("股票名稱查詢失敗（%s）：%s", stock_id, exc)
                continue
            if info and info.get("stock_name"):
                names[stock_id] = info["stock_name"]
        return names

    @staticmethod
    def _known_stock_names(industry_tags: dict) -> dict[str, str]:
        names = {}
        for entry in industry_tags.values():
            for member in entry.get("members", []):
                names[member["stock_id"]] = member["stock_name"]
        return names

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
        prev_date = self._storage.find_previous_trading_day(snapshot_date)
        fetched_any = False
        last_error = None
        for etf_id in self._config.get_watchlist_etfs():
            try:
                provider = self._resolve_issuer_provider(etf_id)
                raw_rows = provider.fetch_holdings(etf_id, snapshot_date)
            except Exception as exc:  # noqa: BLE001 - 單一投信失敗不能讓其他 ETF 抓不到
                self._log_etf_pcf(logging.WARNING, snapshot_date, etf_id, f"PCF 抓取失敗：{exc}")
                last_error = str(exc)
                continue
            if not raw_rows:
                # 投信 Adapter 內部已經記過各自的詳細原因（例如頁面日期還沒更新到今天、
                # 找不到對應內部代碼），這裡只補一行統一格式的結果彙總，方便掃 log。
                self._log_etf_pcf(logging.INFO, snapshot_date, etf_id, "今日尚無持股資料，略過本次抓取")
                continue
            if self._is_holding_count_anomaly(etf_id, prev_date, len(raw_rows)):
                message = "持股筆數異常驟降，判定為解析異常，本次不採用（FETCH_ISSUER_PCF_ANOMALY_DETECTED）"
                self._log_etf_pcf(logging.WARNING, snapshot_date, etf_id, message)
                last_error = f"{etf_id} {message}"
                continue
            records = [self._to_etf_holding_record(snapshot_date, etf_id, row) for row in raw_rows]
            self._storage.write_etf_holdings(snapshot_date, etf_id, records)
            self._log_etf_pcf(logging.INFO, snapshot_date, etf_id, f"PCF 抓取成功，共 {len(records)} 檔持股")
            fetched_any = True

        if fetched_any:
            return SourceStatus(status=SnapshotStatus.OK, fetched_at=self._now())
        if last_error:
            return SourceStatus(status=SnapshotStatus.ERROR, error_message=last_error)
        return SourceStatus(status=SnapshotStatus.NO_DATA)

    def _log_etf_pcf(self, level: int, snapshot_date: str, etf_id: str, message: str) -> None:
        """統一 ETF PCF 抓取相關 log 的格式（查詢日期／ETF 代碼／投信名稱 - 訊息），
        方便掃 log 時一眼看出是哪天、哪檔 ETF、哪家投信發生的事。
        """
        issuer_name = self._config.get_issuer_name(etf_id)
        logger.log(level, "%s %s %s - %s", snapshot_date, etf_id, issuer_name, message)

    def _is_holding_count_anomaly(self, etf_id: str, prev_date: str | None, curr_count: int) -> bool:
        """投信網站局部改版時，Adapter 通常不會直接拋例外，而是靜靜解析出殘缺的持股清單
        （例如原本 40 檔只剩 3 檔）。這種資料如果照樣存進快照，Analyzer 會把「消失的 37 檔」
        當成真實的清倉事件推播出去，所以在寫入前先跟前一交易日的筆數比一下，跌幅太誇張就
        視為解析異常、這次不採用，而不是照單全收。
        """
        if prev_date is None:
            return False  # 沒有前一天快照可比對（例如剛加入監控的新 ETF），沒有基準就不誤判
        try:
            prev_count = len(self._storage.read_etf_holdings(prev_date, etf_id))
        except ValueError:
            # 前一天的快照檔案本身壞掉（JSON 格式異常）時沒有基準可比對，這只是一個健全性
            # 檢查用的輔助讀取，不能讓它把整個 fetch_all() 都拖垮，直接視為沒有基準、不擋。
            logger.warning("%s 前一交易日持股快照格式異常，健全性檢查本次略過比對", etf_id)
            return False
        if prev_count == 0:
            return False
        drop_pct = (prev_count - curr_count) / prev_count * 100
        threshold = self._config.get_etf_holding_count_drop_pct_threshold()
        if drop_pct < threshold:
            return False
        logger.warning(
            "%s 持股筆數從 %d 檔驟降至 %d 檔（跌幅 %.1f%%，達異常門檻 %.1f%%），判定為解析異常，本次不採用",
            etf_id, prev_count, curr_count, drop_pct, threshold,
        )
        return True

    def _resolve_issuer_provider(self, etf_id: str) -> IssuerPcfProvider:
        if etf_id in self._issuer_providers:
            return self._issuer_providers[etf_id]
        mapping = self._config.get_issuer_mapping(etf_id)
        adapter_cls = ADAPTER_REGISTRY[mapping["adapter"]]
        return adapter_cls()

    @staticmethod
    def _to_institutional_trade_record(row: dict, stock_names: dict[str, str]) -> InstitutionalTradeRecord:
        stock_id = row["stock_id"]
        return InstitutionalTradeRecord(
            trade_date=row["trade_date"],
            stock_id=stock_id,
            stock_name=stock_names.get(stock_id, stock_id),
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
