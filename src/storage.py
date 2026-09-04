"""負責讀寫 data/ 目錄下的所有快照與報告 JSON 檔案。

上層模組（fetcher / analyzer / notifier）不需要知道實際存放路徑或檔案格式，
只透過這裡提供的方法存取資料；「前一交易日」的查找邏輯也封裝在這裡，
靠掃描既有快照目錄找最近一筆有效交易日，不需要另外維護交易日曆。

股本快取（capital_stock）比較特別，是按股票代碼存放、不分日期的單一檔案，
放在 data/reference/ 下面而不是 data/snapshots/{date}/ 底下，因為股本是季更新資料，
放進每天的快照只會讓幾乎一樣的內容重複存好幾百份。

`purge_expired()` 負責清掉太舊的歷史資料，只會動 snapshots/ 跟 reports/ 這兩個
按日期分目錄的路徑，reference/ 底下的股本快取因為是「目前最新值」而非歷史紀錄，
一律不動。
"""
from __future__ import annotations

import dataclasses
import json
import re
import shutil
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from src.models import (
    AlertScope,
    AlertTriggerType,
    BrokerTradeRecord,
    DailySnapshotMeta,
    EtfHoldingRecord,
    InstitutionalAlert,
    InstitutionalTradeRecord,
    LimitUpDownRecord,
    MarketCapTier,
    MarketInstitutionalRecord,
    NotificationLogEntry,
    PurgeResult,
    RebalanceEvent,
    RebalanceEventType,
    SourceStatus,
    StockCapitalSnapshot,
    StockDailyTrading,
)

_DATE_DIR_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_valid_date(text: str) -> bool:
    """格式對不代表值合法（例如 9999-99-99），這裡再用 date.fromisoformat() 真的解析一次。"""
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


class _EnumJSONEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, Enum):
            return o.value
        return super().default(o)


class SnapshotRepository:
    def __init__(self, data_dir: Path | str = "data"):
        self._data_dir = Path(data_dir)
        self._snapshots_dir = self._data_dir / "snapshots"
        self._reports_dir = self._data_dir / "reports"
        self._reference_dir = self._data_dir / "reference"
        self._tags_dir = self._data_dir / "tags"

    # --- 路徑 helpers ---
    def _snapshot_dir(self, snapshot_date: str) -> Path:
        return self._snapshots_dir / snapshot_date

    def _report_dir(self, report_date: str) -> Path:
        return self._reports_dir / report_date

    def _capital_stock_cache_path(self, stock_id: str) -> Path:
        return self._reference_dir / "capital_stock" / f"{stock_id}.json"

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, cls=_EnumJSONEncoder)

    @staticmethod
    def _read_json(path: Path) -> Any | None:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    # --- DAILY_SNAPSHOT ---
    def write_meta(self, meta: DailySnapshotMeta) -> None:
        self._write_json(self._snapshot_dir(meta.snapshot_date) / "_meta.json", dataclasses.asdict(meta))

    def read_meta(self, snapshot_date: str) -> dict | None:
        return self._read_json(self._snapshot_dir(snapshot_date) / "_meta.json")

    def upsert_meta_source(
        self, snapshot_date: str, source_key: str, status: SourceStatus, is_trading_day: bool
    ) -> None:
        """只覆寫單一來源的狀態，其餘既有來源與欄位維持原樣後寫回；is_trading_day 只接受
        從 False 轉成 True，不會因為某次局部更新就把已經確認過的交易日改回 False，也不會
        在 _meta.json 還不存在時把整份 meta 憑空蓋掉，避免其他來源的既有狀態被抹掉。
        """
        path = self._snapshot_dir(snapshot_date) / "_meta.json"
        existing = self._read_json(path) or {"snapshot_date": snapshot_date, "sources": {}, "is_trading_day": False}
        existing.setdefault("sources", {})
        existing["sources"][source_key] = dataclasses.asdict(status)
        if is_trading_day:
            existing["is_trading_day"] = True
        self._write_json(path, existing)

    # --- BROKER_TRADE_RECORD（保留，分點功能停用期間不會有新資料寫入） ---
    def write_broker_trades(self, snapshot_date: str, records: list[BrokerTradeRecord]) -> None:
        payload = [dataclasses.asdict(r) for r in records]
        self._write_json(self._snapshot_dir(snapshot_date) / "broker_trades.json", payload)

    def read_broker_trades(self, snapshot_date: str) -> list[dict]:
        return self._read_json(self._snapshot_dir(snapshot_date) / "broker_trades.json") or []

    # --- INSTITUTIONAL_TRADE_RECORD（個股三大法人買賣超原始資料） ---
    def write_institutional_trades(self, snapshot_date: str, records: list[InstitutionalTradeRecord]) -> None:
        payload = [dataclasses.asdict(r) for r in records]
        self._write_json(self._snapshot_dir(snapshot_date) / "institutional_trades.json", payload)

    def read_institutional_trades(self, snapshot_date: str) -> list[dict]:
        return self._read_json(self._snapshot_dir(snapshot_date) / "institutional_trades.json") or []

    # --- STOCK_DAILY_TRADING（個股成交量/收盤價） ---
    def write_stock_trading(self, snapshot_date: str, records: list[StockDailyTrading]) -> None:
        payload = [dataclasses.asdict(r) for r in records]
        self._write_json(self._snapshot_dir(snapshot_date) / "stock_trading.json", payload)

    def read_stock_trading(self, snapshot_date: str) -> list[dict]:
        return self._read_json(self._snapshot_dir(snapshot_date) / "stock_trading.json") or []

    # --- STOCK_CAPITAL_SNAPSHOT（股本快取，獨立於日期快照之外，單檔覆寫） ---
    def read_capital_stock_cache(self, stock_id: str) -> dict | None:
        return self._read_json(self._capital_stock_cache_path(stock_id))

    def write_capital_stock_cache(self, snapshot: StockCapitalSnapshot) -> None:
        self._write_json(self._capital_stock_cache_path(snapshot.stock_id), dataclasses.asdict(snapshot))

    # --- INDUSTRY_TAG（產業→成員反查表，機器自動維護，整份檔案覆寫，不分日期）
    # 獨立放在 data/tags/ 而非 data/reference/，跟股本快取等「單一數值快取」性質不同，
    # 分類標籤未來可能會有更多種類，先給它自己的資料夾。
    def read_industry_tags(self) -> dict:
        return self._read_json(self._tags_dir / "industry_tags.json") or {}

    def write_industry_tags(self, table: dict) -> None:
        self._write_json(self._tags_dir / "industry_tags.json", table)

    # --- MARKET_INSTITUTIONAL_RECORD（大盤三大法人買賣金額，每天僅一筆） ---
    def write_market_institutional(self, snapshot_date: str, record: MarketInstitutionalRecord) -> None:
        self._write_json(self._snapshot_dir(snapshot_date) / "market_institutional.json", dataclasses.asdict(record))

    def read_market_institutional(self, snapshot_date: str) -> dict | None:
        return self._read_json(self._snapshot_dir(snapshot_date) / "market_institutional.json")

    # --- LIMIT_UP_DOWN_RECORD（當日觸及漲跌停股票清單，上市＋上櫃合併） ---
    def write_limit_up_down(self, snapshot_date: str, records: list[LimitUpDownRecord]) -> None:
        payload = [dataclasses.asdict(r) for r in records]
        self._write_json(self._snapshot_dir(snapshot_date) / "limit_up_down.json", payload)

    def read_limit_up_down(self, snapshot_date: str) -> list[dict]:
        return self._read_json(self._snapshot_dir(snapshot_date) / "limit_up_down.json") or []

    # --- ETF_HOLDING_RECORD（每檔 ETF 各自一個檔案，比對時只需載入單一 ETF） ---
    def write_etf_holdings(self, snapshot_date: str, etf_id: str, records: list[EtfHoldingRecord]) -> None:
        payload = [dataclasses.asdict(r) for r in records]
        path = self._snapshot_dir(snapshot_date) / "etf_holdings" / f"{etf_id}.json"
        self._write_json(path, payload)

    def read_etf_holdings(self, snapshot_date: str, etf_id: str) -> list[dict]:
        path = self._snapshot_dir(snapshot_date) / "etf_holdings" / f"{etf_id}.json"
        return self._read_json(path) or []

    # --- REBALANCE_EVENT（分析結果落地保存，供事後稽核判斷依據） ---
    def write_rebalance_events(self, report_date: str, events: list[RebalanceEvent]) -> None:
        payload = [dataclasses.asdict(e) for e in events]
        self._write_json(self._report_dir(report_date) / "rebalance_events.json", payload)

    def read_rebalance_events(self, report_date: str) -> list[RebalanceEvent]:
        """讀回既有的換倉分析結果，還原成 RebalanceEvent（含 event_type 列舉），供推播
        階段跟分析階段脫鉤後（--notify-only）重新格式化訊息用，不用重跑一次分析。
        """
        payload = self._read_json(self._report_dir(report_date) / "rebalance_events.json") or []
        return [self._rebalance_event_from_dict(row) for row in payload]

    @staticmethod
    def _rebalance_event_from_dict(row: dict) -> RebalanceEvent:
        return RebalanceEvent(
            event_date=row["event_date"],
            etf_id=row["etf_id"],
            component_stock_id=row["component_stock_id"],
            component_name=row["component_name"],
            event_type=RebalanceEventType(row["event_type"]),
            prev_shares=row["prev_shares"],
            curr_shares=row["curr_shares"],
            change_pct=row["change_pct"],
        )

    # --- INSTITUTIONAL_ALERT（門檻判斷結果，只存達標項目） ---
    def write_institutional_alerts(self, report_date: str, alerts: list[InstitutionalAlert]) -> None:
        payload = [dataclasses.asdict(a) for a in alerts]
        self._write_json(self._report_dir(report_date) / "institutional_alerts.json", payload)

    def read_institutional_alerts(self, report_date: str) -> list[InstitutionalAlert]:
        """讀回既有的門檻判斷結果，還原成 InstitutionalAlert（含 scope／trigger_type／
        market_cap_tier 列舉），供推播階段跟分析階段脫鉤後（--notify-only）重新格式化
        訊息用，不用重跑一次三大法人門檻判斷。
        """
        payload = self._read_json(self._report_dir(report_date) / "institutional_alerts.json") or []
        return [self._institutional_alert_from_dict(row) for row in payload]

    @staticmethod
    def _institutional_alert_from_dict(row: dict) -> InstitutionalAlert:
        tier = row.get("market_cap_tier")
        return InstitutionalAlert(
            scope=AlertScope(row["scope"]),
            trigger_type=AlertTriggerType(row["trigger_type"]),
            stock_id=row.get("stock_id"),
            estimated_amount=row.get("estimated_amount"),
            market_cap_tier=MarketCapTier(tier) if tier else None,
            volume_ratio_pct=row.get("volume_ratio_pct"),
        )

    # --- DAILY_FULL_REPORT（完整版 Markdown 日報，純文字檔，不分陣列/物件結構） ---
    def write_daily_report_md(self, report_date: str, content: str) -> None:
        path = self._report_dir(report_date) / "daily_report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # --- NOTIFICATION_LOG（同一天可能有多位收訊者，逐筆附加） ---
    def append_notification_log(self, report_date: str, entry: NotificationLogEntry) -> None:
        path = self._report_dir(report_date) / "notification_log.json"
        existing = self._read_json(path) or []
        existing.append(dataclasses.asdict(entry))
        self._write_json(path, existing)

    # --- 前一交易日查找：日期字串格式為 YYYY-MM-DD，字典序即等於時間序 ---
    def find_previous_trading_day(self, before_date: str) -> str | None:
        if not self._snapshots_dir.exists():
            return None
        candidates = sorted(
            (p.name for p in self._snapshots_dir.iterdir() if p.is_dir() and p.name < before_date),
            reverse=True,
        )
        for candidate in candidates:
            meta = self.read_meta(candidate)
            if meta and meta.get("is_trading_day"):
                return candidate
        return None

    # --- 保留清除：只動 snapshots/reports 這兩個按日期分目錄的路徑，reference/ 不碰 ---
    def purge_expired(self, retention_days: int, as_of_date: date, dry_run: bool = False) -> PurgeResult:
        cutoff = (as_of_date - timedelta(days=retention_days)).isoformat()

        deleted: list[str] = []
        skipped_invalid_format: list[str] = []
        failed: list[tuple[str, str]] = []

        for base_dir in (self._snapshots_dir, self._reports_dir):
            self._purge_expired_dir(base_dir, cutoff, dry_run, deleted, skipped_invalid_format, failed)

        return PurgeResult(
            cutoff_date=cutoff, deleted=deleted, skipped_invalid_format=skipped_invalid_format, failed=failed,
        )

    @staticmethod
    def _purge_expired_dir(
        base_dir: Path,
        cutoff: str,
        dry_run: bool,
        deleted: list[str],
        skipped_invalid_format: list[str],
        failed: list[tuple[str, str]],
    ) -> None:
        if not base_dir.exists():
            return
        for entry in sorted(base_dir.iterdir()):
            if not entry.is_dir():
                continue
            if not _DATE_DIR_PATTERN.match(entry.name) or not _is_valid_date(entry.name):
                # 名稱長得不像合法日期就一律略過，不猜測、不連坐清除，避免防呆邏輯本身誤刪不明資料。
                skipped_invalid_format.append(str(entry))
                continue
            if entry.name >= cutoff:
                continue  # 還在保留範圍內

            if dry_run:
                deleted.append(str(entry))
                continue
            try:
                shutil.rmtree(entry)
                deleted.append(str(entry))
            except OSError as exc:
                failed.append((str(entry), str(exc)))
