#!/usr/bin/env bash
# Diagnose an agent-team installation: files, config schema, version drift.
# Static checks by default; --smoke additionally runs a minimal real Codex
# session to confirm agent roles actually load (costs tokens, needs auth).
set -uo pipefail

SMOKE=0
[[ "${1:-}" == "--smoke" ]] && SMOKE=1

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "error: not in a git repo" >&2; exit 2; }
cd "$ROOT"
FAIL=0
WARN=0

fail() { echo "FAIL: $*"; FAIL=1; }
warn() { echo "warn: $*"; WARN=1; }
ok()   { echo "ok:   $*"; }

# --- installed files ---
AGENTS=(researcher architect parallel-planner developer debugger frontend-debugger \
        system-debugger tester integration-tester spec-reviewer code-reviewer knowledge-distiller)
for a in "${AGENTS[@]}"; do
  [[ -f ".codex/agents/$a.toml" ]] || fail "missing .codex/agents/$a.toml"
done
[[ -f ".codex/agents/${AGENTS[0]}.toml" ]] && ok "${#AGENTS[@]} agent TOMLs present (or failures listed above)"

for b in wake-agent-team agent-team-finish agent-team-check-task agent-team-doctor; do
  if [[ ! -x ".codex/bin/$b" ]]; then fail "missing or non-executable .codex/bin/$b"; fi
done
[[ -f ".agents/skills/agent-team-dev/SKILL.md" ]] && ok "agent-team-dev skill installed" \
  || fail "missing .agents/skills/agent-team-dev/SKILL.md"

# --- config.toml schema ---
CFG=".codex/config.toml"
agents_block() {
  # lines inside the [agents] table only (keys under other tables don't count)
  awk '/^[[:space:]]*\[[[:space:]]*agents[[:space:]]*\][[:space:]]*(#.*)?$/{f=1;next}
       /^[[:space:]]*\[/{f=0} f' "$CFG"
}
if [[ -f "$CFG" ]]; then
  if grep -qE '^\s*\[\s*agents\s*\]' "$CFG"; then
    agents_block | grep -q '^\s*max_concurrent_threads_per_session\s*=' \
      && ok "[agents] max_concurrent_threads_per_session set" \
      || fail "$CFG [agents] missing max_concurrent_threads_per_session (concurrency cap silently absent)"
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
    if [[ -f "$SRC/skills/agent-team-dev/SKILL.md" ]] && \
       ! cmp -s "$SRC/skills/agent-team-dev/SKILL.md" ".agents/skills/agent-team-dev/SKILL.md"; then
      warn "installed SKILL.md differs from template source (local edit or stale install)"
    else
      ok "SKILL.md matches template source"
    fi
    if [[ -f "$SRC/VERSION" ]] && \
       [[ "$(cat "$SRC/VERSION")" != "$(grep -oP '^agent-team-template-version: \K.*' "$VER_FILE")" ]]; then
      warn "template source is newer than installed version; re-run the installer"
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
      "List the names of the custom subagent roles available in this project's configuration. Reply with names only, one per line. Do not edit anything." 2>&1 | tail -20)"
    if echo "$OUT" | grep -q "developer"; then
      ok "smoke: codex loaded custom agent roles"
    else
      fail "smoke: codex did not report custom agent roles; output tail:"
      echo "$OUT" | sed 's/^/      /'
    fi
  else
    fail "smoke requested but codex CLI not on PATH"
  fi
fi

echo
if [[ $FAIL -ne 0 ]]; then echo "doctor: FAIL"; exit 1; fi
if [[ $WARN -ne 0 ]]; then echo "doctor: OK with warnings"; exit 0; fi
echo "doctor: OK"
