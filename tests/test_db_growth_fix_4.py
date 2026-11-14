"""DB-GROWTH-FIX-4 — tests for the corrected calendar, the outcome-specific
closed-market control, the per-table gzip JSONL archives, the nightly prune
cap, and the archive/disk guards.

Every test runs against a small temporary SQLite database (tmp_path). The
production ``trading_bot.db`` is never opened.
"""

import gzip
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from test_db_growth_fix_acceptance import (  # noqa: E402,F401
    _fail_closed_production_db_guard,
    isolated_db,
    _buy_entry,
    _count,
    _hold_entry,
    _make_bot,
    _outcome_of,
    _persist,
    _sell_entry,
)


# ── 1. Pinned NYSE calendars ───────────────────────────────────────────────


EXPECTED_HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}

EXPECTED_HOLIDAYS_2027 = {
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}

EXPECTED_EARLY_CLOSES_2026 = {"2026-11-27", "2026-12-24"}
EXPECTED_EARLY_CLOSES_2027 = {"2027-11-26"}


class TestPinnedCalendar:
    def test_2026_full_closures_are_exact(self):
        from src.core import market_calendar

        actual = {
            d for d in market_calendar.MARKET_HOLIDAYS if d.startswith("2026-")
        }
        assert actual == EXPECTED_HOLIDAYS_2026

    def test_2027_full_closures_are_exact(self):
        from src.core import market_calendar

        actual = {
            d for d in market_calendar.MARKET_HOLIDAYS if d.startswith("2027-")
        }
        assert actual == EXPECTED_HOLIDAYS_2027

    def test_2026_early_closes_are_exact(self):
        from src.core import market_calendar

        actual = {
            d for d in market_calendar.EARLY_CLOSE_DAYS if d.startswith("2026-")
        }
        assert actual == EXPECTED_EARLY_CLOSES_2026

    def test_2027_early_closes_are_exact(self):
        from src.core import market_calendar

        actual = {
            d for d in market_calendar.EARLY_CLOSE_DAYS if d.startswith("2027-")
        }
        assert actual == EXPECTED_EARLY_CLOSES_2027

    def test_removed_and_corrected_dates(self):
        from src.core import market_calendar

        # 2026-11-03 is NOT an NYSE holiday.
        assert "2026-11-03" not in market_calendar.MARKET_HOLIDAYS
        # Good Friday 2027 is 2027-03-26, not 2027-04-02.
        assert "2027-04-02" not in market_calendar.MARKET_HOLIDAYS
        assert "2027-03-26" in market_calendar.MARKET_HOLIDAYS
        # No July 2 early closes.
        assert "2026-07-02" not in market_calendar.EARLY_CLOSE_DAYS
        assert "2027-07-02" not in market_calendar.EARLY_CLOSE_DAYS

    def test_no_holiday_is_also_an_early_close(self):
        from src.core import market_calendar

        assert not (
            market_calendar.MARKET_HOLIDAYS & market_calendar.EARLY_CLOSE_DAYS
        )

    def test_full_closure_day_is_not_open(self):
        from src.core import market_calendar

        # 2026-07-03 (Independence Day observed) is a full closure.
        when = datetime(2026, 7, 3, 12, 0, tzinfo=market_calendar.NY_TZ)
        assert market_calendar.is_market_open(when) is False

    def test_early_close_day_closes_at_13_00(self):
        from src.core import market_calendar

        when = datetime(2026, 11, 27, 12, 59, tzinfo=market_calendar.NY_TZ)
        assert market_calendar.is_market_open(when) is True
        when = datetime(2026, 11, 27, 13, 30, tzinfo=market_calendar.NY_TZ)
        assert market_calendar.is_market_open(when) is False


# ── 2. Outcome-specific closed-market behaviour ───────────────────────────


class TestClosedMarketIsOutcomeSpecific:
    @pytest.fixture(autouse=True)
    def _closed_market(self, monkeypatch):
        import src.core.smart_bot as smart_bot_module

        monkeypatch.setattr(
            smart_bot_module.market_calendar, "is_market_open",
            lambda *a, **k: False,
        )

    def test_closed_market_skips_hold(self, isolated_db):
        bot = _make_bot(isolated_db, suppress=False, market_hours_only=True)
        _persist(bot, _hold_entry("CLOSEDH"), "fx4_c1")
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_history",
        ) == 0

    def test_closed_market_still_writes_buy_and_sell(self, isolated_db):
        bot = _make_bot(isolated_db, suppress=False, market_hours_only=True)
        _persist(bot, _buy_entry("CLOSEDB"), "fx4_c2")
        _persist(bot, _sell_entry("CLOSEDS"), "fx4_c3")
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_history",
        ) == 2
        outcomes = {
            _outcome_of(isolated_db, "CLOSEDB"),
            _outcome_of(isolated_db, "CLOSEDS"),
        }
        assert "HOLD_INELIGIBLE" not in outcomes

    def test_closed_market_writes_children_for_buy_rows(self, isolated_db):
        bot = _make_bot(isolated_db, suppress=False, market_hours_only=True)
        _persist(bot, _buy_entry("CLOSEDCH"), "fx4_c4")
        assert _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_gate_evaluations",
        ) >= 1
        assert _count(
            isolated_db,
            "SELECT COUNT(*) FROM decision_execution_checks",
        ) >= 1

    def test_skippable_outcome_set_is_exactly_the_two_low_information_ones(
        self, isolated_db,
    ):
        from src.core.smart_bot import SmartTradingBot

        assert SmartTradingBot.DB_GROWTH_CLOSED_MARKET_SKIPPABLE_OUTCOMES == {
            "HOLD_INELIGIBLE", "SKIPPED_INVALID_DATA",
        }

    def test_closed_market_skip_is_outcome_specific(self, isolated_db):
        """HOLD is skipped while a BUY for the same symbol is kept."""
        bot = _make_bot(isolated_db, suppress=False, market_hours_only=True)
        _persist(bot, _hold_entry("MIXED"), "fx4_c5")
        _persist(bot, _buy_entry("MIXED"), "fx4_c6")
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_history",
        ) == 1
        assert _outcome_of(isolated_db, "MIXED") != "HOLD_INELIGIBLE"

    def test_market_hours_only_disabled_still_writes_hold(self, isolated_db):
        bot = _make_bot(isolated_db, suppress=False, market_hours_only=False)
        _persist(bot, _hold_entry("ALWAYS"), "fx4_c7")
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_history",
        ) == 1

    def test_calendar_failure_keeps_the_row(self, isolated_db, monkeypatch):
        import src.core.smart_bot as smart_bot_module

        def _boom(*a, **k):
            raise RuntimeError("calendar exploded")

        monkeypatch.setattr(
            smart_bot_module.market_calendar, "is_market_open", _boom,
        )
        bot = _make_bot(isolated_db, suppress=False, market_hours_only=True)
        _persist(bot, _buy_entry("CALCFAIL"), "fx4_c8")
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_history",
        ) == 1


# ── 3. Per-table gzip JSONL archives ──────────────────────────────────────


def _seed_rows(isolated_db, count=2, blob=False):
    _db, sqlite_db_module, _p = isolated_db
    cycle_start = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    ids = []
    with sqlite_db_module._get_conn() as conn:
        for i in range(count):
            conn.execute(
                "INSERT INTO decision_history (cycle_id, symbol, cycle_start, "
                "decision_schema_version, decision_snapshot, "
                "analytics_persistence_version) VALUES (?,?,?,?,?,1)",
                (f"fx4_seed_{i}", f"SYM{i}", cycle_start, 1,
                 json.dumps({"decision": {"outcome": "HOLD_INELIGIBLE"}})),
            )
            pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ids.append(pid)
            conn.execute(
                "INSERT INTO decision_gate_evaluations (decision_history_id, "
                "cycle_id, symbol, cycle_start, ordinality, gate_name, "
                "applied, passed, observed_value, threshold_value, reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (pid, f"fx4_seed_{i}", f"SYM{i}", cycle_start, 0,
                 "strategy_gate", 1, 0, 45.0, 30.0, "seeded"),
            )
            conn.execute(
                "INSERT INTO decision_execution_checks (decision_history_id, "
                "cycle_id, symbol, cycle_start, ordinality, check_name, "
                "applied, passed, observed_value, threshold_value, reason, "
                "gap_note, is_first_blocking) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, f"fx4_seed_{i}", f"SYM{i}", cycle_start, 0,
                 "existing_position_check", 1, 0, 1.0, 0.0, "seeded", None, 1),
            )
    return ids


def _read_archive(path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class TestPerTableArchives:
    def test_one_archive_file_per_table(self, isolated_db, tmp_path):
        _db, sqlite_db_module, _p = isolated_db
        _seed_rows(isolated_db, count=2)
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=5000,
            archive_dir=str(tmp_path / "archive"),
        )
        assert result["error"] is None, result["error"]
        names = sorted(
            pathlib.Path(p).name.split("_2026")[0]
            for p in result["archived_files"]
        )
        assert names == [
            "decision_execution_checks",
            "decision_gate_evaluations",
            "decision_history",
        ]

    def test_archives_land_in_a_dated_subfolder(self, isolated_db, tmp_path):
        _db, sqlite_db_module, _p = isolated_db
        _seed_rows(isolated_db, count=1)
        archive_root = tmp_path / "archive"
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=5000, archive_dir=str(archive_root),
        )
        assert result["error"] is None
        for path in result["archived_files"]:
            assert pathlib.Path(path).parent.parent == archive_root
            assert pathlib.Path(path).parent.name == datetime.now(
                timezone.utc
            ).strftime("%Y-%m-%d")

    def test_existing_archive_files_are_never_overwritten(self, isolated_db,
                                                            tmp_path):
        _db, sqlite_db_module, _p = isolated_db
        archive_root = tmp_path / "archive"
        dated = archive_root / datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dated.mkdir(parents=True)
        existing = dated / "decision_history_keepme.jsonl.gz"
        existing.write_bytes(b"do-not-touch")
        _seed_rows(isolated_db, count=1)
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=5000, archive_dir=str(archive_root),
        )
        assert result["error"] is None
        assert existing.read_bytes() == b"do-not-touch"
        assert existing not in {pathlib.Path(p) for p in result["archived_files"]}

    def test_all_columns_are_archived(self, isolated_db, tmp_path):
        _db, sqlite_db_module, _p = isolated_db
        ids = _seed_rows(isolated_db, count=2)
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=5000,
            archive_dir=str(tmp_path / "archive"),
        )
        assert result["error"] is None
        by_table = {
            pathlib.Path(p).name.split("_2026")[0]: p
            for p in result["archived_files"]
        }
        parents = _read_archive(by_table["decision_history"])
        assert sorted(int(row["id"]) for row in parents) == sorted(ids)
        for row in parents:
            for column in ("id", "cycle_id", "symbol", "cycle_start",
                           "decision_snapshot", "analytics_persistence_version"):
                assert column in row
        gates = _read_archive(by_table["decision_gate_evaluations"])
        assert len(gates) == 2
        for row in gates:
            for column in ("id", "decision_history_id", "cycle_id", "symbol",
                           "ordinality", "gate_name", "applied", "passed"):
                assert column in row
        checks = _read_archive(by_table["decision_execution_checks"])
        assert len(checks) == 2
        for row in checks:
            for column in ("id", "decision_history_id", "check_name",
                           "is_first_blocking"):
                assert column in row

    def test_blob_values_are_base64_encoded(self, isolated_db, tmp_path):
        _db, sqlite_db_module, _p = isolated_db
        with sqlite_db_module._get_conn() as conn:
            columns = [
                r[1] for r in conn.execute("PRAGMA table_info(decision_history)")
            ]
            assert "decision_snapshot" not in columns or True
        # Find a BLOB column to exercise the encoder directly.
        encoded = sqlite_db_module.SQLiteDB._encode_archive_value(b"\x00\x01\x02")
        assert isinstance(encoded, dict)
        assert "__blob_b64__" in encoded
        import base64
        assert base64.b64decode(encoded["__blob_b64__"]) == b"\x00\x01\x02"

    def test_archive_failure_deletes_nothing_and_removes_partial_files(
        self, isolated_db, tmp_path, monkeypatch,
    ):
        """A failure while writing the archive deletes NO row and leaves
        no partial archive file behind."""
        _db, sqlite_db_module, _p = isolated_db
        _seed_rows(isolated_db, count=3)

        original = sqlite_db_module.SQLiteDB._encode_archive_value
        calls = {"n": 0}

        def _boom(value):
            calls["n"] += 1
            if calls["n"] > 1:
                raise OSError("disk full")
            return original(value)

        monkeypatch.setattr(
            sqlite_db_module.SQLiteDB, "_encode_archive_value",
            staticmethod(_boom),
        )
        archive_dir = tmp_path / "archive"
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=5000, archive_dir=str(archive_dir),
        )
        assert result["error"] is not None
        assert "archive_write_failed" in result["error"]
        assert result["parents_deleted"] == 0
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_history",
        ) == 3
        leftovers = list(archive_dir.rglob("*.jsonl.gz"))
        assert leftovers == [], f"partial archives left behind: {leftovers}"

    def test_missing_archive_dir_is_refused(self, isolated_db):
        _db, sqlite_db_module, _p = isolated_db
        _seed_rows(isolated_db, count=2)
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=5000, archive_dir=None,
        )
        assert result["error"] is not None
        assert "archive_dir_unavailable" in result["error"]
        assert result["parents_deleted"] == 0
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_history",
        ) == 2

    def test_unusable_archive_dir_is_refused(self, isolated_db, tmp_path):
        _db, sqlite_db_module, _p = isolated_db
        _seed_rows(isolated_db, count=1)
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=5000, archive_dir=str(blocker / "arc"),
        )
        assert result["error"] is not None
        assert "archive_dir_unavailable" in result["error"]
        assert result["parents_deleted"] == 0


# ── 4. Nightly cap and disk guard ─────────────────────────────────────────


class TestPruneCapAndDiskGuard:
    def test_nightly_cap_stops_the_prune(self, isolated_db, tmp_path):
        _db, sqlite_db_module, _p = isolated_db
        _seed_rows(isolated_db, count=6)
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=2, max_rows_per_night=3,
            archive_dir=str(tmp_path / "archive"),
        )
        assert result["error"] is None
        assert result["parents_deleted"] == 3
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_history",
        ) == 3

    def test_default_cap_setting_is_200000(self):
        from src.core import settings_service

        definition = settings_service.STRATEGY_SETTINGS_SCHEMA[
            "decision_history_prune_max_rows_per_night"
        ]
        assert definition.default == 200000

    def test_bot_default_cap_attribute(self):
        """The bot's own default for the nightly cap is 200000."""
        import inspect

        from src.core.smart_bot import SmartTradingBot

        source = inspect.getsource(SmartTradingBot.__init__)
        assert "decision_history_prune_max_rows_per_night = 200000" in source

    def test_prune_hook_is_skipped_when_free_space_is_low(
        self, isolated_db, tmp_path, monkeypatch,
    ):
        import src.core.smart_bot as smart_bot_module

        monkeypatch.setattr(
            smart_bot_module.market_calendar, "is_market_open",
            lambda *a, **k: False,
        )
        bot = _make_bot(isolated_db, prune_enabled=True,
                        archive_dir=str(tmp_path / "archive"))
        calls = []
        monkeypatch.setattr(
            bot, "run_decision_history_retention_prune",
            lambda: calls.append(1),
        )

        class _Usage:
            free = 1024 * 1024  # 1 MB — far below 2 GB

        monkeypatch.setattr(
            smart_bot_module.shutil, "disk_usage", lambda p: _Usage(),
        )
        bot._maybe_run_retention_prune()
        assert calls == []

    def test_prune_hook_runs_when_free_space_is_sufficient(
        self, isolated_db, tmp_path, monkeypatch,
    ):
        import src.core.smart_bot as smart_bot_module

        monkeypatch.setattr(
            smart_bot_module.market_calendar, "is_market_open",
            lambda *a, **k: False,
        )
        bot = _make_bot(isolated_db, prune_enabled=True,
                        archive_dir=str(tmp_path / "archive"))
        calls = []
        monkeypatch.setattr(
            bot, "run_decision_history_retention_prune",
            lambda: calls.append(1),
        )

        class _Usage:
            free = 10 * 1024 * 1024 * 1024  # 10 GB

        monkeypatch.setattr(
            smart_bot_module.shutil, "disk_usage", lambda p: _Usage(),
        )
        bot._maybe_run_retention_prune()
        assert calls == [1]


# ── 5. Archive directory resolution ───────────────────────────────────────


class TestArchiveDirResolution:
    def test_relative_default_lands_beside_the_database(self, isolated_db):
        _db, sqlite_db_module, path = isolated_db
        bot = _make_bot(isolated_db, archive_dir="research_data/db_archive")
        resolved = pathlib.Path(bot._resolve_archive_dir())
        assert resolved.is_absolute()
        assert resolved == pathlib.Path(str(path)).parent / "research_data" / \
            "db_archive"

    def test_absolute_configuration_wins(self, isolated_db, tmp_path):
        explicit = tmp_path / "explicit_archive"
        bot = _make_bot(isolated_db, archive_dir=str(explicit))
        assert pathlib.Path(bot._resolve_archive_dir()) == explicit

    def test_empty_setting_disables_archiving(self, isolated_db):
        bot = _make_bot(isolated_db, archive_dir="")
        assert bot._resolve_archive_dir() is None
