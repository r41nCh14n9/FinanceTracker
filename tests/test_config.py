import json

import pytest

from src.config import ConfigError, ConfigLoader
from src.models import MarketCapTier


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _make_valid_config_dir(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write(config_dir / "thresholds.json", {
        "default": {"broker_net_volume": 500, "etf_rebalance_pct": 10.0},
        "institutional_tiered": {
            "volume_ratio_pct": 15.0,
            "market_cap_tiers": {"large_min": 100_000_000_000, "mid_min": 10_000_000_000},
            "amount_thresholds": {"large": 3_000_000_000, "mid": 500_000_000, "small": 100_000_000},
        },
        "market_institutional": {
            "foreign_amount": 20_000_000_000,
            "trust_amount": 3_000_000_000,
            "dealer_amount": 5_000_000_000,
        },
        "overrides": {"0050": {"etf_rebalance_pct": 20.0}},
    })
    _write(config_dir / "recipients.json", {
        "recipients": [
            {"id": "U1", "type": "USER", "label": "test", "enabled": True},
            {"id": "U2", "type": "USER", "label": "disabled", "enabled": False},
        ]
    })
    _write(config_dir / "broker_branches.json", {"branches": [{"code": "1020", "name": "凱基-台北"}]})
    _write(config_dir / "watchlist.json", {"stocks": ["2330"], "brokers": ["凱基-台北"], "etfs": ["0050"]})
    _write(config_dir / "issuer_registry.json", {
        "issuers": {
            "yuanta": {
                "name": "元大投信",
                "isEnabled": True,
                "adapter": "YuantaPcfAdapter",
                "pcf_url_template": "https://www.yuantaetfs.com/tradeInfo/pcf/{etf_id}",
                "etfs": ["0050"],
            },
            "cathay": {
                "name": "國泰投信",
                "isEnabled": False,
                "adapter": "CathayPcfAdapter",
                "pcf_url_template": "https://www.cathaysite.com.tw/funds/etf/pcf.aspx?fc={issuer_internal_code}",
                "etfs": ["00878"],
                "issuer_internal_codes": {"00878": "CN"},
            },
        }
    })
    return config_dir


def test_loads_valid_config_and_exposes_values(tmp_path):
    config_dir = _make_valid_config_dir(tmp_path)
    config = ConfigLoader(config_dir=config_dir)

    assert config.get_broker_net_volume_threshold() == 500
    assert config.get_etf_rebalance_pct_threshold("0050") == 20.0
    assert config.get_etf_rebalance_pct_threshold("0056") == 10.0
    assert [r["id"] for r in config.get_enabled_recipients()] == ["U1"]
    assert config.get_broker_branch_name("1020") == "凱基-台北"
    assert config.get_broker_branch_name("9999") is None
    assert config.is_broker_monitoring_enabled() is False


def test_exposes_institutional_tiered_and_market_thresholds(tmp_path):
    config_dir = _make_valid_config_dir(tmp_path)
    config = ConfigLoader(config_dir=config_dir)

    assert config.get_volume_ratio_threshold() == 15.0
    assert config.get_market_cap_tier_bounds() == (100_000_000_000, 10_000_000_000)
    assert config.get_tiered_amount_threshold(MarketCapTier.LARGE) == 3_000_000_000
    assert config.get_tiered_amount_threshold(MarketCapTier.MID) == 500_000_000
    assert config.get_tiered_amount_threshold(MarketCapTier.SMALL) == 100_000_000
    assert config.get_market_institutional_threshold("foreign") == 20_000_000_000
    assert config.get_market_institutional_threshold("trust") == 3_000_000_000
    assert config.get_market_institutional_threshold("dealer") == 5_000_000_000


def test_institutional_threshold_multiplier_defaults_to_one_when_not_configured(tmp_path):
    """thresholds.json 沒特別設定這個欄位（既有設定檔就是這樣）時，門檻要維持原始值，不能噴例外。"""
    config_dir = _make_valid_config_dir(tmp_path)
    config = ConfigLoader(config_dir=config_dir)

    assert config.get_volume_ratio_threshold() == 15.0
    assert config.get_tiered_amount_threshold(MarketCapTier.LARGE) == 3_000_000_000


def test_institutional_threshold_multiplier_scales_volume_and_amount_thresholds(tmp_path):
    """設定倍率後，成交量佔比與金額門檻都要一起放大，但市值分級門檻本身（large_min/mid_min）
    不受影響——那是用來判斷一檔股票屬於哪個市值級距，不是買賣超金額門檻。
    """
    config_dir = _make_valid_config_dir(tmp_path)
    thresholds = json.loads((config_dir / "thresholds.json").read_text(encoding="utf-8"))
    thresholds["institutional_tiered"]["threshold_multiplier"] = 1.5
    _write(config_dir / "thresholds.json", thresholds)
    config = ConfigLoader(config_dir=config_dir)

    assert config.get_volume_ratio_threshold() == 22.5
    assert config.get_tiered_amount_threshold(MarketCapTier.LARGE) == 4_500_000_000
    assert config.get_tiered_amount_threshold(MarketCapTier.MID) == 750_000_000
    assert config.get_tiered_amount_threshold(MarketCapTier.SMALL) == 150_000_000
    assert config.get_market_cap_tier_bounds() == (100_000_000_000, 10_000_000_000)


def test_etf_holding_count_drop_pct_threshold_defaults_when_not_configured(tmp_path):
    """thresholds.json 沒特別設定這個欄位（既有設定檔就是這樣）時，要有一個合理預設值，不是噴例外。"""
    config_dir = _make_valid_config_dir(tmp_path)
    config = ConfigLoader(config_dir=config_dir)

    assert config.get_etf_holding_count_drop_pct_threshold() == 50.0


def test_etf_holding_count_drop_pct_threshold_reads_configured_value(tmp_path):
    config_dir = _make_valid_config_dir(tmp_path)
    thresholds = json.loads((config_dir / "thresholds.json").read_text(encoding="utf-8"))
    thresholds["default"]["etf_holding_drop_pct"] = 30.0
    _write(config_dir / "thresholds.json", thresholds)
    config = ConfigLoader(config_dir=config_dir)

    assert config.get_etf_holding_count_drop_pct_threshold() == 30.0


def test_snapshot_retention_days_defaults_when_not_configured(tmp_path):
    """thresholds.json 沒特別設定這個欄位時，要有一個合理預設值（365 天），不是噴例外。"""
    config_dir = _make_valid_config_dir(tmp_path)
    config = ConfigLoader(config_dir=config_dir)

    assert config.get_snapshot_retention_days() == 365


def test_snapshot_retention_days_reads_configured_value(tmp_path):
    config_dir = _make_valid_config_dir(tmp_path)
    thresholds = json.loads((config_dir / "thresholds.json").read_text(encoding="utf-8"))
    thresholds["default"]["snapshot_retention_days"] = 180
    _write(config_dir / "thresholds.json", thresholds)
    config = ConfigLoader(config_dir=config_dir)

    assert config.get_snapshot_retention_days() == 180


def test_broker_monitoring_enabled_when_flag_set(tmp_path):
    config_dir = _make_valid_config_dir(tmp_path)
    _write(config_dir / "broker_branches.json", {
        "enabled": True,
        "branches": [{"code": "1020", "name": "凱基-台北"}],
    })
    config = ConfigLoader(config_dir=config_dir)
    assert config.is_broker_monitoring_enabled() is True


def test_missing_institutional_tiered_raises(tmp_path):
    config_dir = _make_valid_config_dir(tmp_path)
    _write(config_dir / "thresholds.json", {
        "default": {"broker_net_volume": 500, "etf_rebalance_pct": 10.0},
        "market_institutional": {"foreign_amount": 1, "trust_amount": 1, "dealer_amount": 1},
    })
    with pytest.raises(ConfigError):
        ConfigLoader(config_dir=config_dir)


def test_missing_market_institutional_raises(tmp_path):
    config_dir = _make_valid_config_dir(tmp_path)
    _write(config_dir / "thresholds.json", {
        "default": {"broker_net_volume": 500, "etf_rebalance_pct": 10.0},
        "institutional_tiered": {
            "volume_ratio_pct": 15.0,
            "market_cap_tiers": {"large_min": 1, "mid_min": 1},
            "amount_thresholds": {"large": 1, "mid": 1, "small": 1},
        },
    })
    with pytest.raises(ConfigError):
        ConfigLoader(config_dir=config_dir)


def test_missing_config_file_raises(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    with pytest.raises(ConfigError):
        ConfigLoader(config_dir=config_dir)


def test_invalid_thresholds_raises(tmp_path):
    config_dir = _make_valid_config_dir(tmp_path)
    _write(config_dir / "thresholds.json", {"default": {"broker_net_volume": 500}})
    with pytest.raises(ConfigError):
        ConfigLoader(config_dir=config_dir)


def test_get_issuer_mapping_returns_expected_dict(tmp_path):
    config_dir = _make_valid_config_dir(tmp_path)
    config = ConfigLoader(config_dir=config_dir)

    mapping = config.get_issuer_mapping("0050")
    assert mapping["issuer"] == "yuanta"
    assert mapping["adapter"] == "YuantaPcfAdapter"
    assert mapping["pcf_url_template"] == "https://www.yuantaetfs.com/tradeInfo/pcf/{etf_id}"


def test_watchlist_etf_without_issuer_mapping_raises(tmp_path):
    config_dir = _make_valid_config_dir(tmp_path)
    _write(config_dir / "watchlist.json", {"stocks": ["2330"], "brokers": ["凱基-台北"], "etfs": ["0050", "99999"]})

    with pytest.raises(ConfigError, match="99999"):
        ConfigLoader(config_dir=config_dir)


def test_watchlist_etf_with_disabled_issuer_raises(tmp_path):
    """00878 在 issuer_registry.json 裡有登記（國泰投信），但 isEnabled=False，
    要跟「完全沒登記」的情況分開報錯，才能一眼看出是「還沒開發」還是「打錯代碼」。"""
    config_dir = _make_valid_config_dir(tmp_path)
    _write(config_dir / "watchlist.json", {"stocks": ["2330"], "brokers": ["凱基-台北"], "etfs": ["0050", "00878"]})

    with pytest.raises(ConfigError, match="國泰投信"):
        ConfigLoader(config_dir=config_dir)


def test_issuer_registry_missing_issuers_key_raises(tmp_path):
    config_dir = _make_valid_config_dir(tmp_path)
    _write(config_dir / "issuer_registry.json", {})

    with pytest.raises(ConfigError):
        ConfigLoader(config_dir=config_dir)


def test_get_enabled_issuers_only_returns_enabled_ones(tmp_path):
    config_dir = _make_valid_config_dir(tmp_path)
    config = ConfigLoader(config_dir=config_dir)

    enabled = config.get_enabled_issuers()
    assert set(enabled.keys()) == {"yuanta"}


def test_get_available_etfs_by_issuer(tmp_path):
    config_dir = _make_valid_config_dir(tmp_path)
    config = ConfigLoader(config_dir=config_dir)

    assert config.get_available_etfs_by_issuer("cathay") == ["00878"]
    assert config.get_available_etfs_by_issuer("does-not-exist") == []


def test_get_concept_tags_returns_empty_dict_when_file_missing(tmp_path):
    """concept_tags.json 是選填檔案，不存在時不該讓 ConfigLoader 初始化失敗。"""
    config_dir = _make_valid_config_dir(tmp_path)
    config = ConfigLoader(config_dir=config_dir)

    assert config.get_concept_tags() == {}


def test_get_concept_tags_loads_existing_file(tmp_path):
    config_dir = _make_valid_config_dir(tmp_path)
    _write(config_dir / "concept_tags.json", {"IC 設計": {"members": [{"stock_id": "3529", "stock_name": "力旺"}]}})
    config = ConfigLoader(config_dir=config_dir)

    assert config.get_concept_tags() == {"IC 設計": {"members": [{"stock_id": "3529", "stock_name": "力旺"}]}}


def test_get_concept_tags_returns_empty_dict_when_file_malformed(tmp_path):
    """格式錯誤是選填裝飾功能的問題，不該讓整個設定檔載入（進而整個排程）失敗。"""
    config_dir = _make_valid_config_dir(tmp_path)
    (config_dir / "concept_tags.json").write_text("{not valid json", encoding="utf-8")
    config = ConfigLoader(config_dir=config_dir)

    assert config.get_concept_tags() == {}


def test_get_env_required_missing_raises(monkeypatch):
    monkeypatch.delenv("SOME_TEST_ENV", raising=False)
    with pytest.raises(ConfigError):
        ConfigLoader.get_env("SOME_TEST_ENV")


def test_get_env_optional_missing_returns_empty(monkeypatch):
    monkeypatch.delenv("SOME_TEST_ENV", raising=False)
    assert ConfigLoader.get_env("SOME_TEST_ENV", required=False) == ""
