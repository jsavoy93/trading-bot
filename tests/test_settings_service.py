"""Deterministic tests for the shared dashboard/bot settings store."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (str(PROJECT_ROOT), str(SRC_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core import settings_service
from core.smart_bot import SmartTradingBot


@pytest.fixture
def isolated_settings_db(tmp_path, monkeypatch):
    db_path = tmp_path / "settings.db"
    monkeypatch.setattr(settings_service, "_DB_PATH", db_path)
    return db_path


@pytest.fixture
def dashboard_module(isolated_settings_db, monkeypatch):
    # Keep dashboard import deterministic and local to the isolated settings DB.
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST1234567890abcdefghijklmnop")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    sys.modules.pop("dashboard", None)
    module = importlib.import_module("dashboard")
    monkeypatch.setattr(module, "_smart_bot_instance", None)
    return module


def _strategy_bot_stub():
    return SimpleNamespace(
        sma_fast=10,
        sma_slow=30,
        rsi_period=14,
        rsi_buy_threshold=30,
        rsi_sell_threshold=70,
        min_score_buy=50,
        enable_multi_timeframe=True,
        hourly_weight=0.30,
        enable_volume_confirmation=True,
        enable_mtf_conflict_filter=True,
        enable_vol_downgrade_filter=True,
        enable_ai_conflict_filter=True,
        enable_regime_filter=True,
        enable_sp_filter=True,
        enable_liquidity_filter=True,
        enable_sector_filter=True,
        rotation_threshold=20,
        loop_delay_seconds=300,
        atr_position_size_pct=2.0,
        risk_per_trade=0.02,
        max_sector_concentration=0.25,
        max_correlation=0.7,
        max_portfolio_beta=1.5,
        max_drawdown_pct=10.0,
        daily_loss_limit_pct=5.0,
    )


@pytest.mark.parametrize(
    ("key", "default", "param_type"),
    [
        ("sma_fast", 10, "int"),
        ("hourly_weight", 0.30, "float"),
        ("enable_multi_timeframe", True, "bool"),
        ("regime_symbol", "SPY", "str"),
    ],
)
def test_load_typed_returns_default_when_setting_is_absent(
    isolated_settings_db, key, default, param_type
):
    assert settings_service.load_typed(key, default, param_type) == default


@pytest.mark.parametrize(
    ("key", "stored_value", "param_type", "expected"),
    [
        ("sma_fast", "21", "int", 21),
        ("hourly_weight", "0.45", "float", 0.45),
        ("enable_multi_timeframe", "false", "bool", False),
        ("enable_volume_confirmation", "yes", "bool", True),
        ("regime_symbol", "QQQ", "str", "QQQ"),
    ],
)
def test_load_typed_reads_dashboard_style_database_overrides(
    isolated_settings_db, key, stored_value, param_type, expected
):
    # The dashboard persists string values through settings_service.save().
    settings_service.save(key, stored_value)

    assert settings_service.load_typed(key, object(), param_type) == expected


@pytest.mark.parametrize(
    ("key", "bad_value", "param_type"),
    [
        ("sma_fast", "not-an-integer", "int"),
        ("hourly_weight", "not-a-float", "float"),
        ("enable_multi_timeframe", "maybe", "bool"),
    ],
)
def test_load_typed_rejects_invalid_overrides(
    isolated_settings_db, key, bad_value, param_type
):
    settings_service.save(key, bad_value)

    with pytest.raises(ValueError):
        settings_service.load_typed(key, None, param_type)


def test_save_typed_rejects_invalid_numeric_value_without_persisting(
    isolated_settings_db,
):
    with pytest.raises(ValueError):
        settings_service.save_typed("sma_fast", "invalid", "int")

    assert settings_service.get("sma_fast") is None


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("sma_fast", 1),
        ("sma_slow", 201),
        ("hourly_weight", 1.01),
        ("enable_multi_timeframe", "maybe"),
    ],
)
def test_schema_rejects_out_of_range_or_invalid_values(
    isolated_settings_db, key, bad_value
):
    with pytest.raises(ValueError):
        settings_service.save_typed(key, bad_value)

    assert settings_service.get(key) is None


def test_authoritative_schema_defaults_drive_effective_strategy_settings(
    isolated_settings_db,
):
    effective = settings_service.load_effective_strategy_settings()

    assert effective["min_score_buy"] == 50
    assert effective["rsi_buy_threshold"] == 30
    assert effective["sma_fast"] == 10
    assert effective["enable_multi_timeframe"] is True
    assert set(effective) == set(settings_service.STRATEGY_SETTINGS_SCHEMA)


def test_effective_strategy_settings_use_typed_database_overrides(
    isolated_settings_db,
):
    settings_service.save_typed("sma_fast", "21")
    settings_service.save_typed("hourly_weight", "0.45")
    settings_service.save_typed("enable_multi_timeframe", "false")

    effective = settings_service.load_effective_strategy_settings()

    assert effective["sma_fast"] == 21
    assert effective["hourly_weight"] == 0.45
    assert effective["enable_multi_timeframe"] is False
    assert effective["min_score_buy"] == 50


def test_invalid_legacy_values_fall_back_to_schema_defaults(
    isolated_settings_db, caplog
):
    settings_service.save("sma_fast", "bad-legacy-value")
    settings_service.save_typed("hourly_weight", "0.45")

    effective = settings_service.load_effective_strategy_settings()

    assert effective["sma_fast"] == 10
    assert effective["hourly_weight"] == 0.45
    assert "Invalid persisted strategy setting sma_fast" in caplog.text


def test_dashboard_metadata_is_derived_from_schema_and_effective_values(
    isolated_settings_db,
):
    settings_service.save_typed("min_score_buy", 55)

    metadata = settings_service.dashboard_parameters()

    assert metadata["min_score_buy"] == {
        "value": 55,
        "min": 0,
        "max": 100,
        "step": 1,
        "type": "int",
        "default": 50,
        "description": "Minimum total score required for a BUY signal.",
        "category": "Signal Thresholds",
    }
    assert metadata["hourly_weight"]["default"] == 0.30
    assert metadata["hourly_weight"]["type"] == "float"


def test_dashboard_metadata_falls_back_for_invalid_legacy_values(
    dashboard_module,
):
    settings_service.save("hourly_weight", "invalid-legacy-value")

    response = TestClient(dashboard_module.app).get("/api/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["hourly_weight"]["value"] == 0.30
    assert payload["hourly_weight"]["default"] == 0.30


def test_save_typed_serializes_normalized_schema_values(isolated_settings_db):
    settings_service.save_typed("enable_volume_confirmation", "off")
    settings_service.save_typed("sma_fast", "12.0")
    settings_service.save_typed("hourly_weight", "0.5")

    assert settings_service.get("enable_volume_confirmation") == "false"
    assert settings_service.get("sma_fast") == "12"
    assert settings_service.get("hourly_weight") == "0.5"


def test_dashboard_batch_with_invalid_value_persists_nothing(dashboard_module):
    with pytest.raises(HTTPException) as excinfo:
        dashboard_module.api_update_settings({"sma_fast": "12", "hourly_weight": "2"})

    assert excinfo.value.status_code == 400
    assert settings_service.get("sma_fast") is None
    assert settings_service.get("hourly_weight") is None


def test_dashboard_fully_valid_batch_persists_all_normalized_values(dashboard_module):
    response = dashboard_module.api_update_settings(
        {"sma_fast": "12.0", "enable_volume_confirmation": "off"}
    )

    assert response == {
        "updated": {"sma_fast": 12, "enable_volume_confirmation": False},
        "status": "ok",
    }
    assert settings_service.get("sma_fast") == "12"
    assert settings_service.get("enable_volume_confirmation") == "false"


def test_dashboard_api_route_returns_http_400_for_invalid_schema_input(
    dashboard_module,
):
    response = TestClient(dashboard_module.app).post(
        "/api/settings", json={"hourly_weight": 2}
    )

    assert response.status_code == 400
    assert "hourly_weight must be <= 1.0" in response.json()["detail"]
    assert settings_service.get("hourly_weight") is None


def test_bot_consumes_persisted_min_score_buy(isolated_settings_db):
    settings_service.save_typed("min_score_buy", 55)
    bot = _strategy_bot_stub()

    SmartTradingBot._load_effective_strategy_settings(bot)

    assert bot.min_score_buy == 55


def test_bot_consumes_effective_atr_position_size_pct_as_risk_sizing(
    isolated_settings_db,
):
    settings_service.save_typed("atr_position_size_pct", 3.5)
    bot = _strategy_bot_stub()

    SmartTradingBot._load_effective_strategy_settings(bot)

    assert bot.atr_position_size_pct == 3.5
    assert bot.risk_per_trade == pytest.approx(0.035)


def test_bot_loop_delay_uses_configured_value_when_no_explicit_argument(
    isolated_settings_db,
):
    settings_service.save_typed("loop_delay_seconds", 120)
    bot = _strategy_bot_stub()
    SmartTradingBot._load_effective_strategy_settings(bot)

    assert SmartTradingBot._resolve_loop_delay(bot) == 120


def test_bot_loop_delay_explicit_argument_overrides_configured_value(
    isolated_settings_db,
):
    settings_service.save_typed("loop_delay_seconds", 120)
    bot = _strategy_bot_stub()
    SmartTradingBot._load_effective_strategy_settings(bot)

    assert SmartTradingBot._resolve_loop_delay(bot, 5) == 5


def test_bot_startup_with_invalid_legacy_setting_uses_default_safely(
    isolated_settings_db, caplog
):
    settings_service.save("min_score_buy", "legacy-invalid")
    bot = _strategy_bot_stub()

    SmartTradingBot._load_effective_strategy_settings(bot)

    assert bot.min_score_buy == 50
    assert "Invalid persisted strategy setting min_score_buy" in caplog.text


def test_effective_settings_log_format_is_deterministic_and_bounded(
    isolated_settings_db,
):
    effective = settings_service.load_effective_strategy_settings()

    rendered = settings_service.format_effective_strategy_settings_for_log(effective)

    assert rendered.startswith("strategy_settings{")
    assert "min_score_buy=50" in rendered
    assert "ALPACA" not in rendered
    assert "SECRET" not in rendered
    assert rendered == settings_service.format_effective_strategy_settings_for_log(effective)
