"""每日籌碼監控推播引擎的進入點。

依序執行「抓取 -> 分析 -> 產出報告 -> 推播」；任何單一資料源或單一收訊者失敗都不會中斷
整體流程，只有設定檔錯誤、推播全數失敗或未預期例外才會讓程式以非 0 結束碼結束
（GitHub Actions 會因此寄出失敗通知信）。

推播可以跟抓取/分析拆成兩段獨立執行（--skip-notify 只做到產出報告、--notify-only
讀回既有分析結果重新格式化並推播），讓排程可以先把當天的資料 commit 回版控、
確定報告檔案已經在 GitHub 上之後，才推播帶有連結的訊息；不拆分時（無旗標）維持
單次跑完全部流程的既有行為，供本機手動測試使用。

帶 --purge 執行時完全是另一條路：只清舊快照/報告目錄，不碰抓取/分析/推播，
三者互不影響，排程只是依序各跑一次。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime

from src.analyzer import InstitutionalTieredFilter, MarketInstitutionalFilter, RebalanceClassifier
from src.classification import ClassificationService, invert_category_table
from src.config import ConfigError, ConfigLoader
from src.fetcher import Fetcher, FinMindClient
from src.limit_scanner import LimitScanner
from src.link_publisher import LinkPublisher
from src.models import AlertScope, InstitutionalAlert, LimitUpDownRecord, PurgeResult, RebalanceEvent
from src.notifier import MessageFormatter, Notifier
from src.report_generator import ReportGenerator
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
    parser.add_argument("--dry-run", action="store_true", help="只印出簡報內容，不實際呼叫 LINE 推播；與 --purge 併用時只預覽會清除哪些目錄")
    parser.add_argument(
        "--purge", action="store_true",
        help="只執行快照/報告保留清除（依 thresholds.json 設定之保留天數），不執行抓取/分析/推播；此模式下 --date 會被忽略，清除截止日一律以執行當下日期為準",
    )

    notify_mode = parser.add_mutually_exclusive_group()
    notify_mode.add_argument(
        "--skip-notify", action="store_true",
        help="抓取/分析/產出完整版報告，但不推播 LINE；供排程先完成版控回寫，再另外呼叫 --notify-only 推播",
    )
    notify_mode.add_argument(
        "--notify-only", action="store_true",
        help="不重新抓取，讀回既有快照/報告資料格式化並推播 LINE；搭配 --date 指定要重播哪一天",
    )
    parser.add_argument(
        "--report-url", default=None,
        help="完整版報告的 GitHub 網址，僅 --notify-only 時有作用；有帶值會先縮網址再附加到推播訊息末尾",
    )
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


def run(target_date: str, dry_run: bool = False, skip_notify: bool = False) -> bool:
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

    industry_map, concept_map = _resolve_classification_tags(config, storage, stock_alerts, rebalance_events)

    limit_records = _scan_limit_up_down(storage, target_date)
    limit_institutional_trades = _fetch_limit_institutional_trades(config, storage, target_date, limit_records)
    _write_daily_report(
        storage, target_date, institutional_trades, stock_alerts, limit_records,
        limit_institutional_trades, rebalance_events, industry_map, concept_map,
    )

    if skip_notify:
        return True

    if dry_run:
        messages = MessageFormatter().format(
            target_date, market_alerts, stock_alerts, institutional_trades, rebalance_events,
            industry_map, concept_map,
        )
        _print_messages(messages)
        return True

    return Notifier(config, storage).notify(
        target_date, market_alerts, stock_alerts, institutional_trades, rebalance_events,
        industry_map, concept_map,
    )


def run_notify_only(target_date: str, report_url: str | None, dry_run: bool = False) -> bool:
    """不重新抓取，讀回既有快照/報告資料重新格式化並推播，供排程在 commit/push 完成、
    確定完整版報告已經在 GitHub 上之後才呼叫，這時才有辦法附上有效的連結。
    """
    try:
        config = ConfigLoader()
    except ConfigError as exc:
        logger.error("設定檔錯誤，中止執行：%s", exc)
        return False

    storage = SnapshotRepository()
    institutional_trades = storage.read_institutional_trades(target_date)
    alerts = storage.read_institutional_alerts(target_date)
    if not institutional_trades and not alerts:
        # 這兩份檔案都是分析階段一定會寫的（即使當天完全沒有達標項目，institutional_trades
        # 至少也會有 watchlist 全量資料），全部找不到代表當天根本沒跑過分析，不是「今天剛好
        # 沒有任何異動」這種正常情況，不該假裝成功推播一份空白訊息出去。
        logger.error("%s 查無既有分析結果，請先執行 main.py --date %s --skip-notify", target_date, target_date)
        return False

    market_alerts = [a for a in alerts if a.scope == AlertScope.MARKET]
    stock_alerts = [a for a in alerts if a.scope == AlertScope.STOCK]
    rebalance_events = storage.read_rebalance_events(target_date)
    industry_map, concept_map = _resolve_classification_tags(config, storage, stock_alerts, rebalance_events)

    report_link = LinkPublisher().shorten(report_url) if report_url else None

    if dry_run:
        messages = MessageFormatter().format(
            target_date, market_alerts, stock_alerts, institutional_trades, rebalance_events,
            industry_map, concept_map, report_link,
        )
        _print_messages(messages)
        return True

    return Notifier(config, storage).notify(
        target_date, market_alerts, stock_alerts, institutional_trades, rebalance_events,
        industry_map, concept_map, report_link,
    )


def _print_messages(messages: list[str]) -> None:
    for i, message in enumerate(messages, start=1):
        print(f"========== 訊息 {i}/{len(messages)} ==========")
        print(message)


def run_purge(dry_run: bool = False) -> bool:
    """清掉太舊的快照/報告目錄，跟抓取/分析/推播完全脫鉤，可以獨立執行。清除截止日
    一律以執行當下的日期回推保留天數計算，不吃 --date（清除的是「多久以前」的資料，
    跟這次要不要補跑某個特定日期的分析無關）。
    """
    try:
        config = ConfigLoader()
    except ConfigError as exc:
        logger.error("設定檔錯誤，中止執行：%s", exc)
        return False

    storage = SnapshotRepository()
    retention_days = config.get_snapshot_retention_days()
    result = storage.purge_expired(retention_days, date.today(), dry_run=dry_run)
    _log_purge_result(result, dry_run)
    return not result.failed


def _log_purge_result(result: PurgeResult, dry_run: bool) -> None:
    preview_note = "（dry-run，僅預覽不刪除）" if dry_run else ""
    for path in result.deleted:
        logger.info("清除快照/報告目錄%s：%s", preview_note, path)
    for path in result.skipped_invalid_format:
        logger.warning("目錄名稱非合法日期格式，略過不處理：%s", path)
    for path, error in result.failed:
        logger.warning("清除失敗，略過：%s（%s）", path, error)

    logger.info(
        "快照保留清除完成%s：截止日 %s，清除 %d 個、略過 %d 個、失敗 %d 個",
        preview_note, result.cutoff_date, len(result.deleted), len(result.skipped_invalid_format), len(result.failed),
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


def _resolve_classification_tags(
    config: ConfigLoader,
    storage: SnapshotRepository,
    stock_alerts: list[InstitutionalAlert],
    rebalance_events: list[RebalanceEvent],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """發送通知前，把本次通知會用到的股票（達門檻個股 + ETF 換倉成分股）補齊產業分類，
    本地已有的不重打 FinMind；概念標籤則單純讀取人工維護的設定檔，不涉及任何查詢。
    """
    needed_stock_ids = sorted({a.stock_id for a in stock_alerts} | {e.component_stock_id for e in rebalance_events})
    finmind_client = FinMindClient(config.get_env("FINMIND_TOKEN"))
    industry_map = ClassificationService(finmind_client, storage).ensure_industry_categories(needed_stock_ids)
    concept_map = invert_category_table(config.get_concept_tags())
    return industry_map, concept_map


def _scan_limit_up_down(storage: SnapshotRepository, target_date: str) -> list[LimitUpDownRecord]:
    """掃描全市場（上市＋上櫃）當日觸及漲跌停的股票並落地存檔；單一市場查詢失敗只會讓
    該市場當次沒有資料，不影響另一個市場或其餘流程（LimitScanner 內部已處理）。
    """
    records = LimitScanner().scan(target_date)
    storage.write_limit_up_down(target_date, records)
    return records


def _fetch_limit_institutional_trades(
    config: ConfigLoader, storage: SnapshotRepository, target_date: str, limit_records: list[LimitUpDownRecord]
) -> dict[str, dict]:
    """漲跌停股的三大法人買賣超：watchlist 這天已經抓過的直接沿用，不重打 API；
    只有清單外的漲跌停股才額外查一次 FinMind。查詢失敗不中斷報告產出，該股票在報告中
    顯示查無資料即可，不能因為補查失敗就讓整份報告產不出來。
    """
    existing_trades = {t["stock_id"]: t for t in storage.read_institutional_trades(target_date)}
    missing_ids = sorted({r.stock_id for r in limit_records} - existing_trades.keys())

    fresh_rows: list[dict] = []
    if missing_ids:
        finmind_client = FinMindClient(config.get_env("FINMIND_TOKEN"))
        try:
            fresh_rows = finmind_client.fetch_institutional_trades(target_date, missing_ids)
        except Exception as exc:  # noqa: BLE001 - 補查失敗不能擋住報告產出
            logger.warning("漲跌停股三大法人補查失敗：%s", exc)

    combined = {**existing_trades, **{row["stock_id"]: row for row in fresh_rows}}
    return {r.stock_id: combined[r.stock_id] for r in limit_records if r.stock_id in combined}


def _write_daily_report(
    storage: SnapshotRepository,
    target_date: str,
    institutional_trades: list[dict],
    stock_alerts: list[InstitutionalAlert],
    limit_records: list[LimitUpDownRecord],
    limit_institutional_trades: dict[str, dict],
    rebalance_events: list[RebalanceEvent],
    industry_map: dict[str, str],
    concept_map: dict[str, list[str]],
) -> None:
    try:
        content = ReportGenerator().generate(
            target_date, institutional_trades, stock_alerts, limit_records,
            limit_institutional_trades, rebalance_events, industry_map, concept_map,
        )
        storage.write_daily_report_md(target_date, content)
    except Exception:  # noqa: BLE001 - 報告產出失敗不能擋住後續推播，當天沒有報告連結即可，不是致命錯誤
        logger.exception("完整版報告產出失敗，本次略過，不影響其餘流程")


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

    if args.purge:
        try:
            succeeded = run_purge(dry_run=args.dry_run)
        except Exception:  # noqa: BLE001 - 進入點的最後防線，任何未預期例外都要被看見並記錄
            logger.exception("執行清除時發生未預期例外")
            return 1
        return 0 if succeeded else 1

    try:
        target_date = _parse_target_date(args.date) if args.date else date.today().isoformat()
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    try:
        if args.notify_only:
            succeeded = run_notify_only(target_date, args.report_url, dry_run=args.dry_run)
        else:
            succeeded = run(target_date, dry_run=args.dry_run, skip_notify=args.skip_notify)
    except Exception:  # noqa: BLE001 - 進入點的最後防線，任何未預期例外都要被看見並記錄
        logger.exception("執行時發生未預期例外")
        return 1
    return 0 if succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
