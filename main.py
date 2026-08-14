"""每日籌碼監控推播引擎的進入點。

依序執行「抓取 -> 分析 -> 推播」；任何單一資料源或單一收訊者失敗都不會中斷整體流程，
只有設定檔錯誤、推播全數失敗或未預期例外才會讓程式以非 0 結束碼結束
（GitHub Actions 會因此寄出失敗通知信）。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from src.analyzer import InstitutionalTieredFilter, MarketInstitutionalFilter, RebalanceClassifier
from src.config import ConfigError, ConfigLoader
from src.fetcher import Fetcher
from src.notifier import MessageFormatter, Notifier
from src.storage import SnapshotRepository


def _ensure_utf8_output() -> None:
    """有些主控台（尤其 Windows 中文系統）預設輸出編碼不是 UTF-8，直接印中文簡報
    不只會顯示亂碼，重導向到檔案時內容還會真的被寫壞；這裡強制改用 UTF-8 輸出，
    不支援 reconfigure 的環境（例如測試框架接管過 stdout）就靜靜跳過。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


_ensure_utf8_output()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="籌碼監控推播引擎")
    parser.add_argument("--date", default=None, help="指定執行日期（YYYY-MM-DD），預設為今日，供補跑使用")
    parser.add_argument("--dry-run", action="store_true", help="只印出簡報內容，不實際呼叫 LINE 推播")
    return parser.parse_args(argv)


def run(target_date: str, dry_run: bool = False) -> bool:
    try:
        config = ConfigLoader()
    except ConfigError as exc:
        logger.error("設定檔錯誤，中止執行：%s", exc)
        return False

    storage = SnapshotRepository()
    meta = Fetcher(config, storage).fetch_all(target_date)

    if not meta.is_trading_day and not dry_run:
        # 非交易日（國定假日剛好落在平日）不會有任何有意義的資料，硬要推播只會是
        # 一則空洞的「無達門檻標的」，白白浪費 LINE 月配額；dry-run 不受此限，
        # 方便開發時想預覽任何日期的簡報長相。
        logger.info("%s 非交易日，略過分析與推播", target_date)
        return True

    market_alerts, stock_alerts, institutional_trades = _evaluate_institutional_alerts(config, storage, target_date)
    storage.write_institutional_alerts(target_date, market_alerts + stock_alerts)

    rebalance_events = _classify_rebalance_events(config, storage, target_date)
    storage.write_rebalance_events(target_date, rebalance_events)

    if dry_run:
        messages = MessageFormatter().format(
            target_date, market_alerts, stock_alerts, institutional_trades, rebalance_events
        )
        for i, message in enumerate(messages, start=1):
            print(f"========== 訊息 {i}/{len(messages)} ==========")
            print(message)
        return True

    return Notifier(config, storage).notify(
        target_date, market_alerts, stock_alerts, institutional_trades, rebalance_events
    )


def _evaluate_institutional_alerts(
    config: ConfigLoader, storage: SnapshotRepository, target_date: str
) -> tuple[list, list, list[dict]]:
    institutional_trades = storage.read_institutional_trades(target_date)
    stock_trading = storage.read_stock_trading(target_date)
    stock_alerts = InstitutionalTieredFilter(config, storage).filter_significant_trades(
        institutional_trades, stock_trading
    )

    market_record = storage.read_market_institutional(target_date)
    market_alerts = (
        MarketInstitutionalFilter(config).filter_significant_trades(market_record) if market_record else []
    )
    return market_alerts, stock_alerts, institutional_trades


def _classify_rebalance_events(config: ConfigLoader, storage: SnapshotRepository, target_date: str) -> list:
    prev_date = storage.find_previous_trading_day(target_date)
    if prev_date is None:
        logger.info("找不到前一交易日快照（可能為首次執行），略過 ETF 換倉比對")
        return []

    classifier = RebalanceClassifier(config)
    events = []
    for etf_id in config.get_watchlist_etfs():
        prev_holdings = storage.read_etf_holdings(prev_date, etf_id)
        curr_holdings = storage.read_etf_holdings(target_date, etf_id)
        events.extend(classifier.classify(etf_id, target_date, prev_holdings, curr_holdings))
    return events


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    target_date = args.date or date.today().isoformat()
    try:
        succeeded = run(target_date, dry_run=args.dry_run)
    except Exception:  # noqa: BLE001 - 進入點的最後防線，任何未預期例外都要被看見並記錄
        logger.exception("執行時發生未預期例外")
        return 1
    return 0 if succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
