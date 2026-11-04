#!/usr/bin/env bash
# STRAT-SWING-PAPER daily RSI(2) paper executor (Josh-approved 2026-11-04).
# Runs the PR-135 runner from the dedicated clean checkout of main @ e1f53a3
# (/root/deploy/rsi2-swing). PAPER ONLY:
#   - the runner's trading URL is hard-locked to https://paper-api.alpaca.markets
#   - TRADING_BOT_PAPER_ONLY=1 is required for --execute
# Credentials are sourced READ-ONLY from the existing workspace paper .env
# (same file the strat023-c4-shadow unit reads; never modified).
set -euo pipefail

REPO=/root/deploy/rsi2-swing
RUNNER=$REPO/experiments/swing_paper/runner.py
PY=$REPO/.venv/bin/python
ENV_FILE=/root/.openclaw/workspace/trading-bot/.env
UNIVERSE_FILE=/root/.openclaw/workspace/trading-bot/experiments/strat_006r_candidate_universe.py

# Kill switch: create this file to halt all future runs (runner exits on its own
# KILL_SWITCH check; this pre-check also stops the run before any API call).
KILL_SWITCH=$REPO/experiments/swing_paper/KILL_SWITCH
if [ -e "$KILL_SWITCH" ]; then
    echo "swing-rsi2-paper: KILL_SWITCH present ($KILL_SWITCH) — refusing to run" >&2
    exit 3
fi

# Paper-only guard
export TRADING_BOT_PAPER_ONLY=1

# Read-only paper credentials
set -a
source <(grep -E '^(ALPACA_API_KEY|ALPACA_API_SECRET)=' "$ENV_FILE")
set +a

# Symbol universe: same STRAT-006R curated list the shadow runners use.
SYMBOLS=$("$PY" - "$UNIVERSE_FILE" <<'EOF'
import ast, sys
src = open(sys.argv[1]).read()
tree = ast.parse(src)
for node in tree.body:
    if isinstance(node, ast.Assign) and any(
        getattr(t, "id", "") == "POPULAR_LIQUID_US" for t in node.targets):
        print(" ".join(e.value for e in node.value.elts))
        break
EOF
)
if [ -z "$SYMBOLS" ]; then echo "swing-rsi2-paper: empty universe" >&2; exit 4; fi

cd "$REPO"
exec "$PY" "$RUNNER" --execute --sleeve-pct 0.15 --rsi2-cap 4 \
    --max-open 4 --per-position-cap 0.25 --symbols $SYMBOLS
