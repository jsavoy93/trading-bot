"""GAPFADE-MIDCAP-PAPER — PAPER-only guards (mirror of the C4 runner).

--execute refuses to place any order unless ALL are true:
  1. env TRADING_BOT_PAPER_ONLY=1
  2. the trading base URL is EXACTLY https://paper-api.alpaca.markets
     (hard-locked constant; no env override can change it)
  3. the kill-switch file does not exist
Any failure prints a message to stderr and exits with code 2.

Dry-run never needs the env or the kill-switch and always places zero
orders: the guard is only consulted by the execute path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# --- hard-locked paper endpoint (never configurable) ---------------------
PAPER_API_URL = "https://paper-api.alpaca.markets"
PAPER_ONLY_ENV = "TRADING_BOT_PAPER_ONLY"

HERE = Path(__file__).resolve().parent
DEFAULT_KILL_FILE = HERE / "KILL_GAPFADE_MIDCAP_PAPER"
KILL_FILE_ENV = "GAPFADE_MIDCAP_KILL_FILE"

EXIT_GUARD_FAILED = 2


class GuardError(RuntimeError):
    """Raised by check_paper_only(); carries the exit code (always 2)."""

    exit_code = EXIT_GUARD_FAILED


def trading_base_url() -> str:
    """The only trading URL this experiment can ever use."""
    return PAPER_API_URL


def kill_switch_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get(KILL_FILE_ENV)
    return Path(env) if env else DEFAULT_KILL_FILE


def check_paper_only(env: dict | None = None,
                     base_url: str | None = None,
                     kill_file: str | Path | None = None) -> None:
    """Raise GuardError unless every paper-only condition holds."""
    env = os.environ if env is None else env
    url = PAPER_API_URL if base_url is None else str(base_url)
    if env.get(PAPER_ONLY_ENV) != "1":
        raise GuardError(
            f"REFUSING TO EXECUTE: {PAPER_ONLY_ENV}=1 is required "
            "(paper account only).")
    if url != PAPER_API_URL:
        raise GuardError(
            f"REFUSING TO EXECUTE: trading base URL must be exactly "
            f"{PAPER_API_URL} (got {url}).")
    kill = kill_switch_path(kill_file)
    if kill.exists():
        raise GuardError(
            f"KILL SWITCH ACTIVE ({kill} exists): no orders will be placed. "
            "Delete the file to re-enable.")


def run_cli(main_fn, argv: list[str] | None = None) -> int:
    """Parse argv: --execute is guarded, --dry-run (default) is not.

    `main_fn(execute=bool, argv=...)` is called with keyword arguments; a
    main_fn that also needs the parsed CLI args can accept `namespace=`.
    """
    import argparse
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args, _rest = p.parse_known_args(argv)
    if args.execute and args.dry_run:
        print("gapfade_midcap_paper: --execute and --dry-run are mutually "
              "exclusive", file=sys.stderr)
        return EXIT_GUARD_FAILED
    if _rest and _rest[0] in ("-h", "--help"):
        return main_fn(execute=False, argv=argv)
    if args.execute:
        try:
            check_paper_only()
        except GuardError as exc:
            print(f"gapfade_midcap_paper: {exc}", file=sys.stderr)
            return EXIT_GUARD_FAILED
        return main_fn(execute=True, argv=argv)
    # default: dry run, zero orders, no guard needed
    return main_fn(execute=False, argv=argv)
