#!/usr/bin/env python3
"""Integration checks for Supabase REST connectivity and writes.

These tests are skipped unless Supabase credentials are present and reachable.
"""

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from database.simple_rest import SimpleSupabaseREST


def _require_supabase_creds():
    if not os.getenv("SUPABASE_URL"):
        pytest.skip("SUPABASE_URL missing; skip integration test")
    if not (os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")):
        pytest.skip("Supabase key missing; skip integration test")


def _cleanup_symbol(rest: SimpleSupabaseREST, symbol: str):
    try:
        rest.session.delete(
            f"{rest.rest_url}/research_cooldowns?symbol=eq.{symbol}",
            timeout=5,
        )
    except Exception:
        pass


@pytest.mark.integration
@pytest.mark.parametrize("symbol_prefix", ["TSTDB"])
def test_supabase_connection_and_research_insert(symbol_prefix: str):
    _require_supabase_creds()

    rest = SimpleSupabaseREST()
    if not rest.is_available():
        pytest.skip("Supabase not reachable; skip integration test")

    info = rest.get_database_info()
    assert info["available"] is True

    # Perform a write/read round-trip on research_cooldowns
    # Supabase schema expects symbol VARCHAR(10); keep under the limit
    symbol = f"{symbol_prefix}{uuid4().hex[:4].upper()}"
    now = datetime.now(timezone.utc)

    try:
        ok = rest.set_research_cooldown(symbol, now)
        if not ok:
            pytest.fail(f"set_research_cooldown failed: {rest.last_error}")

        retrieved = rest.get_research_cooldown(symbol)
        assert retrieved is not None
        if retrieved.tzinfo is None:
            retrieved = retrieved.replace(tzinfo=timezone.utc)
        assert abs((retrieved - now).total_seconds()) < 5 * 60  # within a few minutes tolerance
    finally:
        _cleanup_symbol(rest, symbol)