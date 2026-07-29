"""負責讀寫 data/ 目錄下的所有快照與報告 JSON 檔案。

上層模組（fetcher / analyzer / notifier）不需要知道實際存放路徑或檔案格式，
只透過這裡提供的方法存取資料；「前一交易日」的查找邏輯也封裝在這裡，
靠掃描既有快照目錄找最近一筆有效交易日，不需要另外維護交易日曆。
"""
from __future__ import annotations

import dataclasses
import json
from enum import Enum
from pathlib import Path
from typing import Any

from src.models import (
    BrokerTradeRecord,
    DailySnapshotMeta,
    EtfHoldingRecord,
    NotificationLogEntry,
    RebalanceEvent,
)


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

    # --- 路徑 helpers ---
    def _snapshot_dir(self, snapshot_date: str) -> Path:
        return self._snapshots_dir / snapshot_date

    def _report_dir(self, report_date: str) -> Path:
        return self._reports_dir / report_date

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

    # --- BROKER_TRADE_RECORD ---
    def write_broker_trades(self, snapshot_date: str, records: list[BrokerTradeRecord]) -> None:
        payload = [dataclasses.asdict(r) for r in records]
        self._write_json(self._snapshot_dir(snapshot_date) / "broker_trades.json", payload)

    def read_broker_trades(self, snapshot_date: str) -> list[dict]:
        return self._read_json(self._snapshot_dir(snapshot_date) / "broker_trades.json") or []

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
