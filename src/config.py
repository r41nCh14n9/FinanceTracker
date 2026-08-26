"""讀取 config/ 底下的設定檔與環境變數，提供型別化的存取介面給其他模組使用。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from src.models import MarketCapTier

logger = logging.getLogger(__name__)

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
        self._issuer_registry = self._load_json("issuer_registry.json")
        self._concept_tags = self._load_json_optional("concept_tags.json")
        self._etf_issuer_key = {}  # etf_id -> issuer 鍵，_validate() 時建好，查表用
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

    def _load_json_optional(self, filename: str) -> dict:
        """給選填設定檔用：檔案不存在或格式錯誤都不中止程式，直接視為空物件；
        這類檔案只是錦上添花的裝飾功能，不該有能力擋下整個每日通知流程。
        """
        path = self._config_dir / filename
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as exc:
            logger.warning("選填設定檔格式錯誤，本次視為空（%s）：%s", path, exc)
            return {}

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
        self._validate_issuer_registry()

    def _validate_issuer_registry(self) -> None:
        if "issuers" not in self._issuer_registry:
            raise ConfigError("issuer_registry.json 缺少 issuers 欄位")
        issuers = self._issuer_registry["issuers"]

        # 反查表：ETF 代碼 -> 投信鍵，同一檔 ETF 理論上只會屬於一家投信；
        # 後面出現的重複只保留最後一筆，設定檔本身不會刻意這樣寫。
        self._etf_issuer_key = {
            etf_id: issuer_key
            for issuer_key, issuer in issuers.items()
            for etf_id in issuer.get("etfs", [])
        }

        for etf_id in self._watchlist["etfs"]:
            issuer_key = self._etf_issuer_key.get(etf_id)
            if issuer_key is None:
                raise ConfigError(
                    f"watchlist.etfs 內 '{etf_id}' 尚未受支援（issuer_registry.json 找不到對應投信），"
                    "請確認代碼是否正確，或該投信是否已完成 Adapter 開發"
                )
            issuer = issuers[issuer_key]
            if not issuer.get("isEnabled", False):
                raise ConfigError(
                    f"watchlist.etfs 內 '{etf_id}' 對應的投信「{issuer.get('name', issuer_key)}」"
                    "目前未開放（isEnabled=false），請洽維運人員確認是否已完成開發並開通"
                )

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

    # --- 概念股標籤（人工維護，選填檔案；結構同 industry_tags.json：分類 -> 成員清單） ---
    def get_concept_tags(self) -> dict:
        return self._concept_tags

    # --- ETF 發行投信對照（決定用哪個 Adapter、打哪個 URL） ---
    def get_issuer_mapping(self, etf_id: str) -> dict:
        issuer_key = self._etf_issuer_key[etf_id]
        issuer = self._issuer_registry["issuers"][issuer_key]
        mapping = {
            "issuer": issuer_key,
            "adapter": issuer["adapter"],
            "pcf_url_template": issuer["pcf_url_template"],
        }
        internal_code = issuer.get("issuer_internal_codes", {}).get(etf_id)
        if internal_code is not None:
            mapping["issuer_internal_code"] = internal_code
        return mapping

    def get_issuer_name(self, etf_id: str) -> str:
        """回傳 ETF 對應投信的中文顯示名稱（例如「元大投信」），純粹給 log 訊息使用；
        查無對照時退回 ETF 代碼本身，不拋例外，避免記 log 這種非關鍵路徑反而讓程式中斷。
        """
        issuer_key = self._etf_issuer_key.get(etf_id)
        if issuer_key is None:
            return etf_id
        issuer = self._issuer_registry["issuers"].get(issuer_key, {})
        return issuer.get("name", issuer_key)

    # --- 投信開放狀態（isEnabled feature flag）與可監控 ETF 清單 ---
    def get_enabled_issuers(self) -> dict[str, dict]:
        """回傳目前 isEnabled=true 的投信對照（鍵為投信代碼），供檢核或分流查詢使用。"""
        issuers = self._issuer_registry["issuers"]
        return {key: issuer for key, issuer in issuers.items() if issuer.get("isEnabled", False)}

    def get_available_etfs_by_issuer(self, issuer_key: str) -> list[str]:
        """回傳指定投信目前登記的 ETF 清單；投信代碼不存在時回傳空清單。"""
        issuer = self._issuer_registry["issuers"].get(issuer_key)
        return list(issuer["etfs"]) if issuer else []

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

    def get_etf_holding_count_drop_pct_threshold(self) -> float:
        """持股筆數較前一交易日驟降多少百分比時，視為投信網站改版造成的解析異常而非真實清倉；
        選填欄位，未設定時預設 50%。"""
        return float(self._thresholds.get("default", {}).get("etf_holding_drop_pct", 50.0))

    def get_snapshot_retention_days(self) -> int:
        """快照/報告目錄保留天數，超過此天數（以執行當下日期回推）視為過期可清除；
        選填欄位，未設定時預設 365 天（1 年）。"""
        return int(self._thresholds.get("default", {}).get("snapshot_retention_days", 365))

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
