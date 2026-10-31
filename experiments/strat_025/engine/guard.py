"""Hard paper-only guard — identical friction to experiments/strat_023.

The engine refuses to start (exit code 2) unless TRADING_BOT_PAPER_ONLY=1 is
set, and additionally refuses if any LIVE-looking endpoint is configured.
"""
from __future__ import annotations

import os
import sys


class PaperOnlyError(RuntimeError):
    pass


def enforce_paper_only(trading_url: str) -> None:
    """Raise PaperOnlyError (caller exits 2) unless the paper-only covenant holds."""
    if os.environ.get("TRADING_BOT_PAPER_ONLY") != "1":
        raise PaperOnlyError(
            "REFUSING TO RUN: set TRADING_BOT_PAPER_ONLY=1 to confirm paper-only "
            "mode. This engine places PAPER orders only and must never be wired "
            "into live execution."
        )
    url = (trading_url or "").lower()
    if "paper" not in url:
        raise PaperOnlyError(
            f"REFUSING TO RUN: trading endpoint {trading_url!r} is not the Alpaca "
            "PAPER endpoint. Live endpoints are forbidden."
        )
    if os.environ.get("STRAT025_ALLOW_LIVE") not in (None, "", "0"):
        raise PaperOnlyError(
            "REFUSING TO RUN: STRAT025_ALLOW_LIVE is set. There is no setting that "
            "enables live trading in this engine."
        )


def guard_or_exit(trading_url: str) -> None:
    """Convenience for CLI entry points: enforce or exit(2) like strat_023."""
    try:
        enforce_paper_only(trading_url)
    except PaperOnlyError as e:
        print(f"strat025: {e}", file=sys.stderr)
        sys.exit(2)
