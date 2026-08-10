#!/usr/bin/env bash
# Diagnose a QTeam project runtime: plugin, files, config schema, version drift.
# Static checks by default; --smoke additionally runs a minimal real Codex
# session to confirm agent roles actually load (costs tokens, needs auth).
set -uo pipefail

SMOKE=0
[[ "${1:-}" == "--smoke" ]] && SMOKE=1

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "error: not in a git repo" >&2; exit 2; }
cd "$ROOT" || exit 2
FAIL=0
WARN=0

fail() { echo "FAIL: $*"; FAIL=1; }
warn() { echo "warn: $*"; WARN=1; }
ok()   { echo "ok:   $*"; }

# --- installed files ---
AGENTS=(researcher architect parallel-planner test-designer spec-reviewer standards-reviewer risk-reviewer)
for a in "${AGENTS[@]}"; do
  [[ -f ".codex/agents/$a.toml" ]] || fail "missing .codex/agents/$a.toml"
done
[[ -f ".codex/agents/${AGENTS[0]}.toml" ]] && ok "${#AGENTS[@]} agent TOMLs present (or failures listed above)"

for b in wake-agent-team agent-team-artifact agent-team-finish agent-team-check-task agent-team-doctor \
         agent-team-state agent-team-worker agent-team-review qteam-project-uninstall; do
  if [[ ! -x ".codex/bin/$b" ]]; then fail "missing or non-executable .codex/bin/$b"; fi
done
[[ -f ".codex/bin/agent_team_policy.py" ]] || fail "missing policy module: agent_team_policy.py"
[[ -f ".codex/bin/agent_team_artifact.py" ]] || fail "missing artifact module: agent_team_artifact.py"
[[ -f ".codex/bin/qteam_project.py" ]] || fail "missing project manifest module: qteam_project.py"

for role in developer debugger frontend-debugger system-debugger test-writer \
            integration-tester fixer knowledge-distiller; do
  [[ -f ".codex/worker-prompts/$role.md" ]] || fail "missing worker prompt: $role"
done
for schema in run-state task task-policy tdd-cycle diagnosis experiment decision-gate handoff scenario-coverage worker-result verification finding review-result review-receipt artifact-lint code-index epic spec-drift; do
  [[ -f ".codex/schemas/$schema.schema.json" ]] || fail "missing schema: $schema"
done
SCHEMA_OK=1
for schema_file in .codex/schemas/*.schema.json; do
  python3 -m json.tool "$schema_file" >/dev/null 2>&1 || { fail "invalid JSON schema: $schema_file"; SCHEMA_OK=0; }
done
[[ $SCHEMA_OK -eq 1 ]] && ok "all JSON schemas parse"

for duplicate in agent-team-dev qteam-router qteam-tdd qteam-diagnose qteam-explore qteam-review \
                 using-superpowers executing-plans subagent-driven-development \
                 requesting-code-review receiving-code-review \
                 finishing-a-development-branch using-git-worktrees \
                 test-driven-development systematic-debugging \
                 dispatching-parallel-agents; do
  [[ ! -d ".agents/skills/$duplicate" ]] \
    || fail "stale repository-local orchestration skill conflicts with QTeam plugin: $duplicate"
done

# --- plugin registration ---
if command -v codex >/dev/null 2>&1; then
  PLUGIN_LIST="$(codex plugin list --json 2>/dev/null || true)"
  if python3 -c '
import json, sys
try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, TypeError):
    raise SystemExit(1)
raise SystemExit(0 if any(item.get("pluginId") == "qteam@qteam"
                          and item.get("installed") is True
                          and item.get("enabled") is True
                          for item in payload.get("installed", [])) else 1)
' <<<"$PLUGIN_LIST"; then
    ok "qteam@qteam plugin installed and enabled"
  else
    fail "qteam@qteam plugin is not installed and enabled"
  fi
else
  fail "codex CLI is not installed or not on PATH"
fi

# --- config.toml schema ---
CFG=".codex/config.toml"
agents_block() {
  # lines inside the [agents] table only (keys under other tables don't count)
  awk '/^[[:space:]]*\[[[:space:]]*agents[[:space:]]*\][[:space:]]*(#.*)?$/{f=1;next}
       /^[[:space:]]*\[/{f=0} f' "$CFG"
}
if [[ -f "$CFG" ]]; then
  if grep -qE '^\s*\[\s*agents\s*\]' "$CFG"; then
    if agents_block | grep -q '^\s*max_concurrent_threads_per_session\s*='; then
      ok "[agents] max_concurrent_threads_per_session set"
    else
      fail "$CFG [agents] missing max_concurrent_threads_per_session (concurrency cap silently absent)"
    fi
    agents_block | grep -q '^\s*max_depth\s*=' || warn "$CFG [agents] missing max_depth"
    agents_block | grep -q '^\s*max_threads\s*=' \
      && warn "$CFG has legacy 'max_threads' — codex ignores it; remove it (re-run installer)"
  else
    fail "$CFG has no [agents] section"
  fi
else
  fail "missing $CFG"
fi

# --- codex binary schema check ---
CODEX_BIN=""
SKIP_SCHEMA=0
if ! command -v strings >/dev/null 2>&1 || ! command -v file >/dev/null 2>&1; then
  warn "binutils 'strings' or 'file' not available; skipping codex schema check"
  SKIP_SCHEMA=1
elif command -v codex >/dev/null 2>&1; then
  CAND="$(readlink -f "$(command -v codex)")"
  if file "$CAND" | grep -q ELF; then
    CODEX_BIN="$CAND"
  else
    # node/bash wrapper: search common npm install locations for the native binary
    for p in "$HOME"/.npm-global/lib/node_modules/@openai/codex*/node_modules/@openai/*/vendor/*/bin/codex \
             /usr/local/lib/node_modules/@openai/codex*/node_modules/@openai/*/vendor/*/bin/codex \
             /usr/lib/node_modules/@openai/codex*/node_modules/@openai/*/vendor/*/bin/codex; do
      [[ -f "$p" ]] && file "$p" | grep -q ELF && { CODEX_BIN="$p"; break; }
    done
  fi
fi
if [[ "$SKIP_SCHEMA" -eq 1 ]]; then
  :
elif [[ -n "$CODEX_BIN" ]]; then
  # extract strings once: `strings | grep -q` would fail under pipefail (SIGPIPE)
  STRTMP="$(mktemp)"
  trap 'rm -f "$STRTMP"' EXIT
  strings -a "$CODEX_BIN" > "$STRTMP"
  for field in developer_instructions sandbox_mode nickname_candidates \
               max_concurrent_threads_per_session max_depth; do
    if grep -qw "$field" "$STRTMP"; then
      ok "codex binary knows '$field'"
    else
      fail "codex binary does NOT know '$field' — schema drift; template config may be silently ignored"
    fi
  done
else
  warn "could not locate native codex binary; skipping schema check"
fi

# --- version / drift ---
VER_FILE=".codex/agent-team-template.version"
if [[ -f "$VER_FILE" ]]; then
  cat "$VER_FILE" | sed 's/^/info: /'
  SRC="$(grep -oP '^source-path: \K.*' "$VER_FILE" 2>/dev/null || true)"
  if [[ -n "$SRC" && -d "$SRC" ]]; then
    if [[ -f "$SRC/VERSION" ]] && \
       [[ "$(cat "$SRC/VERSION")" != "$(grep -oP '^qteam-plugin-version: \K.*' "$VER_FILE")" ]]; then
      warn "plugin source is newer than project runtime; run qteam setup again"
    else
      ok "project runtime matches plugin source version"
    fi
  fi
else
  warn "missing $VER_FILE (installed by an old template version)"
fi

# --- optional smoke test ---
if [[ $SMOKE -eq 1 ]]; then
  if command -v codex >/dev/null 2>&1; then
    echo "info: running smoke test (read-only codex exec)..."
    OUT="$(codex exec -C "$ROOT" --sandbox read-only \
      "Spawn the test_designer role with fork_turns=none. Ask it to return only its QTEAM_ROLE_MARKER. Return that marker only. Do not edit anything." 2>&1 | tail -40)"
    if echo "$OUT" | grep -q "QTEAM_ROLE_MARKER:test-designer-v1"; then
      ok "smoke: specified role loaded with bounded context"
    else
      fail "smoke: specified role/fork_turns=none contract failed; output tail:"
      while IFS= read -r line; do printf '      %s\n' "$line"; done <<< "$OUT"
    fi
  else
    fail "smoke requested but codex CLI not on PATH"
  fi
fi

echo
if [[ $FAIL -ne 0 ]]; then echo "doctor: FAIL"; exit 1; fi
if [[ $WARN -ne 0 ]]; then echo "doctor: OK with warnings"; exit 0; fi
echo "doctor: OK"
