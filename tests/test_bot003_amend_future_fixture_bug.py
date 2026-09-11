"""BOT-003 AMENDMENT — Bug demonstration and regression coverage.

The original BOT-003 v2 fix added a single-sided lower bound:
    session_start_epoch >= runner_process_start_epoch

This file proves that a future-dated ACTIVE row inserted with a
HIGHER id than the real runner's session still wins under v2.
The lower-bound filter only catches pre-runner rows; it does not
catch post-now future-dated fixtures.

The amendment adds an upper bound:
    session_start_epoch <= now + explicit_skew_seconds

After the amendment, future-dated fixtures cannot win regardless of
their id ordering.
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

import pytest

from src.database.sqlite_db import SQLiteDB, DB_PATH, _get_conn


def _seed_session(db, conn, session_start: str, session_end=None,
                  status: str = "ACTIVE", is_paper: int = 1,
                  notes: str = "test") -> int:
    """Insert a session row directly; return its id."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO trading_sessions
            (session_start, session_end, bot_version, configuration,
             is_paper_trading, total_symbols_processed, total_trades_executed,
             session_pnl, error_count, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (session_start, session_end, "2.1.0", "{}", is_paper, 0, 0, 0.0, 0, status, notes),
    )
    conn.commit()
    return cursor.lastrowid


@pytest.fixture
def db(monkeypatch):
    """Fresh isolated SQLite DB per test."""
    tmp = tempfile.NamedTemporaryFile(prefix="bot003_amend_test_", suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setattr("src.database.sqlite_db.DB_PATH", type(DB_PATH)(tmp.name))
    db = SQLiteDB()
    if hasattr(db, "init_schema"):
        db.init_schema()
    yield db, tmp.name
    try:
        os.unlink(tmp.name)
    except FileNotFoundError:
        pass


def _read_pid_start_epoch(pid: int) -> float:
    import time
    with open(f"/proc/{pid}/stat", "r") as f:
        parts = f.read().split()
    starttime_ticks = int(parts[21])
    clk_tck = os.sysconf("SC_CLK_TCK") or 100
    with open("/proc/uptime", "r") as f:
        uptime = float(f.read().split()[0])
    return time.time() - uptime + (starttime_ticks / float(clk_tck))


# ============================================================================
# TIER 1: Demonstrate the bug exists under the v2 (single-bound) implementation
# ============================================================================


def test_DEMONSTRATE_future_higher_id_wins_under_v2(db):
    """Demonstrates the bug class: under v2's single-lower-bound
    implementation, a future-dated ACTIVE row inserted with a HIGHER
    id than the real runner session wins. This proves the v2 fix
    alone does not solve the problem.

    This test asserts the CORRECT post-amendment behavior (real
    session wins). It will FAIL under the v2 implementation and PASS
    once the upper bound is added.
    """
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        runner_pid = os.getpid()
        runner_start_iso = datetime.fromtimestamp(
            _read_pid_start_epoch(runner_pid), tz=timezone.utc
        ).isoformat()
        # Real runner session: id=N, session_start=now
        real_id = _seed_session(
            db_obj, conn,
            session_start=datetime.now(timezone.utc).isoformat(),
            status="ACTIVE", notes="REAL RUNNER SESSION",
        )
        # Future fixture: id=N+1, session_start=2099
        future_id = _seed_session(
            db_obj, conn,
            session_start="2099-01-01T00:00:00+00:00",
            status="ACTIVE", notes="FUTURE FIXTURE (higher id)",
        )
        assert future_id > real_id
    finally:
        conn.close()

    active = db_obj.get_active_session_for_runner(runner_pid)
    assert active is not None, "expected an active session"
    assert active["notes"] == "REAL RUNNER SESSION", (
        f"BUG: future-dated fixture with higher id won. "
        f"Got id={active['id']}, notes={active['notes']!r}. "
        f"The v2 lower-bound fix is insufficient; the amendment "
        f"must add an upper bound to exclude future-dated fixtures."
    )


# ============================================================================
# TIER 2: After the amendment is applied — all cases must hold
# ============================================================================


def test_old_future_fixture_with_lower_id_cannot_win(db):
    """An older future-dated fixture (lower id) must not win. The
    future-dated timestamp disqualifies it via the upper bound."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        runner_pid = os.getpid()
        future_old = _seed_session(
            db_obj, conn,
            session_start="2099-01-01T00:00:00+00:00",
            status="ACTIVE", notes="future fixture (older)",
        )
        real_new = _seed_session(
            db_obj, conn,
            session_start=datetime.now(timezone.utc).isoformat(),
            status="ACTIVE", notes="real runner (newer)",
        )
        assert real_new > future_old
    finally:
        conn.close()

    active = db_obj.get_active_session_for_runner(runner_pid)
    assert active is not None
    assert active["id"] == real_new
    assert active["notes"] == "real runner (newer)"


def test_future_fixture_with_HIGHER_id_cannot_win(db):
    """A future-dated ACTIVE row inserted with a HIGHER id than the
    real runner session must NOT win — the upper bound on
    session_start disqualifies it regardless of id ordering."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        runner_pid = os.getpid()
        real_id = _seed_session(
            db_obj, conn,
            session_start=datetime.now(timezone.utc).isoformat(),
            status="ACTIVE", notes="real runner",
        )
        future_id = _seed_session(
            db_obj, conn,
            session_start="2099-01-01T00:00:00+00:00",
            status="ACTIVE", notes="future fixture (HIGHER id)",
        )
        assert future_id > real_id
    finally:
        conn.close()

    active = db_obj.get_active_session_for_runner(runner_pid)
    assert active is not None
    assert active["id"] == real_id
    assert active["notes"] == "real runner"


def test_normal_real_runner_session_is_selected(db):
    """A real runner session (now timestamp) is selected when no
    fixtures pollute the picture."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        runner_pid = os.getpid()
        real_id = _seed_session(
            db_obj, conn,
            session_start=datetime.now(timezone.utc).isoformat(),
            status="ACTIVE", notes="only real session",
        )
    finally:
        conn.close()

    active = db_obj.get_active_session_for_runner(runner_pid)
    assert active is not None
    assert active["id"] == real_id


def test_stale_pre_runner_ACTIVE_row_cannot_win(db):
    """A pre-runner ACTIVE row (session_start before runner start)
    must NOT win — the lower bound excludes it."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        runner_pid = os.getpid()
        # Pre-runner ACTIVE row: id=N, session_start way before now
        stale_id = _seed_session(
            db_obj, conn,
            session_start="1990-01-01T00:00:00+00:00",
            status="ACTIVE", notes="pre-runner stale",
        )
        # Real runner session: id=N+1, session_start=now
        real_id = _seed_session(
            db_obj, conn,
            session_start=datetime.now(timezone.utc).isoformat(),
            status="ACTIVE", notes="real runner",
        )
        assert real_id > stale_id
    finally:
        conn.close()

    active = db_obj.get_active_session_for_runner(runner_pid)
    assert active is not None
    assert active["id"] == real_id
    assert active["notes"] == "real runner"


def test_multiple_legitimate_post_start_sessions_select_newest_valid(db):
    """When multiple legitimate sessions exist within the runner's
    runtime window, the most recently created one (id DESC) is
    selected."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        runner_pid = os.getpid()
        runner_start_iso = datetime.fromtimestamp(
            _read_pid_start_epoch(runner_pid), tz=timezone.utc
        ).isoformat()
        # Three legitimate sessions spanning the runner's lifetime
        # — must be within both lower and upper bounds.
        older = _seed_session(
            db_obj, conn,
            session_start=runner_start_iso,
            status="ACTIVE", notes="older legit",
        )
        middle = _seed_session(
            db_obj, conn,
            session_start=datetime.now(timezone.utc).isoformat(),
            status="ACTIVE", notes="middle legit",
        )
        # newest, slightly after middle
        import time as _t
        _t.sleep(0.05)
        newest = _seed_session(
            db_obj, conn,
            session_start=datetime.now(timezone.utc).isoformat(),
            status="ACTIVE", notes="newest legit",
        )
    finally:
        conn.close()

    active = db_obj.get_active_session_for_runner(runner_pid)
    assert active is not None
    assert active["id"] == newest
    assert active["notes"] == "newest legit"


def test_runner_inactive_returns_none_does_not_falsely_report(db):
    """When the runner PID is not running (or invalid), the runner-
    specific selector returns None and does not falsely report
    sessions belonging to a different process."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        _seed_session(
            db_obj, conn,
            session_start=datetime.now(timezone.utc).isoformat(),
            status="ACTIVE", notes="some session",
        )
    finally:
        conn.close()

    # Non-existent PID: must return None
    assert db_obj.get_active_session_for_runner(99999999) is None
    # Invalid PIDs: must return None
    assert db_obj.get_active_session_for_runner(-1) is None
    assert db_obj.get_active_session_for_runner(0) is None


def test_no_mutation_history_rows_preserved(db):
    """The amendment must not mutate or delete any existing rows.
    Before/after snapshots must match exactly."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        # Seed various rows including future and pre-runner
        runner_pid = os.getpid()
        runner_start_iso = datetime.fromtimestamp(
            _read_pid_start_epoch(runner_pid), tz=timezone.utc
        ).isoformat()
        rows_before = []
        rows_before.append(_seed_session(db_obj, conn,
            session_start="1990-01-01T00:00:00+00:00",
            status="ACTIVE", notes="pre-runner"))
        rows_before.append(_seed_session(db_obj, conn,
            session_start=runner_start_iso,
            status="ACTIVE", notes="runner start"))
        rows_before.append(_seed_session(db_obj, conn,
            session_start=datetime.now(timezone.utc).isoformat(),
            status="ACTIVE", notes="now"))
        rows_before.append(_seed_session(db_obj, conn,
            session_start="2099-01-01T00:00:00+00:00",
            status="ACTIVE", notes="future"))
        # Snapshot
        before_snapshot = list(conn.execute(
            "SELECT id, session_start, status, notes FROM trading_sessions "
            "ORDER BY id ASC"
        ).fetchall())
    finally:
        conn.close()

    # Run the runner-pid lookup many times — should never mutate
    for _ in range(10):
        _ = db_obj.get_active_session_for_runner(runner_pid)

    conn = sqlite3.connect(path)
    try:
        after_snapshot = list(conn.execute(
            "SELECT id, session_start, status, notes FROM trading_sessions "
            "ORDER BY id ASC"
        ).fetchall())
    finally:
        conn.close()

    assert before_snapshot == after_snapshot, (
        "BOT-003 amendment must not mutate or delete any rows. "
        f"before={before_snapshot}, after={after_snapshot}"
    )


def test_get_active_session_fallback_still_uses_id_desc(db):
    """When the runner-pid selector returns None (runner not active),
    get_active_session() (the fallback) must still order by id DESC
    so the most-recently-created ACTIVE row wins — even if it is
    future-dated. This documents that the fallback is intentionally
    non-runner-aware: it returns the most recently created ACTIVE row
    regardless of timestamp validity, and callers must be aware that
    it can return a fixture if no real runner session exists."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        older_real = _seed_session(db_obj, conn,
            session_start=datetime.now(timezone.utc).isoformat(),
            status="ACTIVE", notes="older real")
        newer_future = _seed_session(db_obj, conn,
            session_start="2099-01-01T00:00:00+00:00",
            status="ACTIVE", notes="newer future fixture")
        assert newer_future > older_real
    finally:
        conn.close()

    # The fallback returns the highest-id ACTIVE row regardless of
    # its timestamp. This is documented behavior; the dashboard
    # only uses the fallback when the runner is not active.
    fallback = db_obj.get_active_session()
    assert fallback is not None
    assert fallback["id"] == newer_future
