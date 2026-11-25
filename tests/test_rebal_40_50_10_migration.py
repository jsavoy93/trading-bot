"""REBAL-40-50-10 — 60/30/10 -> 40/50/10 split plus the one-time migration.

Approved split (Josh, 2026-11-25): 40% main-bot long-term budget,
50% short-term (external swing runners C4 + RSI(2) + GAPFADE midcap
sharing the same Alpaca paper account), 10% cash floor.

Migration contract (see allocation_policy.normalize_persisted_long_term_budget_pct):
- a persisted value that EXACTLY equals the legacy shipped default 60.0 is
  rewritten ONCE to the current schema default through the normal
  settings_service.save_typed() write path;
- custom values (e.g. 50.0) are never touched;
- malformed values fail closed (never touched, never raise);
- a second run is a no-op (idempotent).
"""

import sqlite3

import pytest

from src.core import allocation_policy as ap
from src.core import settings_service
from src.core.smart_bot import SmartTradingBot

# smart_bot.py imports the second module instance `core.settings_service`
# (src is appended to sys.path at import time), so the bot's settings path
# must be patched through THOSE module objects, not src.core.*.
import core.settings_service as bot_settings_service
import core.allocation_policy as bot_allocation_policy


@pytest.fixture
def isolated_settings_db(tmp_path, monkeypatch):
    db_path = tmp_path / "settings.db"
    monkeypatch.setattr(settings_service, "_DB_PATH", db_path)
    return db_path


def _raw_persisted(db_path, key=ap.LONG_TERM_BUDGET_PCT_KEY):
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return None if row is None else row[0]


def _insert_raw(db_path, value, key=ap.LONG_TERM_BUDGET_PCT_KEY):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def _migrate():
    return ap.normalize_persisted_long_term_budget_pct(
        settings_service,
        settings_service.STRATEGY_SETTINGS_SCHEMA[ap.LONG_TERM_BUDGET_PCT_KEY].default,
    )


def _bot_stub():
    """Bot-shaped namespace without __init__ (no creds, no DB, no network).

    Carries only the attributes _load_effective_strategy_settings writes, so
    mocked tests do not need the full constructor.
    """
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.long_term_budget_pct = 60.0
    bot.cash_floor_pct = 10.0
    bot.swing_order_label_prefixes = '["sl_short-", "rsi2-", "gf_mid-"]'
    bot.swing_runner_db_paths = "[]"
    bot.atr_position_size_pct = 2.0
    bot.risk_per_trade = 0.02
    return bot


# ── 1. New authoritative defaults ───────────────────────────────────────────

class TestNewDefaults:
    def test_schema_defaults_are_40_50_10(self):
        assert settings_service.STRATEGY_SETTINGS_SCHEMA["long_term_budget_pct"].default == 40.0
        assert settings_service.STRATEGY_SETTINGS_SCHEMA["cash_floor_pct"].default == 10.0
        # 40% long-term + 50% short-term sleeve + 10% cash floor = 100%
        swing_sleeve_pct = 50.0
        assert (
            settings_service.STRATEGY_SETTINGS_SCHEMA["long_term_budget_pct"].default
            + swing_sleeve_pct
            + settings_service.STRATEGY_SETTINGS_SCHEMA["cash_floor_pct"].default
            == 100.0
        )

    def test_constructor_default_is_40(self):
        import inspect
        from src.core import smart_bot as smart_bot_module
        src = inspect.getsource(smart_bot_module.SmartTradingBot.__init__)
        lines = [ln for ln in src.splitlines() if "self.long_term_budget_pct" in ln]
        assert len(lines) == 1, lines
        assert lines[0].split("=", 1)[1].split("#")[0].strip() == "40.0"

    def test_no_60_default_left_in_code(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "src"
        offenders = []
        for path in root.rglob("*.py"):
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                if "long_term_budget_pct" in line and "60.0" in line:
                    offenders.append(f"{path.relative_to(root)}:{lineno}")
        assert offenders == []

    def test_effective_settings_without_db_row_use_40(self, isolated_settings_db):
        effective = settings_service.load_effective_strategy_settings()
        assert effective["long_term_budget_pct"] == 40.0
        assert effective["cash_floor_pct"] == 10.0

    def test_budget_math_40_50_10(self):
        equity = 100_000.0
        assert ap.long_term_budget(equity, 40.0) == pytest.approx(40_000.0)
        assert ap.cash_floor_amount(equity, 10.0) == pytest.approx(10_000.0)
        # short-term sleeve = the remaining non-cash, non-long-term share
        swing_sleeve = equity - ap.long_term_budget(equity, 40.0) - ap.cash_floor_amount(equity, 10.0)
        assert swing_sleeve == pytest.approx(50_000.0)


# ── 2. Migration: legacy 60.0 -> 40.0 ───────────────────────────────────────

class TestMigrationRewritesLegacyDefault:
    def test_persisted_60_is_rewritten_to_40(self, isolated_settings_db):
        _insert_raw(isolated_settings_db, "60.0")
        assert _migrate() is True
        assert _raw_persisted(isolated_settings_db) == "40.0"
        assert settings_service.load_effective_strategy_settings()["long_term_budget_pct"] == 40.0

    def test_integer_form_60_is_rewritten(self, isolated_settings_db):
        _insert_raw(isolated_settings_db, "60")
        assert _migrate() is True
        assert _raw_persisted(isolated_settings_db) == "40.0"

    def test_second_run_is_a_noop(self, isolated_settings_db):
        _insert_raw(isolated_settings_db, "60.0")
        assert _migrate() is True
        assert _migrate() is False
        assert _raw_persisted(isolated_settings_db) == "40.0"

    def test_no_persisted_row_is_a_noop(self, isolated_settings_db):
        assert _migrate() is False
        assert _raw_persisted(isolated_settings_db) is None

    def test_normalization_goes_through_save_typed(self, isolated_settings_db, monkeypatch):
        calls = []
        real_save_typed = settings_service.save_typed

        def _spy(key, value, param_type=None):
            calls.append((key, value))
            return real_save_typed(key, value, param_type)

        monkeypatch.setattr(settings_service, "save_typed", _spy)
        _insert_raw(isolated_settings_db, "60.0")
        assert _migrate() is True
        assert calls == [(ap.LONG_TERM_BUDGET_PCT_KEY, 40.0)]


# ── 3. Custom / malformed values are untouched (fail closed) ────────────────

class TestMigrationLeavesNonDefaultValuesAlone:
    @pytest.mark.parametrize("custom", ["50.0", "65.0", "0.0", "100.0", "60.5"])
    def test_custom_values_are_not_rewritten(self, isolated_settings_db, custom):
        _insert_raw(isolated_settings_db, custom)
        assert _migrate() is False
        assert _raw_persisted(isolated_settings_db) == custom

    @pytest.mark.parametrize("bad", ["not a number", "", "nan-ish", "60,0"])
    def test_malformed_values_fail_closed(self, isolated_settings_db, bad):
        _insert_raw(isolated_settings_db, bad)
        assert _migrate() is False
        assert _raw_persisted(isolated_settings_db) == bad
        # the normal load path still fails closed to the schema default
        assert settings_service.load_effective_strategy_settings()["long_term_budget_pct"] == 40.0

    def test_migration_never_raises_when_db_unavailable(self, monkeypatch):
        def _boom():
            raise sqlite3.OperationalError("locked")

        monkeypatch.setattr(settings_service, "_get_conn", _boom)
        assert _migrate() is False

    def test_pure_row_helper_ignores_other_keys(self):
        rows = [("cash_floor_pct", "60.0"), ("long_term_budget_pct", "60.0")]
        to_rewrite, legacy = ap.normalize_long_term_budget_pct_rows(rows, 40.0)
        assert to_rewrite == [("long_term_budget_pct", "60.0")]
        assert legacy == 60.0


# ── 4. Bot startup path applies the migration once ──────────────────────────

class TestBotStartupPath:
    def test_bot_module_aliases_resolve_to_the_same_source(self):
        assert bot_settings_service.__file__ == settings_service.__file__
        assert bot_allocation_policy.__file__ == ap.__file__
        assert callable(bot_allocation_policy.normalize_persisted_long_term_budget_pct)

    def test_bot_load_normalizes_legacy_value(self, tmp_path, monkeypatch):
        db_path = tmp_path / "bot_settings.db"
        monkeypatch.setattr(bot_settings_service, "_DB_PATH", db_path)
        _insert_raw(db_path, "60.0")
        bot = _bot_stub()
        SmartTradingBot._load_effective_strategy_settings(bot)
        assert bot.long_term_budget_pct == 40.0
        assert _raw_persisted(db_path) == "40.0"

    def test_bot_load_respects_custom_value(self, tmp_path, monkeypatch):
        db_path = tmp_path / "bot_settings_custom.db"
        monkeypatch.setattr(bot_settings_service, "_DB_PATH", db_path)
        _insert_raw(db_path, "55.0")
        bot = _bot_stub()
        SmartTradingBot._load_effective_strategy_settings(bot)
        assert bot.long_term_budget_pct == 55.0
        assert _raw_persisted(db_path) == "55.0"


# ── 5. Log-format fix + swing sleeve cap helpers ────────────────────────────

class TestMigrationLogFormat:
    def test_rewrite_log_message_formats_with_all_args(self, isolated_settings_db, caplog):
        """Regression: logger.info had 2 %s placeholders but 3 args."""
        _insert_raw(isolated_settings_db, "60.0")
        with caplog.at_level("INFO", logger=ap.logger.name):
            assert _migrate() is True
        msgs = [r.getMessage() for r in caplog.records
                if "normalizing persisted" in r.getMessage()]
        assert len(msgs) == 1
        text = msgs[0]
        assert ap.LONG_TERM_BUDGET_PCT_KEY in text
        assert "60.0" in text and "40.0" in text
        assert "%s" not in text and "{" not in text


class TestSwingSleeveCaps:
    def test_split_constants(self):
        assert ap.MAIN_BUDGET_PCT == 0.40
        assert ap.C4_SLEEVE_PCT == 0.10
        assert ap.RSI2_SLEEVE_PCT == 0.10
        assert ap.GAPFADE_TOTAL_CAP_PCT == 0.30
        assert ap.SWING_SLEEVE_TOTAL_PCT == 0.50
        assert ap.CASH_FLOOR_PCT == 0.10
        # worst case 40 + 10 + 10 + 30 = 90 deployed, 10 cash floor
        assert (ap.MAIN_BUDGET_PCT + ap.C4_SLEEVE_PCT + ap.RSI2_SLEEVE_PCT
                + ap.GAPFADE_TOTAL_CAP_PCT + ap.CASH_FLOOR_PCT) == pytest.approx(1.0)
        assert (ap.C4_SLEEVE_PCT + ap.RSI2_SLEEVE_PCT
                + ap.GAPFADE_TOTAL_CAP_PCT) == pytest.approx(ap.SWING_SLEEVE_TOTAL_PCT)

    @pytest.mark.parametrize("sleeve", [ap.C4_SLEEVE_PCT, ap.RSI2_SLEEVE_PCT,
                                        ap.GAPFADE_TOTAL_CAP_PCT])
    def test_cap_is_a_hard_ceiling(self, sleeve):
        eq = 100_000.0
        cap = eq * sleeve
        assert ap.swing_total_cap_exceeded(cap, eq, sleeve) is True   # at cap
        assert ap.swing_total_cap_exceeded(cap - 1.0, eq, sleeve) is False
        assert ap.swing_entry_budget(cap - 1.0, eq, sleeve) == pytest.approx(1.0)
        assert ap.swing_entry_budget(cap * 2, eq, sleeve) == 0.0

    @pytest.mark.parametrize("exp,eq,sleeve", [
        (None, 100_000.0, 0.10), (float("nan"), 100_000.0, 0.10),
        (1_000.0, None, 0.10), (1_000.0, 100_000.0, "x"),
        (1_000.0, -1.0, 0.10), (-5.0, 100_000.0, 0.10),
        (1_000.0, 0.0, 0.10), (1_000.0, 100_000.0, 0.0),
    ])
    def test_cap_fails_closed(self, exp, eq, sleeve):
        assert ap.swing_total_cap_exceeded(exp, eq, sleeve) is True
        assert ap.swing_entry_budget(exp, eq, sleeve) == 0.0
