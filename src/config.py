"""讀取 config/ 底下的設定檔與環境變數，提供型別化的存取介面給其他模組使用。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    """設定檔格式錯誤或缺少必要欄位時拋出，代表部署/設定問題而非當日資料問題。"""


class ConfigLoader:
    def __init__(self, config_dir: Path | str = "config"):
        load_dotenv()
        self._config_dir = Path(config_dir)
        self._thresholds = self._load_json("thresholds.json")
        self._recipients = self._load_json("recipients.json")
        self._broker_branches = self._load_json("broker_branches.json")
        self._watchlist = self._load_json("watchlist.json")
        self._validate()

    def _load_json(self, filename: str) -> dict:
        path = self._config_dir / filename
        if not path.exists():
            raise ConfigError(f"設定檔不存在：{path}")
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"設定檔格式錯誤：{path}（{exc}）") from exc

    def _validate(self) -> None:
        if "default" not in self._thresholds:
            raise ConfigError("thresholds.json 缺少 default 區塊")
        required_default_keys = {"broker_net_volume", "etf_rebalance_pct"}
        missing = required_default_keys - self._thresholds["default"].keys()
        if missing:
            raise ConfigError(f"thresholds.json.default 缺少欄位：{missing}")
        if "recipients" not in self._recipients:
            raise ConfigError("recipients.json 缺少 recipients 欄位")
        if "branches" not in self._broker_branches:
            raise ConfigError("broker_branches.json 缺少 branches 欄位")
        for key in ("stocks", "brokers", "etfs"):
            if key not in self._watchlist:
                raise ConfigError(f"watchlist.json 缺少 {key} 欄位")

    # --- 監控範圍 ---
    def get_watchlist_stocks(self) -> list[str]:
        return list(self._watchlist["stocks"])

    def get_watchlist_brokers(self) -> list[str]:
        return list(self._watchlist["brokers"])

    def get_watchlist_etfs(self) -> list[str]:
        return list(self._watchlist["etfs"])

    # --- 門檻（分點買賣超為全域單一值，ETF 調倉幅度可依代碼覆寫） ---
    def get_broker_net_volume_threshold(self) -> int:
        return int(self._thresholds["default"]["broker_net_volume"])

    def get_etf_rebalance_pct_threshold(self, etf_id: str) -> float:
        overrides = self._thresholds.get("overrides", {})
        if etf_id in overrides and "etf_rebalance_pct" in overrides[etf_id]:
            return float(overrides[etf_id]["etf_rebalance_pct"])
        return float(self._thresholds["default"]["etf_rebalance_pct"])

    # --- 收訊名單 ---
    def get_enabled_recipients(self) -> list[dict]:
        return [r for r in self._recipients["recipients"] if r.get("enabled", False)]

    # --- 分點代碼對照中文名稱 ---
    def get_broker_branch_name(self, code: str) -> str | None:
        for branch in self._broker_branches["branches"]:
            if branch["code"] == code:
                return branch["name"]
        return None

    # --- 環境變數 / 密鑰 ---
    @staticmethod
    def get_env(key: str, required: bool = True) -> str:
        value = os.environ.get(key)
        if required and not value:
            raise ConfigError(f"缺少必要環境變數：{key}")
        return value or ""
