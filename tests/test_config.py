import json

import pytest

from src.config import ConfigError, ConfigLoader


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _make_valid_config_dir(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write(config_dir / "thresholds.json", {
        "default": {"broker_net_volume": 500, "etf_rebalance_pct": 10.0},
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
