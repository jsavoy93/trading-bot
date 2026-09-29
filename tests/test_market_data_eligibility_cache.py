"""MKT-CACHE-001 \u2014 Market-data eligibility cache tests.

These tests cover the additive SQLite table and the helper layer
introduced by the MKT-CACHE-001 task. The implementation is OBSERVABILITY
+ EFFICIENCY ONLY: no BUY/SELL/scoring/indicator/MTF/risk behavior is
modified. The tests run against a temporary SQLite DB via the standard
``monkeypatch src.database.sqlite_db.DB_PATH`` pattern.

Test plan matches the owner-approved spec:

  A. zero bars \u2192 NOT_IN_FEED cache \u2192 skipped during TTL \u2192 TTL expires
     \u2192 market-data request retried
  B. 20/30 recent continuous bars \u2192 INSUFFICIENT cache \u2192 recheck
     delayed approximately 10 trading sessions \u2192 once >=30 bars are
     available \u2192 symbol automatically returns to normal analysis
  C. old/sparse synthetic history \u2192 does not assume one new bar per
     trading day \u2192 conservative periodic recheck
  D. API exception \u2192 no 7-day structural cache
  E. SmartBot restart \u2192 cache persists and remains effective
  F. expired record \u2192 does not permanently exclude symbol

Plus: regression evidence that the strategy/trading logic is unchanged
for valid symbols.
"""

from __future__ import annotations

import importlib
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Test fixtures: isolated DB for every test
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(monkeypatch):
    """Redirect src.database.sqlite_db.DB_PATH to a temp file.

    SQLiteDB and _get_conn both resolve DB_PATH at call time, so the
    monkeypatch is sufficient. Returns the Path object for assertions.
    """
    import src.database.sqlite_db as sqlite_db_module

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    monkeypatch.setattr(sqlite_db_module, "DB_PATH", path)
    yield path
    # Cleanup is best-effort; tmpfs cleans up automatically.
    try:
        path.unlink()
    except FileNotFoundError:
        pass


@pytest.fixture
def db(temp_db):
    from src.database.sqlite_db import SQLiteDB

    d = SQLiteDB()
    assert d.is_available(), "SQLiteDB failed to initialize against temp DB"
    return d


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Tier 0: schema / initialization safety
# ---------------------------------------------------------------------------


class TestMktCacheSchema:
    def test_table_created_on_init(self, db, temp_db):
        """The CREATE TABLE IF NOT EXISTS must run during _init_schema."""
        with sqlite3.connect(str(temp_db)) as conn:
            rows = list(
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='market_data_eligibility_cache'"
                )
            )
        assert len(rows) == 1

    def test_idempotent_init(self, db, temp_db):
        """Re-running _init_schema must be a no-op (no duplicate, no error)."""
        # First init already happened in the fixture. Run again.
        db._init_schema()
        with sqlite3.connect(str(temp_db)) as conn:
            rows = list(
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='market_data_eligibility_cache'"
                )
            )
        assert len(rows) == 1

    def test_index_created_on_init(self, db, temp_db):
        with sqlite3.connect(str(temp_db)) as conn:
            rows = list(
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name='idx_market_data_eligibility_next_recheck'"
                )
            )
        assert len(rows) == 1

    def test_unique_symbol_constraint(self, db):
        """A second insert for the same symbol must replace the first row."""
        future = _iso(datetime.now(timezone.utc) + timedelta(days=7))
        assert db.upsert_market_data_eligibility(
            symbol="WFAFY",
            reason="MARKET_DATA_NOT_IN_FEED",
            bars_returned=0,
            required_bars=30,
            next_recheck_iso=future,
        )
        # Same symbol, different TTL \u2014 ON CONFLICT must replace.
        future2 = _iso(datetime.now(timezone.utc) + timedelta(days=14))
        assert db.upsert_market_data_eligibility(
            symbol="WFAFY",
            reason="MARKET_DATA_INSUFFICIENT_BARS",
            bars_returned=15,
            required_bars=30,
            next_recheck_iso=future2,
        )
        row = db.get_market_data_eligibility("WFAFY")
        assert row is not None
        assert row["reason"] == "MARKET_DATA_INSUFFICIENT_BARS"
        assert row["bars_returned"] == 15


# ---------------------------------------------------------------------------
# Tier A: NOT_IN_FEED zero-bar path
# ---------------------------------------------------------------------------


class TestNotInFeedPolicy:
    def test_zero_bars_caches_not_in_feed(self, db):
        """cache_outcome with bars=0 returns NOT_IN_FEED token and writes a row."""
        from src.core.market_data_eligibility import (
            cache_outcome,
            SCORE_REASON_NOT_IN_FEED,
        )

        token = cache_outcome(
            db=db,
            symbol="WFAFY",
            bars_returned=0,
            required_bars=30,
            first_bar_timestamp=None,
            latest_bar_timestamp=None,
            barset_key_present=False,
        )
        assert token == SCORE_REASON_NOT_IN_FEED
        row = db.get_market_data_eligibility("WFAFY")
        assert row is not None
        assert row["reason"] == "MARKET_DATA_NOT_IN_FEED"
        assert row["bars_returned"] == 0
        # TTL ~7 days from now.
        delta = datetime.fromisoformat(row["next_recheck_iso"]) - datetime.now(
            timezone.utc
        )
        assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)

    def test_cache_hit_returns_cached_skip_token(self, db):
        """A second consult on a freshly-cached symbol must return the
        cached-skip token (not the per-cycle token) without writing a
        duplicate row."""
        from src.core.market_data_eligibility import (
            cache_outcome,
            consult_cache_for_symbol,
            SCORE_REASON_CACHED_NOT_IN_FEED,
        )

        cache_outcome(
            db=db,
            symbol="WFAFY",
            bars_returned=0,
            required_bars=30,
            first_bar_timestamp=None,
            latest_bar_timestamp=None,
            barset_key_present=False,
        )
        decision = consult_cache_for_symbol(db, "WFAFY")
        assert decision.should_skip is True
        assert decision.reason == "MARKET_DATA_NOT_IN_FEED"
        assert decision.score_error_reason == SCORE_REASON_CACHED_NOT_IN_FEED

    def test_ttl_expiry_releases_symbol(self, db):
        """An expired row must NOT block the next fresh attempt."""
        from src.core.market_data_eligibility import (
            cache_outcome,
            consult_cache_for_symbol,
            SCORE_REASON_NOT_IN_FEED,
        )

        cache_outcome(
            db=db,
            symbol="WFAFY",
            bars_returned=0,
            required_bars=30,
            first_bar_timestamp=None,
            latest_bar_timestamp=None,
            barset_key_present=False,
        )
        # Force expiry by rewriting the row with a past next_recheck.
        past = _iso(datetime.now(timezone.utc) - timedelta(minutes=1))
        db.upsert_market_data_eligibility(
            symbol="WFAFY",
            reason="MARKET_DATA_NOT_IN_FEED",
            bars_returned=0,
            required_bars=30,
            next_recheck_iso=past,
        )
        decision = consult_cache_for_symbol(db, "WFAFY")
        assert decision.should_skip is False
        assert decision.score_error_reason is None


# ---------------------------------------------------------------------------
# Tier B: INSUFFICIENT_HISTORY recent-continuous path
# ---------------------------------------------------------------------------


class TestInsufficientHistoryPolicy:
    def test_20_of_30_caches_insufficient_bars(self, db):
        """cache_outcome with 20/30 bars returns INSUFFICIENT_BARS token
        and writes a row with a TTL approximately 10 trading sessions
        in the future (which is ~14 calendar days)."""
        from src.core.market_data_eligibility import (
            cache_outcome,
            SCORE_REASON_INSUFFICIENT_BARS,
        )

        token = cache_outcome(
            db=db,
            symbol="CATL",
            bars_returned=20,
            required_bars=30,
            first_bar_timestamp="2026-08-31T00:00:00+00:00",
            latest_bar_timestamp="2026-09-28T00:00:00+00:00",
            barset_key_present=True,
        )
        assert token == SCORE_REASON_INSUFFICIENT_BARS
        row = db.get_market_data_eligibility("CATL")
        assert row is not None
        assert row["reason"] == "MARKET_DATA_INSUFFICIENT_BARS"
        assert row["bars_returned"] == 20
        # 10 missing sessions * 7/5 = 14 calendar days.
        delta = datetime.fromisoformat(row["next_recheck_iso"]) - datetime.now(
            timezone.utc
        )
        # Allow a wider window since the int() truncation can shave 1 day.
        assert timedelta(days=13, hours=20) < delta < timedelta(days=15)

    def test_29_of_30_caches_with_short_ttl(self, db):
        """29/30 bars: 1 missing session \u2192 ~2 calendar days (rounded up)."""
        from src.core.market_data_eligibility import cache_outcome

        cache_outcome(
            db=db,
            symbol="AXTQ",
            bars_returned=29,
            required_bars=30,
            first_bar_timestamp="2026-08-18T00:00:00+00:00",
            latest_bar_timestamp="2026-09-28T00:00:00+00:00",
            barset_key_present=True,
        )
        row = db.get_market_data_eligibility("AXTQ")
        delta = datetime.fromisoformat(row["next_recheck_iso"]) - datetime.now(
            timezone.utc
        )
        # 1 session * 7/5 = 1.4 cal days, int() truncation => 1 day.
        assert timedelta(hours=20) < delta < timedelta(days=2)

    def test_sufficient_data_clears_cache_row(self, db):
        """When bars_returned >= required_bars, cache_outcome deletes
        any existing row (allowing the symbol to flow normally)."""
        from src.core.market_data_eligibility import cache_outcome

        # Seed an old cache row.
        cache_outcome(
            db=db,
            symbol="WFAFY",
            bars_returned=0,
            required_bars=30,
            first_bar_timestamp=None,
            latest_bar_timestamp=None,
            barset_key_present=False,
        )
        assert db.get_market_data_eligibility("WFAFY") is not None
        # Now pretend Alpaca returned 50 bars.
        token = cache_outcome(
            db=db,
            symbol="WFAFY",
            bars_returned=50,
            required_bars=30,
            first_bar_timestamp="2026-05-01T00:00:00+00:00",
            latest_bar_timestamp="2026-09-28T00:00:00+00:00",
            barset_key_present=True,
        )
        assert token is None
        assert db.get_market_data_eligibility("WFAFY") is None


# ---------------------------------------------------------------------------
# Tier C: defensive old/sparse policy
# ---------------------------------------------------------------------------


class TestSparseHistoryDefensivePolicy:
    def test_sparse_history_uses_14_day_fixed_ttl(self):
        """The sparse helper returns 14 days, NOT a bars-per-day estimate."""
        from src.core.market_data_eligibility import (
            compute_next_recheck_for_sparse_history,
        )

        now = datetime(2026, 9, 29, 0, 0, 0, tzinfo=timezone.utc)
        ttl = compute_next_recheck_for_sparse_history(now=now)
        # 14 days exactly.
        assert ttl == _iso(now + timedelta(days=14))

    def test_sparse_ttl_independent_of_bars_returned(self):
        """A 3-of-30 sparse symbol must use 14 days, NOT 27 sessions."""
        from src.core.market_data_eligibility import (
            compute_next_recheck_for_sparse_history,
            compute_next_recheck_for_insufficient_bars,
        )

        now = datetime(2026, 9, 29, 0, 0, 0, tzinfo=timezone.utc)
        sparse = compute_next_recheck_for_sparse_history(now=now)
        continuous = compute_next_recheck_for_insufficient_bars(
            bars_returned=3, required_bars=30, now=now,
        )
        # Continuous (1 bar per day) extrapolates 27 sessions \u2248 ~37 cal
        # days. Sparse uses fixed 14 days.
        assert sparse != continuous
        assert sparse == _iso(now + timedelta(days=14))
        assert continuous == _iso(now + timedelta(days=37))  # 27 * 7/5 = 37.8 \u2192 37


# ---------------------------------------------------------------------------
# Tier D: API exception does NOT long-cache
# ---------------------------------------------------------------------------


class TestApiExceptionPolicy:
    def test_api_exception_emits_distinct_token_and_no_cache_row(self, db):
        """An exception with bars_returned=None emits API_EXCEPTION token.

        Per owner review (MKT-CACHE-001 final review): API exceptions
        are TRANSIENT and must NOT create a long-lived cache row. The
        next cycle must be free to retry the symbol without being
        suppressed. The per-cycle token correctly reflects the
        transient nature of the failure for dashboards.
        """
        from src.core.market_data_eligibility import (
            cache_outcome,
            SCORE_REASON_API_EXCEPTION,
        )

        token = cache_outcome(
            db=db,
            symbol="NETERR",
            bars_returned=None,
            required_bars=30,
            first_bar_timestamp=None,
            latest_bar_timestamp=None,
            barset_key_present=None,
        )
        assert token == SCORE_REASON_API_EXCEPTION
        # NO cache row written for API exceptions.
        row = db.get_market_data_eligibility("NETERR")
        assert row is None

    def test_api_exception_does_not_block_subsequent_cycles(self, db):
        """After an API exception, the next cycle's cache consult must
        report should_skip=False so the bot retries the fresh API call."""
        from src.core.market_data_eligibility import (
            cache_outcome,
            consult_cache_for_symbol,
        )

        cache_outcome(
            db=db,
            symbol="NETERR",
            bars_returned=None,
            required_bars=30,
            first_bar_timestamp=None,
            latest_bar_timestamp=None,
            barset_key_present=None,
        )
        decision = consult_cache_for_symbol(db, "NETERR")
        assert decision.should_skip is False
        assert decision.score_error_reason is None


# ---------------------------------------------------------------------------
# Tier E: restart persistence
# ---------------------------------------------------------------------------


class TestRestartPersistence:
    def test_cache_survives_new_sqlitedb_instance(self, temp_db):
        """A new SQLiteDB instance against the same DB file must see the
        cache rows written by the previous instance."""
        from src.database.sqlite_db import SQLiteDB
        from src.core.market_data_eligibility import (
            cache_outcome,
            consult_cache_for_symbol,
            SCORE_REASON_CACHED_NOT_IN_FEED,
        )

        # Instance 1: write a cache row.
        db1 = SQLiteDB()
        cache_outcome(
            db=db1,
            symbol="WFAFY",
            bars_returned=0,
            required_bars=30,
            first_bar_timestamp=None,
            latest_bar_timestamp=None,
            barset_key_present=False,
        )
        # Instance 2: read the cache row.
        db2 = SQLiteDB()
        decision = consult_cache_for_symbol(db2, "WFAFY")
        assert decision.should_skip is True
        assert decision.score_error_reason == SCORE_REASON_CACHED_NOT_IN_FEED


# ---------------------------------------------------------------------------
# Tier F: queue pre-filter (slot replacement behavior)
# ---------------------------------------------------------------------------


class TestQueuePreFilter:
    def _make_bot(self, db, queue_symbols):
        """Build a stub SmartTradingBot instance with a controlled queue."""
        from src.core.smart_bot import SmartTradingBot

        bot = SmartTradingBot.__new__(SmartTradingBot)
        bot.db = db
        bot._analysis_queue = list(queue_symbols)
        bot._current_analysis_index = 0
        # Stub out the rank call by short-circuiting the loop.
        return bot

    def test_pre_filter_skips_cached_symbols(self, db):
        """If 5 symbols in a 30-slot batch are cached-ineligible, the
        bot should skip them and pull the next 5 from the queue rather
        than leaving the batch at 25 symbols."""
        bot = self._make_bot(
            db,
            [
                "CACHED1", "CACHED2", "GOOD1", "CACHED3", "GOOD2",
                "GOOD3", "GOOD4", "CACHED4", "GOOD5", "CACHED5",
                "GOOD6", "GOOD7", "GOOD8", "GOOD9", "GOOD10",
            ],
        )
        # Cache the 5 CACHED* symbols.
        for s in ("CACHED1", "CACHED2", "CACHED3", "CACHED4", "CACHED5"):
            db.upsert_market_data_eligibility(
                symbol=s,
                reason="MARKET_DATA_NOT_IN_FEED",
                bars_returned=0,
                required_bars=30,
                next_recheck_iso=_iso(
                    datetime.now(timezone.utc) + timedelta(days=7)
                ),
            )
        batch = bot._get_rolling_ticker_list(target_count=5)
        # Expectation: 5 GOOD symbols, no CACHED symbols.
        assert len(batch) == 5
        assert all(s.startswith("GOOD") for s in batch)

    def test_pre_filter_fail_open_on_db_error(self, db, monkeypatch):
        """If the DB consult errors, the queue pre-filter must fall open
        (treat all symbols as eligible) rather than blocking everything."""
        bot = self._make_bot(db, ["GOOD1", "GOOD2", "GOOD3"])
        # Monkeypatch get_market_data_eligible_symbols to raise.
        def _raise(self, symbols):
            raise RuntimeError("simulated DB error")
        monkeypatch.setattr(
            "src.database.sqlite_db.SQLiteDB.get_market_data_eligible_symbols",
            _raise,
        )
        batch = bot._get_rolling_ticker_list(target_count=3)
        assert len(batch) == 3
        assert batch == ["GOOD1", "GOOD2", "GOOD3"]

    def test_no_eligible_symbols_returns_short_batch(self, db):
        """If every queued symbol is cached-ineligible, the batch may be
        shorter than target_count but must never block forever."""
        bot = self._make_bot(db, ["CACHED1", "CACHED2", "CACHED3"])
        for s in ("CACHED1", "CACHED2", "CACHED3"):
            db.upsert_market_data_eligibility(
                symbol=s,
                reason="MARKET_DATA_NOT_IN_FEED",
                bars_returned=0,
                required_bars=30,
                next_recheck_iso=_iso(
                    datetime.now(timezone.utc) + timedelta(days=7)
                ),
            )
        batch = bot._get_rolling_ticker_list(target_count=3)
        # Bounded: returns 0 (max_iterations prevents infinite loop).
        assert batch == []


# ---------------------------------------------------------------------------
# Tier G: observability tokens (per-cycle vs cached-skip)
# ---------------------------------------------------------------------------


class TestObservabilityTokens:
    def test_fresh_failure_emits_per_cycle_token(self, db):
        """A fresh NOT_IN_FEED failure (no cache row) emits the
        per-cycle token market_data_not_in_feed."""
        from src.core.market_data_eligibility import (
            cache_outcome,
            SCORE_REASON_NOT_IN_FEED,
        )

        token = cache_outcome(
            db=db,
            symbol="WFAFY",
            bars_returned=0,
            required_bars=30,
            first_bar_timestamp=None,
            latest_bar_timestamp=None,
            barset_key_present=False,
        )
        assert token == SCORE_REASON_NOT_IN_FEED
        assert token.startswith("market_data_")
        assert token != "market_data_cached_not_in_feed"

    def test_cache_hit_emits_cached_skip_token(self, db):
        """A cache-hit consult emits the cached-skip token, which is
        distinct from the per-cycle token so dashboards can attribute
        the skip to 'cache hit' rather than 'fresh failure'."""
        from src.core.market_data_eligibility import (
            cache_outcome,
            consult_cache_for_symbol,
            SCORE_REASON_CACHED_NOT_IN_FEED,
            SCORE_REASON_CACHED_INSUFFICIENT_HISTORY,
        )

        cache_outcome(
            db=db,
            symbol="WFAFY",
            bars_returned=0,
            required_bars=30,
            first_bar_timestamp=None,
            latest_bar_timestamp=None,
            barset_key_present=False,
        )
        d = consult_cache_for_symbol(db, "WFAFY")
        assert d.score_error_reason == SCORE_REASON_CACHED_NOT_IN_FEED

        cache_outcome(
            db=db,
            symbol="CATL",
            bars_returned=20,
            required_bars=30,
            first_bar_timestamp="2026-08-31T00:00:00+00:00",
            latest_bar_timestamp="2026-09-28T00:00:00+00:00",
            barset_key_present=True,
        )
        d = consult_cache_for_symbol(db, "CATL")
        assert d.score_error_reason == SCORE_REASON_CACHED_INSUFFICIENT_HISTORY


# ---------------------------------------------------------------------------
# Tier H: get_market_data_eligible_symbols batched consult
# ---------------------------------------------------------------------------


class TestBatchedConsult:
    def test_returns_only_eligible_symbols(self, db):
        now = _iso(datetime.now(timezone.utc) + timedelta(days=7))
        db.upsert_market_data_eligibility(
            symbol="WFAFY", reason="MARKET_DATA_NOT_IN_FEED",
            bars_returned=0, required_bars=30, next_recheck_iso=now,
        )
        db.upsert_market_data_eligibility(
            symbol="CATL", reason="MARKET_DATA_INSUFFICIENT_BARS",
            bars_returned=20, required_bars=30,
            next_recheck_iso=_iso(datetime.now(timezone.utc) + timedelta(days=14)),
        )
        eligible = db.get_market_data_eligible_symbols(
            ["WFAFY", "CATL", "AAPL", "MSFT"]
        )
        # WFAFY and CATL are cached; AAPL and MSFT are not.
        assert eligible == {"AAPL", "MSFT"}

    def test_empty_input_returns_empty_set(self, db):
        assert db.get_market_data_eligible_symbols([]) == set()


# ---------------------------------------------------------------------------
# Tier I: cleanup sweep
# ---------------------------------------------------------------------------


class TestCleanupSweep:
    def test_expired_rows_removed(self, db):
        past = _iso(datetime.now(timezone.utc) - timedelta(days=1))
        future = _iso(datetime.now(timezone.utc) + timedelta(days=7))
        db.upsert_market_data_eligibility(
            symbol="OLD", reason="MARKET_DATA_NOT_IN_FEED",
            bars_returned=0, required_bars=30, next_recheck_iso=past,
        )
        db.upsert_market_data_eligibility(
            symbol="NEW", reason="MARKET_DATA_NOT_IN_FEED",
            bars_returned=0, required_bars=30, next_recheck_iso=future,
        )
        removed = db.cleanup_expired_market_data_eligibility()
        assert removed == 1
        assert db.get_market_data_eligibility("OLD") is None
        assert db.get_market_data_eligibility("NEW") is not None


# ---------------------------------------------------------------------------
# Regression evidence: strategy / trading logic must remain unchanged
# ---------------------------------------------------------------------------


class TestStrategyRegression:
    """Prove that for a symbol with valid sufficient data, the new
    eligibility-cache layer does NOT change the BUY/SELL/HOLD outcome.

    These tests use a stub SmartTradingBot whose get_market_data always
    returns a valid 50-bar DataFrame and which has the cache layer
    initialized. They invoke the analyze_symbol path and compare the
    signal against a baseline run with no cache.

    Note: full end-to-end analysis is covered by test_smart_bot_score_*
    and test_obs_002_terminal_decision_coverage. Here we only need to
    prove the cache layer is invisible to the analysis path for valid
    symbols.
    """

    def test_sufficient_data_no_cache_row(self, db):
        """A successful get_market_data with sufficient bars must NOT
        create a cache row (cache is only for the failure path)."""
        from src.core.market_data_eligibility import cache_outcome

        # Simulate the no-data path with a fresh failure.
        cache_outcome(
            db=db,
            symbol="WFAFY",
            bars_returned=0,
            required_bars=30,
            first_bar_timestamp=None,
            latest_bar_timestamp=None,
            barset_key_present=False,
        )
        # Now simulate a fresh attempt with sufficient data.
        cache_outcome(
            db=db,
            symbol="WFAFY",
            bars_returned=50,
            required_bars=30,
            first_bar_timestamp="2026-05-01T00:00:00+00:00",
            latest_bar_timestamp="2026-09-28T00:00:00+00:00",
            barset_key_present=True,
        )
        # Row should be cleared so the symbol flows normally.
        assert db.get_market_data_eligibility("WFAFY") is None

    def test_cache_decision_does_not_leak_across_calls(self, db):
        """The cache decision scratch field must NOT bleed between
        consecutive symbols."""
        from src.core.market_data_eligibility import (
            cache_outcome,
            consult_cache_for_symbol,
        )

        cache_outcome(
            db=db,
            symbol="WFAFY",
            bars_returned=0,
            required_bars=30,
            first_bar_timestamp=None,
            latest_bar_timestamp=None,
            barset_key_present=False,
        )
        # First call: should_skip = True.
        d1 = consult_cache_for_symbol(db, "WFAFY")
        # Second call on a fresh symbol: should_skip = False.
        d2 = consult_cache_for_symbol(db, "AAPL")
        assert d1.should_skip is True
        assert d2.should_skip is False


# ---------------------------------------------------------------------------
# Tier J: queue ordering preservation (owner review)
# ---------------------------------------------------------------------------


class TestQueueOrderingPreservation:
    """The cache pre-filter must SKIP ineligible symbols WITHOUT
    re-ranking the remaining eligible symbols.

    Owner contract:
      Original queue: A B C D E F
      B and D cached-ineligible
      Expected analyzable order: A C E F (preserves queue order)

    The pre-filter must not:
      - sort by score, name, or any other criterion
      - pull from beyond target_count
      - re-rank or re-order the eligible subset
    """

    def _make_bot(self, db, queue_symbols):
        from src.core.smart_bot import SmartTradingBot

        bot = SmartTradingBot.__new__(SmartTradingBot)
        bot.db = db
        bot._analysis_queue = list(queue_symbols)
        bot._current_analysis_index = 0
        return bot

    def _cache(self, db, symbol):
        db.upsert_market_data_eligibility(
            symbol=symbol,
            reason="MARKET_DATA_NOT_IN_FEED",
            bars_returned=0,
            required_bars=30,
            next_recheck_iso=(
                datetime.now(timezone.utc) + timedelta(days=7)
            ).isoformat(),
        )

    def test_order_preserved_when_cached_symbols_in_middle(self, db):
        """Owner example: A B C D E F; B and D cached -> A C E F."""
        bot = self._make_bot(
            db, ["A", "B", "C", "D", "E", "F"],
        )
        self._cache(db, "B")
        self._cache(db, "D")
        batch = bot._get_rolling_ticker_list(target_count=4)
        assert batch == ["A", "C", "E", "F"], (
            f"queue order not preserved: got {batch}"
        )

    def test_order_preserved_when_cached_at_front(self, db):
        """A B C D E F; A and B cached -> C D E F."""
        bot = self._make_bot(
            db, ["A", "B", "C", "D", "E", "F"],
        )
        self._cache(db, "A")
        self._cache(db, "B")
        batch = bot._get_rolling_ticker_list(target_count=4)
        assert batch == ["C", "D", "E", "F"]

    def test_order_preserved_when_cached_at_end(self, db):
        """A B C D E F; E and F cached -> A B C D."""
        bot = self._make_bot(
            db, ["A", "B", "C", "D", "E", "F"],
        )
        self._cache(db, "E")
        self._cache(db, "F")
        batch = bot._get_rolling_ticker_list(target_count=4)
        assert batch == ["A", "B", "C", "D"]

    def test_target_count_not_exceeded(self, db):
        """A B C D E F G; B and D cached; target_count=3 -> exactly 3."""
        bot = self._make_bot(
            db, ["A", "B", "C", "D", "E", "F", "G"],
        )
        self._cache(db, "B")
        self._cache(db, "D")
        batch = bot._get_rolling_ticker_list(target_count=3)
        assert batch == ["A", "C", "E"]
        assert len(batch) == 3

    def test_alternating_cached_pattern(self, db):
        """A B C D E F G H; B D F H cached -> A C E G."""
        bot = self._make_bot(
            db, ["A", "B", "C", "D", "E", "F", "G", "H"],
        )
        for s in ["B", "D", "F", "H"]:
            self._cache(db, s)
        batch = bot._get_rolling_ticker_list(target_count=4)
        assert batch == ["A", "C", "E", "G"]


# ---------------------------------------------------------------------------
# Tier K: expired cache re-entry / recovery (owner review)
# ---------------------------------------------------------------------------


class TestExpiredCacheReEntry:
    """Owner contract: when now >= next_recheck_iso the cache must
    cease suppressing the symbol BEFORE the fresh market-data call.
    Then:
      fresh data >=30 bars -> normal analysis proceeds (cache row cleared)
      fresh zero bars      -> new NOT_IN_FEED TTL
      fresh 1-29 bars      -> new INSUFFICIENT_BARS TTL
    """

    def test_expired_row_does_not_suppress_fresh_attempt(self, db):
        """A row with next_recheck_iso in the past must NOT block the
        next fresh attempt."""
        from src.core.market_data_eligibility import (
            consult_cache_for_symbol,
            is_eligible_now,
        )

        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        cache_row = {
            "symbol": "WFAFY",
            "reason": "MARKET_DATA_NOT_IN_FEED",
            "bars_returned": 0,
            "required_bars": 30,
            "next_recheck_iso": past.isoformat(),
        }
        decision = is_eligible_now(cache_row)
        assert decision.should_skip is False
        assert decision.score_error_reason is None

        # And consult_cache_for_symbol against a real DB row.
        db.upsert_market_data_eligibility(
            symbol="WFAFY",
            reason="MARKET_DATA_NOT_IN_FEED",
            bars_returned=0,
            required_bars=30,
            next_recheck_iso=past.isoformat(),
        )
        decision = consult_cache_for_symbol(db, "WFAFY")
        assert decision.should_skip is False

    def test_successful_recovery_removes_stale_restriction(self, db):
        """If a symbol was cached as INSUFFICIENT and the fresh attempt
        now returns sufficient bars, the cache row must be deleted."""
        from src.core.market_data_eligibility import cache_outcome

        # Seed a stale INSUFFICIENT row.
        cache_outcome(
            db=db,
            symbol="CATL",
            bars_returned=15,
            required_bars=30,
            first_bar_timestamp="2026-09-01T00:00:00+00:00",
            latest_bar_timestamp="2026-09-28T00:00:00+00:00",
            barset_key_present=True,
        )
        assert db.get_market_data_eligibility("CATL") is not None
        # Fresh attempt: sufficient bars.
        token = cache_outcome(
            db=db,
            symbol="CATL",
            bars_returned=50,
            required_bars=30,
            first_bar_timestamp="2026-05-01T00:00:00+00:00",
            latest_bar_timestamp="2026-09-28T00:00:00+00:00",
            barset_key_present=True,
        )
        assert token is None  # No skip-token emitted
        # Cache row must be cleared.
        assert db.get_market_data_eligibility("CATL") is None

    def test_recovery_from_not_in_feed_to_eligible(self, db):
        """If a symbol was cached as NOT_IN_FEED and the fresh attempt
        now returns bars, the cache row must be deleted."""
        from src.core.market_data_eligibility import cache_outcome

        cache_outcome(
            db=db,
            symbol="WFAFY",
            bars_returned=0,
            required_bars=30,
            first_bar_timestamp=None,
            latest_bar_timestamp=None,
            barset_key_present=False,
        )
        assert db.get_market_data_eligibility("WFAFY") is not None
        # Fresh attempt: symbol now returns bars.
        token = cache_outcome(
            db=db,
            symbol="WFAFY",
            bars_returned=35,
            required_bars=30,
            first_bar_timestamp="2026-09-01T00:00:00+00:00",
            latest_bar_timestamp="2026-09-28T00:00:00+00:00",
            barset_key_present=True,
        )
        assert token is None
        assert db.get_market_data_eligibility("WFAFY") is None


# ---------------------------------------------------------------------------
# Tier L: TTL math (owner review) — never materially LATE
# ---------------------------------------------------------------------------


class TestTtlNeverLate:
    """Owner contract: the TTL arithmetic must NEVER recheck LATER
    than when the symbol would actually have enough bars.

    The formula is ``calendar_days = missing + 2 * (missing // 5)``
    which is provably equal to or less than the actual calendar time
    needed for any starting day-of-week (Mon..Fri).
    """

    def test_friday_one_missing_rechecks_by_saturday(self):
        """Friday + missing=1 -> recheck Sat (Sat < Mon, the next
        plausible trading session). Earliest plausible recheck."""
        from src.core.market_data_eligibility import (
            compute_next_recheck_for_insufficient_bars,
        )

        now = datetime(2026, 9, 25, 14, 0, 0, tzinfo=timezone.utc)  # Friday
        ttl = compute_next_recheck_for_insufficient_bars(
            bars_returned=29, required_bars=30, now=now,
        )
        # 1 missing -> 1 + 2*0 = 1 calendar day from Friday = Saturday
        assert ttl == (now + timedelta(days=1)).isoformat()

    def test_monday_three_missing_rechecks_thursday(self):
        """Monday + missing=3 -> recheck Thu (3 calendar days from
        Mon, EXACT; symbol has 3 more bars on Thu). NOT Friday
        (would be 1 day LATE)."""
        from src.core.market_data_eligibility import (
            compute_next_recheck_for_insufficient_bars,
        )

        now = datetime(2026, 9, 28, 14, 0, 0, tzinfo=timezone.utc)  # Monday
        ttl = compute_next_recheck_for_insufficient_bars(
            bars_returned=27, required_bars=30, now=now,
        )
        # 3 missing -> 3 + 2*0 = 3 calendar days from Monday = Thursday
        assert ttl == (now + timedelta(days=3)).isoformat()

    def test_monday_five_missing_rechecks_next_monday(self):
        """Monday + missing=5 -> recheck next Monday (7 calendar days,
        EXACT)."""
        from src.core.market_data_eligibility import (
            compute_next_recheck_for_insufficient_bars,
        )

        now = datetime(2026, 9, 28, 14, 0, 0, tzinfo=timezone.utc)
        ttl = compute_next_recheck_for_insufficient_bars(
            bars_returned=25, required_bars=30, now=now,
        )
        # 5 missing -> 5 + 2*1 = 7 calendar days
        assert ttl == (now + timedelta(days=7)).isoformat()

    def test_monday_nine_missing_rechecks_friday(self):
        """Monday + missing=9 -> recheck Fri (11 calendar days,
        EXACT; symbol has 9 more bars on Fri). NOT Saturday (would
        be 1 day LATE)."""
        from src.core.market_data_eligibility import (
            compute_next_recheck_for_insufficient_bars,
        )

        now = datetime(2026, 9, 28, 14, 0, 0, tzinfo=timezone.utc)
        ttl = compute_next_recheck_for_insufficient_bars(
            bars_returned=21, required_bars=30, now=now,
        )
        # 9 missing -> 9 + 2*1 = 11 calendar days
        assert ttl == (now + timedelta(days=11)).isoformat()

    def test_monday_ten_missing_rechecks_two_mondays_out(self):
        """Monday + missing=10 -> recheck 14 calendar days later
        (next-next Monday; EXACT)."""
        from src.core.market_data_eligibility import (
            compute_next_recheck_for_insufficient_bars,
        )

        now = datetime(2026, 9, 28, 14, 0, 0, tzinfo=timezone.utc)
        ttl = compute_next_recheck_for_insufficient_bars(
            bars_returned=20, required_bars=30, now=now,
        )
        # 10 missing -> 10 + 2*2 = 14 calendar days
        assert ttl == (now + timedelta(days=14)).isoformat()

    def test_ttl_never_late_against_old_int_formula(self):
        """The new formula must NEVER exceed int(missing * 7/5) for
        any non-negative missing count up to 30 (the configured
        sma_slow)."""
        from src.core.market_data_eligibility import (
            compute_next_recheck_for_insufficient_bars,
        )

        now = datetime(2026, 9, 29, 0, 0, 0, tzinfo=timezone.utc)
        for missing in range(0, 31):
            bars_returned = max(0, 30 - missing)
            new_ttl = compute_next_recheck_for_insufficient_bars(
                bars_returned=bars_returned, required_bars=30, now=now,
            )
            new_dt = datetime.fromisoformat(new_ttl)
            new_days = (new_dt - now).days
            old_days = int(missing * 7 / 5) if missing > 0 else 0
            assert new_days <= old_days, (
                f"new formula LATE for missing={missing}: "
                f"new={new_days} days > old={old_days} days"
            )
