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


def test_get_env_required_missing_raises(monkeypatch):
    monkeypatch.delenv("SOME_TEST_ENV", raising=False)
    with pytest.raises(ConfigError):
        ConfigLoader.get_env("SOME_TEST_ENV")


def test_get_env_optional_missing_returns_empty(monkeypatch):
    monkeypatch.delenv("SOME_TEST_ENV", raising=False)
    assert ConfigLoader.get_env("SOME_TEST_ENV", required=False) == ""
