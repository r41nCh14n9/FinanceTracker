"""讀取 config/ 底下的設定檔與環境變數，提供型別化的存取介面給其他模組使用。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

from src.models import MarketCapTier

_TIER_KEY_BY_ENUM = {
    MarketCapTier.LARGE: "large",
    MarketCapTier.MID: "mid",
    MarketCapTier.SMALL: "small",
}


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
        self._validate_institutional_tiered()
        self._validate_market_institutional()

        if "recipients" not in self._recipients:
            raise ConfigError("recipients.json 缺少 recipients 欄位")
        if "branches" not in self._broker_branches:
            raise ConfigError("broker_branches.json 缺少 branches 欄位")
        for key in ("stocks", "brokers", "etfs"):
            if key not in self._watchlist:
                raise ConfigError(f"watchlist.json 缺少 {key} 欄位")

    def _validate_institutional_tiered(self) -> None:
        tiered = self._thresholds.get("institutional_tiered")
        if tiered is None:
            raise ConfigError("thresholds.json 缺少 institutional_tiered 區塊")
        if "volume_ratio_pct" not in tiered:
            raise ConfigError("thresholds.json.institutional_tiered 缺少 volume_ratio_pct")
        tiers = tiered.get("market_cap_tiers", {})
        if not {"large_min", "mid_min"} <= tiers.keys():
            raise ConfigError("thresholds.json.institutional_tiered.market_cap_tiers 缺少 large_min/mid_min")
        amounts = tiered.get("amount_thresholds", {})
        if not {"large", "mid", "small"} <= amounts.keys():
            raise ConfigError("thresholds.json.institutional_tiered.amount_thresholds 缺少 large/mid/small")

    def _validate_market_institutional(self) -> None:
        market = self._thresholds.get("market_institutional")
        if market is None:
            raise ConfigError("thresholds.json 缺少 market_institutional 區塊")
        required = {"foreign_amount", "trust_amount", "dealer_amount"}
        missing = required - market.keys()
        if missing:
            raise ConfigError(f"thresholds.json.market_institutional 缺少欄位：{missing}")

    # --- 監控範圍 ---
    def get_watchlist_stocks(self) -> list[str]:
        return list(self._watchlist["stocks"])

    def get_watchlist_brokers(self) -> list[str]:
        return list(self._watchlist["brokers"])

    def get_watchlist_etfs(self) -> list[str]:
        return list(self._watchlist["etfs"])

    # --- 分點功能（保留但預設停用，設定於 broker_branches.json 頂層） ---
    def is_broker_monitoring_enabled(self) -> bool:
        return bool(self._broker_branches.get("enabled", False))

    # --- 門檻：分點（保留供日後復用）、ETF 調倉幅度（可依代碼覆寫） ---
    def get_broker_net_volume_threshold(self) -> int:
        return int(self._thresholds["default"]["broker_net_volume"])

    def get_etf_rebalance_pct_threshold(self, etf_id: str) -> float:
        overrides = self._thresholds.get("overrides", {})
        if etf_id in overrides and "etf_rebalance_pct" in overrides[etf_id]:
            return float(overrides[etf_id]["etf_rebalance_pct"])
        return float(self._thresholds["default"]["etf_rebalance_pct"])

    # --- 門檻：個股三大法人雙門檻（成交量佔比 / 市值分級金額） ---
    def get_volume_ratio_threshold(self) -> float:
        """回傳百分比數字（例如 15.0 代表 15%），比對時記得除以 100。"""
        return float(self._thresholds["institutional_tiered"]["volume_ratio_pct"])

    def get_market_cap_tier_bounds(self) -> tuple[int, int]:
        """回傳 (大型股市值下限, 中型股市值下限)，單位元。"""
        tiers = self._thresholds["institutional_tiered"]["market_cap_tiers"]
        return int(tiers["large_min"]), int(tiers["mid_min"])

    def get_tiered_amount_threshold(self, tier: MarketCapTier) -> int:
        key = _TIER_KEY_BY_ENUM[tier]
        return int(self._thresholds["institutional_tiered"]["amount_thresholds"][key])

    # --- 門檻：大盤三大法人金額（外資/投信/自營商各自獨立） ---
    def get_market_institutional_threshold(self, investor_type: str) -> int:
        """investor_type 為 'foreign' / 'trust' / 'dealer'。"""
        return int(self._thresholds["market_institutional"][f"{investor_type}_amount"])

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
