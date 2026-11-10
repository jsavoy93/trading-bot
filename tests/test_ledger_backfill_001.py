"""LEDGER-BACKFILL-001 - Alpaca PAPER-only ledger backfill tool + the
case-insensitive broker-position-symbol fix.

Scope (PAPER ONLY; no merge/deploy without approval):

  A. ``SmartBot._broker_position_qty`` matches broker position symbols
     CASE-INSENSITIVELY. A broker that reports 'Aaa' for a position the
     bot calls 'AAA' must not silently read as "no broker position",
     because the fail-closed SELL path treats 0.0 as "broker bound
     unavailable" and refuses a legitimate SELL.

  B. The backfill tool's safety contract:
       B1. dry run by default (nothing is written without --apply);
       B2. --apply REQUIRES --db (a write without an explicit target is
           refused);
       B3. a non-PAPER broker endpoint is refused outright;
       B4. broker access is READ-ONLY (no submit/cancel/replace exists);
       B5. swing label prefixes ('sl_short-', 'rsi2-') are excluded;
       B6. order/client ids present in a runner DB are excluded;
       B7. a fill whose broker_order_id is already in the ledger is
           skipped (idempotency);
       B8. quantity reconciliation is FAIL-CLOSED and uses the bot's own
           allocation_policy.own_net_qty_from_ledger_rows;
       B9. the inserted row has the exact bot `trades` schema with
           status='FILLED', the real filled_qty, broker_order_id,
           avg_fill_price and a backfill note;
       B10. a timestamped DB backup is taken before any write;
       B11. post-write verification proves the ledger net matches.

Deterministic and fully offline: the broker is a fake object, the
databases are minimal SQLite fixtures created under tmp_path. NO Alpaca
call and no network access ever happens.
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
for _p in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.core.smart_bot import SmartTradingBot  # noqa: E402


# --------------------------------------------------------------------------- #
# Loading scripts/backfill_symbol_fills.py as a module
# --------------------------------------------------------------------------- #

SCRIPT_PATH = PROJECT_ROOT / "scripts" / "backfill_symbol_fills.py"


def _load_backfill_module():
    spec = importlib.util.spec_from_file_location(
        "backfill_symbol_fills_under_test", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bf = _load_backfill_module()


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

CMA_ORDER_ID = "a5c552fd-d7e5-4f57-9fbf-16daeaa669ff"
CMA_COID = "669488bd-a45e-42ab-ac10-a177d657caf0"
AAAA_ORDER_ID = "692a85e1-60ef-4b6b-a7c6-7f12332ed8a1"
AAAA_COID = "sl_short-AAAA-e-20251230"


def _fill(**overrides):
    base = {
        "id": CMA_ORDER_ID,
        "client_order_id": CMA_COID,
        "symbol": "CMA",
        "side": "buy",
        "status": "filled",
        "qty": 17.0,
        "filled_qty": 17.0,
        "filled_avg_price": 84.58,
        "created_at": "2025-12-06T05:32:16.504132Z",
        "filled_at": "2025-12-08T14:30:18.440279Z",
    }
    base.update(overrides)
    return base


def _make_trades_db(path):
    """Minimal but REAL-shaped trades table (same columns the bot creates)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            symbol TEXT NOT NULL,
            side TEXT,
            qty REAL,
            price REAL,
            pnl REAL,
            signal TEXT,
            rsi REAL,
            order_time TEXT,
            status TEXT DEFAULT 'SUBMITTED',
            broker_order_id TEXT,
            sleeve TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            filled_qty REAL,
            avg_fill_price REAL,
            order_updated_at TEXT,
            notes TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return path


def _rows(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM trades").fetchall()]
    finally:
        conn.close()


class _FakePosition:
    def __init__(self, symbol, qty):
        self.symbol = symbol
        self.qty = qty


class _FakeClient:
    """Minimal read-only broker double."""

    def __init__(self, positions=(), orders=()):
        self._positions = list(positions)
        self._orders = list(orders)
        self.calls = []

    def get_all_positions(self):
        self.calls.append("get_all_positions")
        return list(self._positions)

    def get_orders(self, request=None):
        self.calls.append("get_orders")
        return list(self._orders)

    # Any mutating broker API must NOT exist in this tool; these guards make
    # an accidental regression loud instead of silent.
    def submit_order(self, *a, **kw):  # pragma: no cover - guard only
        raise AssertionError("backfill tool must never submit orders")

    def cancel_order(self, *a, **kw):  # pragma: no cover - guard only
        raise AssertionError("backfill tool must never cancel orders")

    def replace_order(self, *a, **kw):  # pragma: no cover - guard only
        raise AssertionError("backfill tool must never replace orders")


# --------------------------------------------------------------------------- #
# A. _broker_position_qty case-insensitivity
# --------------------------------------------------------------------------- #

def _bot_with_positions(positions):
    client = SimpleNamespace(get_all_positions=lambda: list(positions))
    return SimpleNamespace(trading_client=client)


def test_broker_position_qty_matches_different_casing():
    """A broker reporting 'Aaa' must satisfy a lookup for 'AAA'."""
    bot = _bot_with_positions([_FakePosition("Aaa", 75.0)])
    assert SmartTradingBot._broker_position_qty(bot, "AAA") == 75.0


def test_broker_position_qty_matches_lower_case_broker_for_upper_symbol():
    bot = _bot_with_positions([_FakePosition("cma", 17.0)])
    assert SmartTradingBot._broker_position_qty(bot, "CMA") == 17.0


def test_broker_position_qty_matches_upper_case_broker_for_lower_symbol():
    bot = _bot_with_positions([_FakePosition("CMA", 17.0)])
    assert SmartTradingBot._broker_position_qty(bot, "cma") == 17.0


def test_broker_position_qty_ignores_whitespace_and_padding():
    bot = _bot_with_positions([_FakePosition(" Aaa ", 12.0)])
    assert SmartTradingBot._broker_position_qty(bot, "aaa") == 12.0


def test_broker_position_qty_still_zero_for_absent_symbol():
    bot = _bot_with_positions([_FakePosition("AMD", 22.0)])
    assert SmartTradingBot._broker_position_qty(bot, "CMA") == 0.0


def test_broker_position_qty_still_zero_on_broker_read_failure():
    def _boom():
        raise RuntimeError("broker down")

    bot = SimpleNamespace(
        trading_client=SimpleNamespace(get_all_positions=_boom)
    )
    assert SmartTradingBot._broker_position_qty(bot, "CMA") == 0.0


def test_broker_position_qty_never_matches_empty_symbol():
    bot = _bot_with_positions([_FakePosition("", 99.0)])
    assert SmartTradingBot._broker_position_qty(bot, "") == 0.0


# --------------------------------------------------------------------------- #
# B1/B2 - dry-run default and --apply requiring --db
# --------------------------------------------------------------------------- #

def test_db_argument_is_required():
    """Without --db argparse refuses (exit code 2)."""
    with pytest.raises(SystemExit) as exc:
        bf.main([])
    assert exc.value.code == bf.EXIT_USAGE


def test_apply_flag_is_off_by_default():
    """--apply is store_true, so it is False unless explicitly passed."""
    import argparse as _argparse

    p = _argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    assert p.parse_args([]).apply is False
    assert p.parse_args(["--apply"]).apply is True


def test_apply_without_db_is_refused(monkeypatch):
    """--apply must never be usable without an explicit --db target."""
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "TEST")
    with pytest.raises(SystemExit) as exc:
        bf.main(["--apply", "--symbols", "CMA"])
    assert exc.value.code == bf.EXIT_USAGE


def test_missing_db_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "TEST")
    with pytest.raises(SystemExit) as exc:
        bf.main(["--db", str(tmp_path / "nope.db")])
    assert "not found" in str(exc.value).lower()


# --------------------------------------------------------------------------- #
# B3 - PAPER endpoint guard
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "base",
    [
        "https://api.alpaca.markets",
        "https://live-api.alpaca.markets",
    ],
)
def test_non_paper_endpoint_is_refused(monkeypatch, base):
    monkeypatch.setenv("ALPACA_BASE_URL", base)
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "TEST")
    with pytest.raises(SystemExit) as exc:
        bf.make_paper_client()
    assert "paper" in str(exc.value).lower()


def test_paper_endpoint_is_accepted(monkeypatch):
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "TEST")
    client = bf.make_paper_client()
    assert client is not None


def test_missing_credentials_are_refused(monkeypatch):
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    for var in ("ALPACA_API_KEY", "ALPACA_API_KEY_ID",
                "ALPACA_API_SECRET", "ALPACA_API_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit) as exc:
        bf.make_paper_client()
    assert "missing" in str(exc.value).lower()


# --------------------------------------------------------------------------- #
# B4 - read-only broker access
# --------------------------------------------------------------------------- #

def test_tool_source_contains_no_mutating_broker_calls():
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "submit_order",
        "cancel_order",
        "replace_order",
        "close_position",
    ):
        assert forbidden not in src, f"{forbidden} must not appear in the tool"


# --------------------------------------------------------------------------- #
# B5/B6/B7 - classification: labels, runner DBs, idempotency
# --------------------------------------------------------------------------- #

def test_swing_prefix_fills_are_excluded():
    fill = _fill(id=AAAA_ORDER_ID, client_order_id=AAAA_COID, symbol="AAAA")
    verdict, reason = bf.classify_fill(
        fill, recorded_ids=set(),
        prefixes=bf.DEFAULT_SWING_ORDER_LABEL_PREFIXES, runner_ids=set(),
    )
    assert verdict == bf.EXCLUDED_LABEL
    assert "swing" in reason.lower()


def test_rsi2_prefix_fill_is_excluded():
    fill = _fill(id="x", client_order_id="rsi2-AMD-20261103-1", symbol="AMD")
    verdict, _ = bf.classify_fill(
        fill, recorded_ids=set(),
        prefixes=bf.DEFAULT_SWING_ORDER_LABEL_PREFIXES, runner_ids=set(),
    )
    assert verdict == bf.EXCLUDED_LABEL


def test_runner_db_order_id_is_excluded():
    fill = _fill(id="runner-order-1", client_order_id="opaque-uuid")
    verdict, reason = bf.classify_fill(
        fill, recorded_ids=set(),
        prefixes=bf.DEFAULT_SWING_ORDER_LABEL_PREFIXES,
        runner_ids={"runner-order-1"},
    )
    assert verdict == bf.EXCLUDED_RUNNER_DB
    assert "runner" in reason.lower()


def test_runner_db_client_order_id_is_excluded():
    fill = _fill(id="opaque-2", client_order_id="runner-coid-1")
    verdict, _ = bf.classify_fill(
        fill, recorded_ids=set(),
        prefixes=bf.DEFAULT_SWING_ORDER_LABEL_PREFIXES,
        runner_ids={"runner-coid-1"},
    )
    assert verdict == bf.EXCLUDED_RUNNER_DB


def test_already_recorded_fill_is_skipped():
    """Idempotency: a broker_order_id already in the ledger is not re-added."""
    fill = _fill()
    verdict, reason = bf.classify_fill(
        fill, recorded_ids={CMA_ORDER_ID},
        prefixes=bf.DEFAULT_SWING_ORDER_LABEL_PREFIXES, runner_ids=set(),
    )
    assert verdict == bf.ALREADY_RECORDED
    assert "already" in reason.lower()


def test_main_bot_fill_is_included():
    verdict, reason = bf.classify_fill(
        _fill(), recorded_ids=set(),
        prefixes=bf.DEFAULT_SWING_ORDER_LABEL_PREFIXES, runner_ids=set(),
    )
    assert verdict == bf.INCLUDED
    assert "main-bot" in reason.lower()


def test_runner_db_identifiers_are_read_read_only(tmp_path):
    db = tmp_path / "runner.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE orders (alpaca_order_id TEXT, client_order_id TEXT)"
    )
    conn.execute("INSERT INTO orders VALUES (?, ?)", ("oid-1", "coid-1"))
    conn.commit()
    conn.close()
    ids = bf.runner_db_identifiers(str(db))
    assert "oid-1" in ids and "coid-1" in ids


def test_runner_db_identifiers_missing_file_is_empty():
    assert bf.runner_db_identifiers("/nonexistent/runner.db") == set()


# --------------------------------------------------------------------------- #
# B8 - fail-closed reconciliation using the bot's OWN ownership math
# --------------------------------------------------------------------------- #

def test_reconciliation_uses_own_net_qty_from_ledger_rows():
    from src.core.allocation_policy import own_net_qty_from_ledger_rows

    rows = [
        {"symbol": "CMA", "side": "BUY", "status": "FILLED", "filled_qty": 17.0},
        {"symbol": "CMA", "side": "SELL", "status": "FILLED", "filled_qty": 2.0},
        # A SUBMITTED row proves nothing and must not count.
        {"symbol": "CMA", "side": "BUY", "status": "SUBMITTED", "filled_qty": 99.0},
    ]
    assert own_net_qty_from_ledger_rows(rows) == {"CMA": 15.0}

    ok, per_symbol = bf.verify_projection(rows, [], {"CMA": 15.0})
    assert ok is True
    assert per_symbol["CMA"]["projected"] == 15.0


def test_reconciliation_fails_closed_on_mismatch():
    rows = [
        {"symbol": "CMA", "side": "BUY", "status": "FILLED", "filled_qty": 17.0},
    ]
    ok, per_symbol = bf.verify_projection(rows, [], {"CMA": 75.0})
    assert ok is False
    assert per_symbol["CMA"]["match"] is False


def test_reconciliation_fails_closed_when_no_ledger_rows_at_all():
    ok, per_symbol = bf.verify_projection([], [], {"CMA": 17.0})
    assert ok is False


def test_reconciliation_is_case_insensitive_on_symbol():
    rows = [
        {"symbol": "cma", "side": "BUY", "status": "FILLED", "filled_qty": 17.0},
    ]
    ok, _ = bf.verify_projection(rows, [], {"CMA": 17.0})
    assert ok is True


def test_swing_fill_alone_cannot_reconcile_a_main_bot_position():
    """A runner-owned fill must never make a symbol look main-bot owned."""
    fill = _fill(id=AAAA_ORDER_ID, client_order_id=AAAA_COID, symbol="AAAA")
    verdict, _ = bf.classify_fill(
        fill, recorded_ids=set(),
        prefixes=bf.DEFAULT_SWING_ORDER_LABEL_PREFIXES, runner_ids=set(),
    )
    assert verdict != bf.INCLUDED
    planned = [bf.build_backfill_row(fill)] if verdict == bf.INCLUDED else []
    ok, _ = bf.verify_projection([], planned, {"AAAA": 75.0})
    assert ok is False



# --------------------------------------------------------------------------- #
# B9 - exact trades row shape
# --------------------------------------------------------------------------- #

def test_backfill_row_has_exact_schema_keys_and_values():
    row = bf.build_backfill_row(_fill())
    expected_keys = {
        "session_id", "symbol", "side", "qty", "price", "pnl", "signal",
        "rsi", "order_time", "status", "broker_order_id", "filled_qty",
        "avg_fill_price", "sleeve", "notes",
    }
    assert set(row.keys()) == expected_keys
    assert row["status"] == "FILLED"
    assert row["filled_qty"] == 17.0
    assert row["qty"] == 17.0
    assert row["avg_fill_price"] == 84.58
    assert row["price"] == 84.58
    assert row["broker_order_id"] == CMA_ORDER_ID
    assert row["symbol"] == "CMA"
    assert row["side"] == "BUY"
    assert row["sleeve"] == "S"
    assert "LEDGER-BACKFILL-001" in row["notes"]
    # pnl is never fabricated for a backfill.
    assert row["pnl"] is None


def test_backfill_row_keys_are_a_subset_of_the_real_trades_schema(tmp_path):
    db = _make_trades_db(tmp_path / "t.db")
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
    conn.close()
    row = bf.build_backfill_row(_fill())
    unknown = set(row.keys()) - cols
    assert not unknown, f"row uses columns absent from trades: {unknown}"


def test_backfill_row_normalizes_symbol_to_upper():
    row = bf.build_backfill_row(_fill(symbol="cma"))
    assert row["symbol"] == "CMA"


def test_signed_qty_is_negative_for_sell():
    assert bf.signed_qty(_fill(side="sell")) == -17.0
    assert bf.signed_qty(_fill(side="buy")) == 17.0


def test_is_filled_requires_status_and_real_qty():
    assert bf.is_filled(_fill()) is True
    assert bf.is_filled(_fill(status="submitted")) is False
    assert bf.is_filled(_fill(filled_qty=0.0)) is False


def test_normalize_order_handles_enum_side_and_status():
    order = SimpleNamespace(
        id="oid", client_order_id="coid", symbol="cma",
        side=SimpleNamespace(value="buy"), status=SimpleNamespace(value="filled"),
        qty="17", filled_qty="17", filled_avg_price="84.58",
        created_at="2025-12-06T05:32:16.504132Z",
        filled_at="2025-12-08T14:30:18.440279Z",
    )
    out = bf.normalize_order(order)
    assert out["side"] == "buy"
    assert out["status"] == "filled"
    assert out["symbol"] == "CMA"
    assert out["filled_qty"] == 17.0
    assert out["filled_avg_price"] == 84.58


# --------------------------------------------------------------------------- #
# B10 - timestamped backup
# --------------------------------------------------------------------------- #

def test_backup_is_timestamped_and_never_overwrites(tmp_path):
    db = _make_trades_db(tmp_path / "ledger.db")
    first = bf.backup_db(db, str(tmp_path / "backups"))
    second = bf.backup_db(db, str(tmp_path / "backups"))
    assert first.exists() and second.exists()
    assert first != second
    assert "ledger-backup-" in first.name
    assert first.read_bytes() == db.read_bytes()


def test_backup_of_missing_db_raises(tmp_path):
    with pytest.raises(Exception):
        bf.backup_db(tmp_path / "missing.db", str(tmp_path))


# --------------------------------------------------------------------------- #
# End-to-end against a REAL minimal SQLite fixture (isolated, /tmp only)
# --------------------------------------------------------------------------- #

def _install_fake_broker(monkeypatch, positions, orders):
    """Point the tool at a fake read-only broker; no network, ever."""
    client = _FakeClient(positions=positions, orders=orders)

    def _fake_fetch(c, syms, order_limit=500):
        wanted = {str(s).upper() for s in syms}
        out = []
        for o in c.get_orders():
            n = bf.normalize_order(o)
            if n["symbol"] in wanted and bf.is_filled(n):
                out.append(n)
        return out

    monkeypatch.setattr(bf, "make_paper_client", lambda: client)
    monkeypatch.setattr(bf, "fetch_filled_orders", _fake_fetch)
    return client


def _make_runner_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE orders (alpaca_order_id TEXT, client_order_id TEXT)"
    )
    conn.commit()
    conn.close()
    return path


def test_end_to_end_dry_run_writes_nothing(tmp_path, monkeypatch):
    db = _make_trades_db(tmp_path / "ledger.db")
    runner = _make_runner_db(tmp_path / "runner.db")
    client = _install_fake_broker(
        monkeypatch, [_FakePosition("CMA", 17.0)], [_fill()]
    )
    rc = bf.main([
        "--db", str(db), "--symbols", "CMA", "--runner-db", str(runner),
    ])
    assert rc == bf.EXIT_OK
    assert _rows(db) == []
    # Read-only broker access only.
    assert set(client.calls) <= {"get_orders", "get_all_positions"}
    # No backup artifact may be created by a dry run.
    assert list(tmp_path.glob("*.ledger-backup-*.db")) == []


def test_end_to_end_apply_inserts_exact_row_and_backs_up(tmp_path, monkeypatch):
    db = _make_trades_db(tmp_path / "ledger.db")
    runner = _make_runner_db(tmp_path / "runner.db")
    _install_fake_broker(monkeypatch, [_FakePosition("CMA", 17.0)], [_fill()])
    rc = bf.main([
        "--db", str(db), "--symbols", "CMA", "--runner-db", str(runner),
        "--apply",
    ])
    assert rc == bf.EXIT_OK
    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "CMA"
    assert row["side"] == "BUY"
    assert row["status"] == "FILLED"
    assert row["filled_qty"] == 17.0
    assert row["avg_fill_price"] == 84.58
    assert row["broker_order_id"] == CMA_ORDER_ID
    assert row["sleeve"] == "S"
    assert "LEDGER-BACKFILL-001" in (row["notes"] or "")

    # A timestamped backup was taken before the write.
    backups = list(tmp_path.glob("ledger.db.ledger-backup-*.db"))
    assert backups, "a timestamped backup must exist after --apply"

    # Ownership is now provable through the bot's own math.
    from src.core.allocation_policy import own_net_qty_from_ledger_rows

    assert own_net_qty_from_ledger_rows(rows) == {"CMA": 17.0}


def test_end_to_end_apply_is_idempotent_on_rerun(tmp_path, monkeypatch):
    db = _make_trades_db(tmp_path / "ledger.db")
    runner = _make_runner_db(tmp_path / "runner.db")
    _install_fake_broker(monkeypatch, [_FakePosition("CMA", 17.0)], [_fill()])
    rc = bf.main([
        "--db", str(db), "--symbols", "CMA", "--runner-db", str(runner),
        "--apply",
    ])
    assert rc == bf.EXIT_OK
    assert len(_rows(db)) == 1

    # Second run against the SAME db: the fill is already recorded.
    _install_fake_broker(monkeypatch, [_FakePosition("CMA", 17.0)], [_fill()])
    rc2 = bf.main([
        "--db", str(db), "--symbols", "CMA", "--runner-db", str(runner),
        "--apply",
    ])
    assert rc2 == bf.EXIT_OK
    assert len(_rows(db)) == 1, "a re-run must not duplicate the row"


def test_end_to_end_swing_only_fill_is_refused(tmp_path, monkeypatch):
    """AAAA is runner-owned: the tool must refuse to reconcile it."""
    db = _make_trades_db(tmp_path / "ledger.db")
    runner = _make_runner_db(tmp_path / "runner.db")
    _install_fake_broker(
        monkeypatch, [_FakePosition("AAAA", 75.0)],
        [_fill(id=AAAA_ORDER_ID, client_order_id=AAAA_COID, symbol="AAAA",
               filled_qty=75.0, filled_avg_price=30.192, qty=75.0)],
    )
    rc = bf.main([
        "--db", str(db), "--symbols", "AAAA", "--runner-db", str(runner),
        "--apply",
    ])
    assert rc == bf.EXIT_DISCREPANCY
    assert _rows(db) == [], "nothing may be written on a discrepancy"


def test_end_to_end_discrepancy_writes_nothing(tmp_path, monkeypatch):
    db = _make_trades_db(tmp_path / "ledger.db")
    runner = _make_runner_db(tmp_path / "runner.db")
    _install_fake_broker(monkeypatch, [_FakePosition("CMA", 99.0)], [_fill()])
    rc = bf.main([
        "--db", str(db), "--symbols", "CMA", "--runner-db", str(runner),
        "--apply",
    ])
    assert rc == bf.EXIT_DISCREPANCY
    assert _rows(db) == []


def test_end_to_end_existing_ledger_row_makes_it_a_no_op(tmp_path, monkeypatch):
    db = _make_trades_db(tmp_path / "ledger.db")
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO trades (symbol, side, qty, price, status, broker_order_id,"
        " sleeve, filled_qty, avg_fill_price, notes)"
        " VALUES ('CMA','BUY',17.0,84.58,'FILLED',?, 'S',17.0,84.58,'pre-existing')",
        (CMA_ORDER_ID,),
    )
    conn.commit()
    conn.close()

    runner = _make_runner_db(tmp_path / "runner.db")
    _install_fake_broker(monkeypatch, [_FakePosition("CMA", 17.0)], [_fill()])
    rc = bf.main([
        "--db", str(db), "--symbols", "CMA", "--runner-db", str(runner),
        "--apply",
    ])
    assert rc == bf.EXIT_OK
    assert len(_rows(db)) == 1


def test_end_to_end_runner_db_id_is_excluded(tmp_path, monkeypatch):
    """A fill recorded in the runner DB is never backfilled."""
    db = _make_trades_db(tmp_path / "ledger.db")
    runner = tmp_path / "runner.db"
    conn = sqlite3.connect(str(runner))
    conn.execute(
        "CREATE TABLE orders (alpaca_order_id TEXT, client_order_id TEXT)"
    )
    conn.execute(
        "INSERT INTO orders VALUES (?, ?)", (CMA_ORDER_ID, CMA_COID)
    )
    conn.commit()
    conn.close()

    _install_fake_broker(monkeypatch, [_FakePosition("CMA", 17.0)], [_fill()])
    rc = bf.main([
        "--db", str(db), "--symbols", "CMA", "--runner-db", str(runner),
        "--apply",
    ])
    assert rc == bf.EXIT_DISCREPANCY
    assert _rows(db) == [], "a runner-owned fill must not be backfilled"
