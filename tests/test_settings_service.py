"""Deterministic tests for the shared dashboard/bot settings store."""

import pytest

from src.core import settings_service


@pytest.fixture
def isolated_settings_db(tmp_path, monkeypatch):
    db_path = tmp_path / "settings.db"
    monkeypatch.setattr(settings_service, "_DB_PATH", db_path)
    return db_path


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


def test_save_typed_serializes_normalized_schema_values(isolated_settings_db):
    settings_service.save_typed("enable_volume_confirmation", "off")
    settings_service.save_typed("sma_fast", "12.0")
    settings_service.save_typed("hourly_weight", "0.5")

    assert settings_service.get("enable_volume_confirmation") == "false"
    assert settings_service.get("sma_fast") == "12"
    assert settings_service.get("hourly_weight") == "0.5"


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
