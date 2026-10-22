#!/usr/bin/env python
"""READ-ONLY runtime verification of docs/SMARTBOT_STRATEGY.md persisted values.

Compares the document's ``<!-- param: key=value source=persisted -->`` markers
against the EFFECTIVE strategy settings loaded by the authoritative loader
(``src.core.settings_service.load_effective_strategy_settings`` — the same
source the bot uses). Never writes settings, never touches trading behavior.

Usage (in the deployed environment, e.g. /root/deploy/trading-bot-main)::

    python scripts/verify_strategy_doc_runtime.py [--doc PATH]

Exit codes:
    0  all persisted markers verified against effective runtime settings
    1  at least one MISMATCH between document and runtime
    2  runtime evidence UNAVAILABLE (could not load effective settings) —
       this is explicitly NOT a successful verification

GitHub CI does NOT run this script (no production DB access there); CI only
runs the negative/positive unit tests around its comparison logic.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOC = REPO_ROOT / "docs" / "SMARTBOT_STRATEGY.md"

MARKER_RE = re.compile(r"<!--\s*param:\s*([A-Za-z0-9_]+)=([^\s>]+)\s+source=(\w+)\s*-->")

VERIFIED = "VERIFIED"
MISMATCH = "MISMATCH"
UNAVAILABLE = "UNAVAILABLE"


def parse_markers(text: str) -> dict[str, tuple[str, str]]:
    """Return {key: (value, source)} from a strategy document's param markers."""
    markers: dict[str, tuple[str, str]] = {}
    for key, value, source in MARKER_RE.findall(text):
        markers[key] = (value, source)
    return markers


def coerce(raw: str):
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _values_match(documented: object, runtime: object) -> bool:
    """Type-strict comparison: numeric literals never 'match' booleans."""
    if isinstance(runtime, bool) or isinstance(documented, bool):
        return isinstance(documented, bool) and isinstance(runtime, bool) and documented == runtime
    return documented == runtime


def verify_persisted_markers(
    markers: dict[str, tuple[str, str]], effective: dict[str, object]
) -> tuple[list[str], list[str]]:
    """Compare persisted markers against effective settings.

    Returns (verified_keys, mismatch_descriptions).
    """
    verified: list[str] = []
    mismatches: list[str] = []
    for key, (value, source) in sorted(markers.items()):
        if source != "persisted":
            continue
        if key not in effective:
            mismatches.append(f"{key}: documented (persisted) but absent from effective settings")
            continue
        if not _values_match(coerce(value), effective[key]):
            mismatches.append(
                f"{key}: documented {value} != effective runtime {effective[key]!r}"
            )
        else:
            verified.append(key)
    return verified, mismatches


def load_effective(repo_root: Path | None = None) -> dict[str, object]:
    """Load effective settings via the authoritative loader (read-only).

    ``repo_root`` lets the verifier run against a deployed checkout whose
    settings_service resolves the production DB next to itself.
    """
    sys.path.insert(0, str(repo_root or REPO_ROOT))
    from src.core.settings_service import load_effective_strategy_settings

    return dict(load_effective_strategy_settings())


def git_revision() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc", type=Path, default=DEFAULT_DOC)
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="repository root whose settings_service/DB to verify against "
        "(defaults to the script's own repo)",
    )
    args = parser.parse_args()

    print(f"timestamp_utc={_dt.datetime.now(_dt.timezone.utc).isoformat()}")
    print(f"deployment_revision={git_revision()}")
    print(f"doc={args.doc}")

    markers = parse_markers(args.doc.read_text())
    persisted = {k for k, (_, s) in markers.items() if s == "persisted"}
    if not persisted:
        print("NO PERSISTED MARKERS FOUND IN DOCUMENT")
        return 1

    try:
        effective = load_effective(args.repo)
    except Exception as exc:  # noqa: BLE001 - report any loader failure honestly
        print(f"status={UNAVAILABLE}")
        print(f"reason=could not load effective settings: {type(exc).__name__}")
        print("NOTE: unavailable runtime evidence is NOT a successful verification.")
        return 2

    verified, mismatches = verify_persisted_markers(markers, effective)
    for key in verified:
        print(f"  {key}: {VERIFIED}")
    for line in mismatches:
        print(f"  {line} [{MISMATCH}]")

    if mismatches:
        print(f"status={MISMATCH} ({len(mismatches)} mismatch(es), {len(verified)} verified)")
        return 1
    print(f"status={VERIFIED} ({len(verified)} persisted markers verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
