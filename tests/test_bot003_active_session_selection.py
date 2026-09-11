"""BOT-003: Dashboard active-session selection tests.

Proves that:
  - When a SmartBot runner is active, get_runtime_status() reports
    the session belonging to the current runner (NOT a stale/fixture
    ACTIVE row whose timestamp happens to be in the future).
  - SQLiteDB.get_active_session() orders by id DESC (auto-increment),
    not session_start DESC (string compare), so clock-skewed fixtures
    can no longer poison runtime status.
  - SQLiteDB.get_active_session_for_runner(pid) deterministically
    selects the ACTIVE row whose session_start >= runner's process
    start time, and only the runner's session passes that filter.
  - Historical rows are NOT mutated or deleted.
  - No hard-coded exclusion of specific session ids.
  - No hard-coded year/date workarounds.
"""
import os
import sqlite3
from datetime import datetime, timezone

import pytest

from src.database.sqlite_db import SQLiteDB, DB_PATH, _get_conn


@pytest.fixture
def db(monkeypatch):
    """Provide a fresh isolated SQLite instance for each test.

    monkeypatch DB_PATH on the module so SQLiteDB and _get_conn both
    see the temp path. Yields the (db, path) pair for direct
    inspection.
    """
    import tempfile
    tmp = tempfile.NamedTemporaryFile(prefix="bot003_test_", suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setattr("src.database.sqlite_db.DB_PATH", type(DB_PATH)(tmp.name))
    db = SQLiteDB()
    db.init_schema() if hasattr(db, "init_schema") else None
    yield db, tmp.name
    try:
        os.unlink(tmp.name)
    except FileNotFoundError:
        pass


def _seed_session(db, conn, session_start: str, session_end: str = None,
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


# ============================================================================
# Tier A: SQLiteDB.get_active_session() — id-DESC ordering (safest deterministic)
# ============================================================================


def test_get_active_session_returns_most_recently_created(db) -> None:
    """When multiple ACTIVE rows exist, get_active_session returns the
    one with the highest auto-increment id (the most recently created),
    NOT the one with the highest string-compare session_start.

    This is the BOT-003 deterministic fix: auto-increment ids are
    monotonic and clock-independent, so they cannot be poisoned by
    fixture rows with future dates."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        old_id = _seed_session(
            db_obj, conn,
            session_start="2099-01-01T00:00:00+00:00",
            status="ACTIVE", notes="future-dated fixture",
        )
        new_id = _seed_session(
            db_obj, conn,
            session_start="2026-09-11T00:00:00+00:00",
            status="ACTIVE", notes="real runner session",
        )
    finally:
        conn.close()

    active = db_obj.get_active_session()
    assert active is not None
    assert active["id"] == new_id, (
        f"Expected most-recently-created row id={new_id} (the real runner), "
        f"but got id={active['id']} (the 2099 fixture id={old_id}). "
        f"This means get_active_session() is still ordering by session_start, "
        f"which string-compares '2099' > '2026' and incorrectly selects the fixture."
    )
    assert active["notes"] == "real runner session"


def test_get_active_session_excludes_ended_rows(db) -> None:
    """A more recently created ENDED row must NOT be returned."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        # ACTIVE row with id=A (older)
        active_id = _seed_session(
            db_obj, conn,
            session_start="2026-09-11T00:00:00+00:00",
            status="ACTIVE",
        )
        # ENDED row with id=B (newer); has session_end set
        ended_id = _seed_session(
            db_obj, conn,
            session_start="2026-09-11T01:00:00+00:00",
            session_end="2026-09-11T01:30:00+00:00",
            status="ENDED",
        )
        assert ended_id > active_id
    finally:
        conn.close()

    active = db_obj.get_active_session()
    assert active is not None
    assert active["id"] == active_id
    assert active["status"] == "ACTIVE"
    assert active["session_end"] is None


def test_get_active_session_returns_none_when_no_active(db) -> None:
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        _seed_session(
            db_obj, conn,
            session_start="2026-09-11T00:00:00+00:00",
            session_end="2026-09-11T01:00:00+00:00",
            status="ENDED",
        )
    finally:
        conn.close()

    assert db_obj.get_active_session() is None


# ============================================================================
# Tier B: SQLiteDB.get_active_session_for_runner(pid) — process-start-time filter
# ============================================================================


def _read_pid_start_epoch(pid: int) -> float:
    """Mirror the production logic: read /proc/<pid>/stat and convert
    field 22 (starttime) to an epoch float."""
    import time as time_mod
    with open(f"/proc/{pid}/stat", "r") as f:
        parts = f.read().split()
    starttime_ticks = int(parts[21])
    clk_tck = os.sysconf("SC_CLK_TCK") or 100
    with open("/proc/uptime", "r") as f:
        uptime = float(f.read().split()[0])
    return time_mod.time() - uptime + (starttime_ticks / float(clk_tck))


def test_get_active_session_for_runner_excludes_future_dated_fixture(db) -> None:
    """The real runner session is selected even when a future-dated
    fixture row is also ACTIVE. This is the exact bug that motivated
    BOT-003 (session 71804 with year 2099 was beating the real session
    71920 because session_start DESC string-compares '2099' > '2026')."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        fixture_id = _seed_session(
            db_obj, conn,
            session_start="2099-01-01T00:00:00+00:00",
            status="ACTIVE", notes="future-dated fixture",
        )
        # Real session: must be after the runner's process start time.
        # Use "now" as a safe value that is definitely >= runner start.
        real_id = _seed_session(
            db_obj, conn,
            session_start=datetime.now(timezone.utc).isoformat(),
            status="ACTIVE", notes="real runner session",
        )
    finally:
        conn.close()

    # The runner PID is this test process's own pid; its start time
    # is in the past, well before "now".
    runner_pid = os.getpid()
    runner_start = _read_pid_start_epoch(runner_pid)
    assert runner_start < datetime.now(timezone.utc).timestamp(), (
        "Test invariant: runner_pid start time must be in the past"
    )

    active = db_obj.get_active_session_for_runner(runner_pid)
    assert active is not None
    assert active["id"] == real_id, (
        f"Expected runner session id={real_id}, got id={active['id']} "
        f"(fixture id was {fixture_id}). The runner-pid filter must "
        f"exclude any row with session_start strictly before the "
        f"runner's process start time."
    )
    assert active["notes"] == "real runner session"


def test_get_active_session_for_runner_invalid_pid_returns_none(db) -> None:
    """Invalid PIDs must return None, not raise."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        _seed_session(
            db_obj, conn,
            session_start="2026-09-11T00:00:00+00:00",
            status="ACTIVE",
        )
    finally:
        conn.close()

    assert db_obj.get_active_session_for_runner(-1) is None
    assert db_obj.get_active_session_for_runner(0) is None
    # Non-int PIDs are rejected.
    assert db_obj.get_active_session_for_runner("not-an-int") is None  # type: ignore[arg-type]


def test_get_active_session_for_runner_nonexistent_pid_returns_none(db) -> None:
    """A PID that doesn't correspond to a running process must return
    None (not crash, not return a fixture)."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        _seed_session(
            db_obj, conn,
            session_start="2026-09-11T00:00:00+00:00",
            status="ACTIVE",
        )
    finally:
        conn.close()

    # Pick a PID that is almost certainly not running. os.getpid() returns
    # the test process's PID; use a PID far away from typical ranges.
    assert db_obj.get_active_session_for_runner(999999) is None


def test_get_active_session_for_runner_prefers_newest_among_runner_rows(db) -> None:
    """When multiple ACTIVE rows belong to the current runner (e.g.,
    a stale ACTIVE row from a prior loop iteration that didn't
    finalize), the newest one (id DESC) is selected."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        runner_pid = os.getpid()
        runner_start_iso = datetime.fromtimestamp(
            _read_pid_start_epoch(runner_pid), tz=timezone.utc
        ).isoformat()

        older_runner_id = _seed_session(
            db_obj, conn,
            session_start=runner_start_iso,
            status="ACTIVE", notes="older runner session",
        )
        # Newer row, still after runner start time
        newer_runner_id = _seed_session(
            db_obj, conn,
            session_start=datetime.now(timezone.utc).isoformat(),
            status="ACTIVE", notes="newer runner session",
        )
        assert newer_runner_id > older_runner_id
    finally:
        conn.close()

    active = db_obj.get_active_session_for_runner(runner_pid)
    assert active is not None
    assert active["id"] == newer_runner_id


# ============================================================================
# Tier C: get_runtime_status() integration — no live systemd dependency
# ============================================================================


def test_get_runtime_status_returns_real_runner_session_under_future_fixture(
    monkeypatch, db,
) -> None:
    """End-to-end: when a future-dated fixture ACTIVE row exists alongside
    a real runner session, get_runtime_status() returns the real runner
    session (not 71804-style fixture), provided the runner PID matches.

    We stub `is_smartbot_runner_active` and the lock-file path so this
    test does not require a live systemd unit."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        fixture_id = _seed_session(
            db_obj, conn,
            session_start="2099-01-01T00:00:00+00:00",
            status="ACTIVE", notes="future-dated fixture",
        )
        # Real runner session: must be after this test process's start.
        real_id = _seed_session(
            db_obj, conn,
            session_start=datetime.now(timezone.utc).isoformat(),
            status="ACTIVE", notes="real runner session",
        )
    finally:
        conn.close()

    runner_pid = os.getpid()
    # Write the lock file pointing at the test process
    import tempfile
    lock_dir = tempfile.mkdtemp(prefix="bot003_lock_")
    lock_path = os.path.join(lock_dir, "trading_bot.lock")
    with open(lock_path, "w") as f:
        f.write(str(runner_pid))

    # Patch dashboard helpers that would touch the host (systemd, alpaca).
    monkeypatch.setattr(
        "dashboard.is_smartbot_runner_active", lambda: True,
        raising=False,
    )
    # Patch the lock-file path used inside get_runtime_status().
    monkeypatch.setattr("os.path.exists", lambda p: True if p == "/tmp/trading_bot.lock" else os.path.exists(p))
    monkeypatch.setattr("builtins.open", _open_factory(lock_path))
    monkeypatch.setattr(
        "dashboard.simple_rest", db_obj,
        raising=False,
    )

    # get_account_info is at module-level; patch its Alpaca-side path.
    monkeypatch.setattr(
        "dashboard.get_account_info",
        lambda: {"portfolio_value": 100000.0, "cash": 50000.0},
        raising=False,
    )

    # Now import dashboard (may need sys.path tweak depending on runner).
    import sys
    if "" not in sys.path:
        sys.path.insert(0, ".")
    import dashboard as dashboard_mod

    # Re-bind the lock path under the dashboard module namespace.
    monkeypatch.setattr(dashboard_mod, "is_smartbot_runner_active", lambda: True)

    result = dashboard_mod.get_runtime_status()

    assert result["smartbot_runner_active"] is True
    assert result["alpaca_api_reachable"] is True
    assert result["active_session_id"] == real_id, (
        f"Expected real runner session id={real_id}, got "
        f"{result['active_session_id']} (fixture id was {fixture_id}). "
        f"runtime-status source: {result.get('active_session_source')!r}"
    )
    assert result["active_session_source"] == "runner-pid"
    assert result["fully_ready"] is True

    # Cleanup the temp lock dir
    try:
        os.remove(lock_path)
        os.rmdir(lock_dir)
    except FileNotFoundError:
        pass


def test_get_runtime_status_falls_back_when_runner_inactive(monkeypatch, db) -> None:
    """When the runner is NOT active, get_runtime_status falls back to
    the most-recently-created ACTIVE row (id DESC). This is the
    backward-compatible behavior for the dashboard when no runner is
    running."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        older = _seed_session(
            db_obj, conn,
            session_start="2026-09-11T00:00:00+00:00",
            status="ACTIVE",
        )
        newer = _seed_session(
            db_obj, conn,
            session_start="2026-09-11T01:00:00+00:00",
            status="ACTIVE",
        )
    finally:
        conn.close()

    import sys
    if "" not in sys.path:
        sys.path.insert(0, ".")
    import dashboard as dashboard_mod
    monkeypatch.setattr(dashboard_mod, "is_smartbot_runner_active", lambda: False)
    monkeypatch.setattr(dashboard_mod, "get_account_info", lambda: {"portfolio_value": 1.0}, raising=False)
    monkeypatch.setattr(dashboard_mod, "simple_rest", db_obj, raising=False)

    result = dashboard_mod.get_runtime_status()

    assert result["smartbot_runner_active"] is False
    assert result["active_session_id"] == newer
    assert result["active_session_source"] == "id-desc"
    assert result["fully_ready"] is False  # runner is inactive


def test_get_runtime_status_no_active_session_returns_none(monkeypatch, db) -> None:
    """When no ACTIVE session exists, runtime status reports None."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        _seed_session(
            db_obj, conn,
            session_start="2026-09-11T00:00:00+00:00",
            session_end="2026-09-11T01:00:00+00:00",
            status="ENDED",
        )
    finally:
        conn.close()

    import sys
    if "" not in sys.path:
        sys.path.insert(0, ".")
    import dashboard as dashboard_mod
    monkeypatch.setattr(dashboard_mod, "is_smartbot_runner_active", lambda: False)
    monkeypatch.setattr(dashboard_mod, "get_account_info", lambda: {"portfolio_value": 1.0}, raising=False)
    monkeypatch.setattr(dashboard_mod, "simple_rest", db_obj, raising=False)

    result = dashboard_mod.get_runtime_status()
    assert result["active_session_id"] is None
    assert result["active_session_start"] is None
    assert result["active_session_source"] is None


# ============================================================================
# Tier D: no mutation/deletion of historical rows
# ============================================================================


def test_bot003_does_not_mutate_or_delete_history(db) -> None:
    """All BOT-003 operations are read-only. Confirm by snapshotting
    row counts before/after and confirming nothing changed."""
    db_obj, path = db
    conn = sqlite3.connect(path)
    try:
        # Seed a realistic mix of rows including the historical fixture.
        _seed_session(
            db_obj, conn,
            session_start="2099-01-01T00:00:00+00:00",
            status="ACTIVE", notes="historical fixture (must survive)",
        )
        _seed_session(
            db_obj, conn,
            session_start="2026-09-11T00:00:00+00:00",
            status="ACTIVE", notes="real",
        )
        _seed_session(
            db_obj, conn,
            session_start="2026-09-10T00:00:00+00:00",
            session_end="2026-09-10T01:00:00+00:00",
            status="ENDED", notes="historical ended",
        )
        rows_before = conn.execute(
            "SELECT id, session_start, session_end, status, notes FROM trading_sessions ORDER BY id"
        ).fetchall()
        before_count = len(rows_before)
    finally:
        conn.close()

    # Run the BOT-003 selection paths.
    db_obj.get_active_session()
    db_obj.get_active_session_for_runner(os.getpid())
    # And the runtime-status path (with everything stubbed).
    import sys
    if "" not in sys.path:
        sys.path.insert(0, ".")
    import dashboard as dashboard_mod
    import builtins
    real_open = builtins.open

    class _LockFile:
        def __init__(self):
            self.path = None

        def open(self, *args, **kwargs):
            # Pretend the lock file does not exist (so the runner-pid
            # path falls back to id DESC).
            if args and isinstance(args[0], str) and args[0] == "/tmp/trading_bot.lock":
                raise FileNotFoundError(args[0])
            return real_open(*args, **kwargs)

    # Just use monkeypatch's builtins.open; the production code uses
    # `open(...)` directly which goes through builtins.open.
    import dashboard as dashboard_mod
    import sys as sys_mod
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(dashboard_mod, "is_smartbot_runner_active", lambda: False)
        monkeypatch.setattr(dashboard_mod, "get_account_info", lambda: {"portfolio_value": 1.0}, raising=False)
        monkeypatch.setattr(dashboard_mod, "simple_rest", db_obj, raising=False)
        dashboard_mod.get_runtime_status()
    finally:
        monkeypatch.undo()

    conn = sqlite3.connect(path)
    try:
        rows_after = conn.execute(
            "SELECT id, session_start, session_end, status, notes FROM trading_sessions ORDER BY id"
        ).fetchall()
        after_count = len(rows_after)
    finally:
        conn.close()

    assert after_count == before_count, (
        f"Row count changed: before={before_count} after={after_count}. "
        f"BOT-003 must not insert or delete rows."
    )
    assert rows_after == rows_before, (
        f"Row contents changed: before={rows_before} after={rows_after}. "
        f"BOT-003 must not mutate rows."
    )


# ============================================================================
# Helper: open() factory for monkeypatch (returns a wrapper that points
# /tmp/trading_bot.lock at a temp file while passing through other paths).
# ============================================================================


def _open_factory(lock_path: str):
    """Returns a patched open() that points /tmp/trading_bot.lock at
    `lock_path` while passing through other paths unchanged."""
    import builtins
    real_open = builtins.open

    def patched_open(file, mode="r", *args, **kwargs):
        if isinstance(file, str) and file == "/tmp/trading_bot.lock":
            return real_open(lock_path, mode, *args, **kwargs)
        return real_open(file, mode, *args, **kwargs)

    return patched_open
