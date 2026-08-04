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
    ],
)
def test_load_typed_rejects_invalid_numeric_overrides(
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
