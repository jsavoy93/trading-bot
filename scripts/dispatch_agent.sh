#!/usr/bin/env bash
# dispatch_agent.sh — safely dispatch an openclaw agent turn with a message
# read from a FILE (never inline), so pipes |, quotes, $, backticks, and
# newlines survive unchanged. (Engineering task ENG-02.)
#
# Usage:
#   scripts/dispatch_agent.sh [--dry-run|--echo] <agent-id> <task-id> <model> <message-file>
#
# The documented and default target for CODE work is engineering-manager:
#   scripts/dispatch_agent.sh engineering-manager <TASKID> minimax-portal/MiniMax-M3 /tmp/<taskid>-brief.txt
#
# The agent-id argument stays REQUIRED and positional (there is no default value),
# so this helper can still target trading-manager for RESEARCH work:
#   scripts/dispatch_agent.sh trading-manager <TASKID> minimax-portal/MiniMax-M3 /tmp/<taskid>-brief.txt
#
# --dry-run / --echo: print the exact command that would run plus the message
# verbatim, then exit 0 WITHOUT dispatching anything. Tests rely on this.
#
# Session keys are always fresh: agent:<agent>:<TASKID>-<unix-time> (ENG-01
# rule: session keys are never reused).

set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage: dispatch_agent.sh [--dry-run|--echo] <agent-id> <task-id> <model> <message-file>

The documented and default target for CODE work is engineering-manager:
  scripts/dispatch_agent.sh engineering-manager <TASKID> minimax-portal/MiniMax-M3 /tmp/<taskid>-brief.txt

The agent-id argument stays REQUIRED and positional (there is no default value),
so this helper can still target trading-manager for RESEARCH work:
  scripts/dispatch_agent.sh trading-manager <TASKID> minimax-portal/MiniMax-M3 /tmp/<taskid>-brief.txt
USAGE
  exit 2
}

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" || "${1:-}" == "--echo" ]]; then
  DRY_RUN=1
  shift
fi

[[ $# -eq 4 ]] || usage

AGENT="$1"
TASK_ID="$2"
MODEL="$3"
MESSAGE_FILE="$4"

if [[ ! -f "$MESSAGE_FILE" ]]; then
  echo "ERROR: message file not found: $MESSAGE_FILE" >&2
  exit 3
fi
if [[ ! -s "$MESSAGE_FILE" ]]; then
  echo "ERROR: message file is empty: $MESSAGE_FILE" >&2
  exit 3
fi
if [[ -d "$MESSAGE_FILE" ]]; then
  echo "ERROR: message file is a directory: $MESSAGE_FILE" >&2
  exit 3
fi

# Sub-second timestamp guarantees a fresh key even for rapid successive
# calls (ENG-01: session keys are never reused).
SESSION_STAMP="$(date -u +%s%N)"
[[ "$SESSION_STAMP" =~ ^[0-9]+$ ]] || SESSION_STAMP="$(date -u +%s)$RANDOM"
SESSION_KEY="agent:${AGENT}:${TASK_ID}-${SESSION_STAMP}"
LOG_FILE="/root/${TASK_ID}_run.log"

# Read the message exactly once from the file. Quoted heredoc semantics: no
# expansion, byte-for-byte preservation of | ' " $ ` and newlines.
# The trailing sentinel protects trailing newlines, which plain $(cat) strips.
MESSAGE="$(cat -- "$MESSAGE_FILE"; printf 'x')"
MESSAGE="${MESSAGE%x}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "DRY-RUN: would dispatch with session key: ${SESSION_KEY}"
  echo "DRY-RUN: exact command:"
  printf 'nohup openclaw agent --agent %q --session-key %q --model %q --message %q --json --timeout 3600 > %q 2>&1 &\n' \
    "$AGENT" "$SESSION_KEY" "$MODEL" "$MESSAGE" "$LOG_FILE"
  echo "DRY-RUN: message follows verbatim between markers:"
  echo "-----BEGIN MESSAGE-----"
  printf '%s' "$MESSAGE"
  # Separator so the END marker always starts on its own line without altering
  # the message bytes themselves (a trailing newline in the message is kept).
  if [[ -z "$MESSAGE" || "${MESSAGE: -1}" != $'\n' ]]; then
    printf '\n'
  fi
  echo "-----END MESSAGE-----"
  if command -v sha256sum >/dev/null 2>&1; then
    echo "DRY-RUN: message sha256: $(printf '%s' "$MESSAGE" | sha256sum | cut -d' ' -f1)"
  fi
  exit 0
fi

nohup openclaw agent \
  --agent "$AGENT" \
  --session-key "$SESSION_KEY" \
  --model "$MODEL" \
  --message "$MESSAGE" \
  --json \
  --timeout 3600 \
  > "$LOG_FILE" 2>&1 &

echo "dispatched agent=${AGENT} session=${SESSION_KEY} log=${LOG_FILE} pid=$!"
