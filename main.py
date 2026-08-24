"""每日籌碼監控推播引擎的進入點。

依序執行「抓取 -> 分析 -> 推播」；任何單一資料源或單一收訊者失敗都不會中斷整體流程，
只有設定檔錯誤、推播全數失敗或未預期例外才會讓程式以非 0 結束碼結束
（GitHub Actions 會因此寄出失敗通知信）。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime

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

# 使用者輸入日期收這三種常見格式；其餘一律視為輸入錯誤，不要讓錯的格式沒被擋下來，
# 一路流到 FinMind／各投信官網才各自用不同方式炸出一堆看似不相關的錯誤。
_DATE_INPUT_FORMATS = ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="籌碼監控推播引擎")
    parser.add_argument(
        "--date", default=None,
        help="指定執行日期，支援 YYYYMMDD、YYYY-MM-DD、YYYY/MM/DD 三種格式，預設為今日，供補跑使用",
    )
    parser.add_argument("--dry-run", action="store_true", help="只印出簡報內容，不實際呼叫 LINE 推播")
    return parser.parse_args(argv)


def _parse_target_date(date_str: str) -> str:
    """把使用者輸入的日期字串正規化成系統內部統一使用的 YYYY-MM-DD 格式；之後不管是查
    FinMind 或哪個投信官網，都是從這個統一格式再各自轉換（例如元大要去掉連字號、復華要
    換成斜線），呼叫端不需要猜使用者原始打的是哪一種格式。
    """
    for fmt in _DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"日期格式輸入錯誤：'{date_str}'（可用格式：YYYYMMDD、YYYY-MM-DD、YYYY/MM/DD）")


def run(target_date: str, dry_run: bool = False) -> bool:
    try:
        config = ConfigLoader()
    except ConfigError as exc:
        logger.error("設定檔錯誤，中止執行：%s", exc)
        return False

    storage = SnapshotRepository()
    fetcher = Fetcher(config, storage)

    if fetcher.is_known_trading_day(target_date) is False:
        # 交易日曆已經確認非交易日，不需要再白白抓一輪三大法人／各投信官網；非交易日本來
        # 就不會有任何有意義的資料，不管是不是 dry-run 都一樣，沒有理由為了「預覽」而白白
        # 多打一輪外部 API 只為了印出同一則空洞的「無達門檻標的」。
        logger.info("%s 經交易日曆確認為非交易日，略過本次抓取與分析", target_date)
        return True

    meta = fetcher.fetch_all(target_date)

    if not meta.is_trading_day:
        # 交易日曆查詢失敗或查不到答案時才會走到這裡：抓完資料後，如果三大法人／各投信
        # 官網當天都查無資料，一樣視為非交易日，同上理由不分 dry-run 一律略過。
        logger.info("%s 非交易日，略過分析與推播", target_date)
        return True

    market_alerts, stock_alerts, institutional_trades = _evaluate_institutional_alerts(config, storage, target_date)
    storage.write_institutional_alerts(target_date, market_alerts + stock_alerts)

    rebalance_events = _classify_rebalance_events(config, storage, fetcher, target_date)
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


def _classify_rebalance_events(
    config: ConfigLoader, storage: SnapshotRepository, fetcher: Fetcher, target_date: str
) -> list:
    prev_date = fetcher.resolve_backfill_trading_day(target_date)
    if prev_date is None:
        logger.info("%s 沒有可用的前一交易日資訊，略過 ETF 換倉比對（FETCH_ISSUER_PCF_NO_PREVIOUS_DAY）", target_date)
        return []

    classifier = RebalanceClassifier(config)
    events = []
    for etf_id in config.get_watchlist_etfs():
        curr_holdings = storage.read_etf_holdings(target_date, etf_id)
        if not curr_holdings:
            # 讀不到今天的持股檔案，代表這檔 ETF 今天沒抓到新資料（頁面尚未更新、解析失敗等），
            # 不是「真的變成 0 檔」；直接拿空清單去跟前一天比對的話，每一檔既有持股都會被
            # 誤判成「清倉」而整批推播出去，所以沒有新資料的當天直接跳過比對，不硬湊。
            _log_etf_event(config, logging.INFO, target_date, etf_id, "今日尚無持股資料，略過本次換倉比對")
            continue
        prev_holdings = fetcher.ensure_etf_holdings(etf_id, prev_date)
        if not prev_holdings:
            # 不論成因是投信不支援回補、還是支援但這次查無資料，一律視為同一種結果：
            # 沒有前一天資料可比，僅保留當日快照，不硬比出一批假的清倉/新建倉事件。
            _log_etf_event(
                config, logging.INFO, target_date, etf_id,
                f"沒有 {prev_date} 的前一交易日持股資訊，僅保留當日快照，略過換倉比對（FETCH_ISSUER_PCF_NO_PREVIOUS_DAY）",
            )
            continue
        etf_events = classifier.classify(etf_id, target_date, prev_holdings, curr_holdings)
        _log_etf_event(config, logging.INFO, target_date, etf_id, f"當前日期與前一交易日比較換倉 {len(etf_events)} 檔")
        events.extend(etf_events)
    return events


def _log_etf_event(config: ConfigLoader, level: int, target_date: str, etf_id: str, message: str) -> None:
    """統一 ETF 換倉比對相關 log 的格式（查詢日期／ETF 代碼／投信名稱 - 訊息），跟
    Fetcher 內部 PCF 抓取的 log 用同一套排版，方便掃 log 時一眼看出是哪天、哪檔 ETF、
    哪家投信發生的事。
    """
    issuer_name = config.get_issuer_name(etf_id)
    logger.log(level, "%s %s %s - %s", target_date, etf_id, issuer_name, message)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        target_date = _parse_target_date(args.date) if args.date else date.today().isoformat()
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    try:
        succeeded = run(target_date, dry_run=args.dry_run)
    except Exception:  # noqa: BLE001 - 進入點的最後防線，任何未預期例外都要被看見並記錄
        logger.exception("執行時發生未預期例外")
        return 1
    return 0 if succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
