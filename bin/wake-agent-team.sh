#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'U'
usage: wake-agent-team [--exec] [--plan <file>] [--allow-assumptions] [--] [goal...]

  --exec               non-interactive run (codex exec). Requires an approved
                       spec/plan: an active run under .agents/runs/, or
                       --plan <file>, or --allow-assumptions.
  --plan <file>        path to an approved plan file to execute.
  --allow-assumptions  let an unattended run derive assumptions from the goal
                       (recorded in the plan file) instead of failing.
U
  exit 2
}

if ! command -v git >/dev/null 2>&1; then
  echo "error: git is required" >&2
  exit 127
fi
if ! command -v codex >/dev/null 2>&1; then
  echo "error: codex CLI is not installed or not on PATH" >&2
  echo "install/authenticate Codex first, then rerun this script" >&2
  exit 127
fi

ROOT="$(git rev-parse --show-toplevel)"
PROMPT_FILE="$ROOT/.agents/skills/agent-team-dev/references/wake-prompt.md"
if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "error: missing $PROMPT_FILE" >&2
  echo "run the qnote codex-agent-team-template installer first" >&2
  exit 2
fi

MODE="interactive"
PLAN_FILE=""
ALLOW_ASSUMPTIONS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --exec) MODE="exec"; shift ;;
    --plan) PLAN_FILE="${2:?--plan needs a file}"; shift 2 ;;
    --allow-assumptions) ALLOW_ASSUMPTIONS=1; shift ;;
    -h|--help) usage ;;
    --) shift; break ;;
    -*) usage ;;
    *) break ;;
  esac
done

active_runs() {
  # Fail closed: a corrupt state file must not disappear from discovery and
  # permit a second run to start against the same repository.
  python3 - "$ROOT" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
errors = []
for path in sorted((root / ".agents/runs").glob("*/state.json")):
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not isinstance(state.get("finished"), bool):
            raise ValueError("state must be an object with boolean 'finished'")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"{path}: {exc}")
        continue
    if not state["finished"]:
        print(path)
if errors:
    for error in errors:
        print(f"error: invalid run state: {error}", file=sys.stderr)
    raise SystemExit(5)
PY
}

BASE_PROMPT="$(cat "$PROMPT_FILE")"
FULL_PROMPT="$BASE_PROMPT"
if [[ $# -gt 0 ]]; then
  FULL_PROMPT="$FULL_PROMPT

User goal:
$*"
fi
if [[ -n "$PLAN_FILE" ]]; then
  # normalize to a repo-root-relative path: codex runs with -C "$ROOT", so a
  # cwd-relative path from a subdirectory would point at the wrong file
  if [[ "$PLAN_FILE" = /* ]]; then
    PLAN_ABS="$PLAN_FILE"
  elif [[ -f "$ROOT/$PLAN_FILE" ]]; then
    PLAN_ABS="$ROOT/$PLAN_FILE"
  elif [[ -f "$PLAN_FILE" ]]; then
    PLAN_ABS="$(cd "$(dirname "$PLAN_FILE")" && pwd)/$(basename "$PLAN_FILE")"
  else
    echo "error: plan file not found: $PLAN_FILE" >&2
    exit 2
  fi
  if [[ ! -f "$PLAN_ABS" ]]; then
    echo "error: plan file not found: $PLAN_ABS" >&2
    exit 2
  fi
  PLAN_REF="${PLAN_ABS#"$ROOT"/}"
  FULL_PROMPT="$FULL_PROMPT

Approved plan: $PLAN_REF — execute it; do not re-brainstorm the design."
fi

if [[ "$MODE" == "exec" ]]; then
  RUNS="$(active_runs)"
  RUN_COUNT="$(printf '%s\n' "$RUNS" | sed '/^$/d' | wc -l)"
  if [[ "$RUN_COUNT" -gt 1 ]]; then
    echo "error: multiple active runs; unattended execution cannot choose:" >&2
    printf '%s\n' "$RUNS" | sed '/^$/d;s/^/  /' >&2
    exit 4
  fi
  RUN="$RUNS"
  if [[ -z "$RUN" && -z "$PLAN_FILE" && $ALLOW_ASSUMPTIONS -eq 0 ]]; then
    echo "EXEC_REQUIRES_APPROVED_SPEC" >&2
    echo "error: --exec needs an active run, --plan <file>, or --allow-assumptions." >&2
    echo "Unattended runs must not guess requirements by default." >&2
    exit 3
  fi
  FULL_PROMPT="$FULL_PROMPT

Non-interactive run: no user is available for clarification."
  if [[ -n "$RUN" ]]; then
    FULL_PROMPT="$FULL_PROMPT
An active run exists ($RUN): resume it from its recorded phase."
  fi
  if [[ $ALLOW_ASSUMPTIONS -eq 1 ]]; then
    FULL_PROMPT="$FULL_PROMPT
Assumptions are explicitly allowed: derive minimal assumptions from the goal, record them in the plan file before implementing, prefer the safest reasonable interpretation, and list open questions in the final summary."
  fi
  exec codex exec -C "$ROOT" "$FULL_PROMPT"
fi

RUNS="$(active_runs)"
RUN_COUNT="$(printf '%s\n' "$RUNS" | sed '/^$/d' | wc -l)"
if [[ "$RUN_COUNT" -gt 1 ]]; then
  FULL_PROMPT="$FULL_PROMPT
Multiple active runs exist. List them and ask the user which one to resume before any other action:
$RUNS"
fi

exec codex -C "$ROOT" "$FULL_PROMPT"
