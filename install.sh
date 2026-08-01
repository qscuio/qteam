#!/usr/bin/env bash
set -euo pipefail

if ! command -v git >/dev/null 2>&1; then
  echo "error: git is required" >&2
  exit 127
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required" >&2
  exit 127
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_ARG="${1:-$PWD}"
PROJECT_ROOT="$(git -C "$TARGET_ARG" rev-parse --show-toplevel)"
STAMP="$(date +%Y%m%d%H%M%S%N)-$$"
TEMPLATE_VERSION="$(cat "$SCRIPT_DIR/VERSION" 2>/dev/null || echo unknown)"
SOURCE_COMMIT="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"

install_file() {
  local src="$1"
  local dst="$2"
  local mode="${3:-0644}"
  mkdir -p "$(dirname "$dst")"
  if [[ -f "$dst" ]] && ! cmp -s "$src" "$dst"; then
    cp "$dst" "$dst.bak.$STAMP"
  fi
  cp "$src" "$dst"
  chmod "$mode" "$dst"
}

install_dir_replace() {
  local src="$1"
  local dst="$2"
  mkdir -p "$(dirname "$dst")"
  if [[ -e "$dst" ]]; then
    local backup
    if [[ "$dst" == "$PROJECT_ROOT/.agents/skills/"* ]]; then
      mkdir -p "$PROJECT_ROOT/.codex/qteam-backups/skills"
      backup="$PROJECT_ROOT/.codex/qteam-backups/skills/$(basename "$dst").$STAMP"
    else
      backup="$dst.bak.$STAMP"
    fi
    [[ ! -e "$backup" ]] || { echo "error: backup collision: $backup" >&2; exit 2; }
    mv "$dst" "$backup"
  fi
  mkdir -p "$dst"
  cp -R "$src"/. "$dst"/
}

mkdir -p "$PROJECT_ROOT/.codex/agents" "$PROJECT_ROOT/.codex/bin" \
  "$PROJECT_ROOT/.codex/worker-prompts" "$PROJECT_ROOT/.codex/schemas" \
  "$PROJECT_ROOT/.agents/skills"

# --- .codex/config.toml: [agents] concurrency + depth ---
# Field names verified against the Codex binary's config schema; legacy
# 'max_threads' is not a real field and is migrated away.
CONFIG="$PROJECT_ROOT/.codex/config.toml"
python3 - "$CONFIG" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
text = path.read_text(encoding="utf-8") if path.exists() else ""
if text and not text.endswith("\n"):
    text += "\n"

lines = text.splitlines()
header_re = re.compile(r"^\s*\[\s*agents\s*\]\s*(?:#.*)?$")
section_re = re.compile(r"^\s*\[[^\]]+\]\s*(?:#.*)?$")

if not any(header_re.match(line) for line in lines):
    if text.strip():
        text += "\n"
    text += "[agents]\nmax_concurrent_threads_per_session = 6\nmax_depth = 1\n"
    path.write_text(text, encoding="utf-8")
    raise SystemExit(0)

start = next(i for i, line in enumerate(lines) if header_re.match(line))
end = next((i for i in range(start + 1, len(lines)) if section_re.match(lines[i])), len(lines))
block = lines[start + 1:end]

# migrate legacy max_threads (never a valid codex field) to the real key,
# keeping the full value and any trailing comment
migrated = []
for line in block:
    m = re.match(r"^\s*max_threads\s*=\s*(.+)$", line)
    if m:
        migrated.append(f"max_concurrent_threads_per_session = {m.group(1)}")
    else:
        migrated.append(line)
block = migrated

existing = set()
for line in block:
    m = re.match(r"^\s*(max_concurrent_threads_per_session|max_depth)\s*=", line)
    if m:
        existing.add(m.group(1))

if "max_concurrent_threads_per_session" not in existing:
    block.append("max_concurrent_threads_per_session = 6")
if "max_depth" not in existing:
    block.append("max_depth = 1")

lines[start + 1:end] = block
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

# --- agents, bin, skills ---
for agent in researcher architect parallel-planner test-designer spec-reviewer standards-reviewer risk-reviewer; do
  install_file "$SCRIPT_DIR/agents/$agent.toml" "$PROJECT_ROOT/.codex/agents/$agent.toml"
done

# Remove role files from old releases that could let a coordinator accidentally
# spawn writable native subagents without a per-task cwd boundary.
for obsolete in developer debugger frontend-debugger system-debugger tester \
                integration-tester code-reviewer knowledge-distiller; do
  if [[ -f "$PROJECT_ROOT/.codex/agents/$obsolete.toml" ]]; then
    mv "$PROJECT_ROOT/.codex/agents/$obsolete.toml" \
      "$PROJECT_ROOT/.codex/agents/$obsolete.toml.bak.$STAMP"
  fi
done

for prompt in "$SCRIPT_DIR"/worker-prompts/*.md; do
  install_file "$prompt" "$PROJECT_ROOT/.codex/worker-prompts/$(basename "$prompt")"
done
for schema in "$SCRIPT_DIR"/schemas/*.json; do
  install_file "$schema" "$PROJECT_ROOT/.codex/schemas/$(basename "$schema")"
done

install_file "$SCRIPT_DIR/bin/wake-agent-team.sh" "$PROJECT_ROOT/.codex/bin/wake-agent-team" 0755
install_file "$SCRIPT_DIR/bin/agent-team-finish.py" "$PROJECT_ROOT/.codex/bin/agent-team-finish" 0755
install_file "$SCRIPT_DIR/bin/agent-team-check-task.py" "$PROJECT_ROOT/.codex/bin/agent-team-check-task" 0755
install_file "$SCRIPT_DIR/bin/agent-team-doctor.sh" "$PROJECT_ROOT/.codex/bin/agent-team-doctor" 0755
install_file "$SCRIPT_DIR/bin/agent-team-state.py" "$PROJECT_ROOT/.codex/bin/agent-team-state" 0755
install_file "$SCRIPT_DIR/bin/agent-team-worker.py" "$PROJECT_ROOT/.codex/bin/agent-team-worker" 0755
install_file "$SCRIPT_DIR/bin/agent-team-review.py" "$PROJECT_ROOT/.codex/bin/agent-team-review" 0755
install_file "$SCRIPT_DIR/THIRD_PARTY_NOTICES.md" "$PROJECT_ROOT/.codex/QTEAM-THIRD-PARTY-NOTICES.md"
install_file "$SCRIPT_DIR/LICENSES/Matt-Pocock-MIT.txt" "$PROJECT_ROOT/.codex/licenses/Matt-Pocock-MIT.txt"

# --- version stamp (read by agent-team-doctor for drift detection) ---
cat > "$PROJECT_ROOT/.codex/agent-team-template.version" <<V
agent-team-template-version: $TEMPLATE_VERSION
source-commit: $SOURCE_COMMIT
source-path: $SCRIPT_DIR
installed-at: $STAMP
V

# --- .gitignore: run infrastructure must never be committed ---
GITIGNORE="$PROJECT_ROOT/.gitignore"
if ! grep -qs '^\.agents/runs/$' "$GITIGNORE"; then
  {
    echo ""
    echo "# codex-agent-team-template run infrastructure"
    echo ".agents/runs/"
    echo ".agents/tmp/"
    echo "*.bak.*"
  } >> "$GITIGNORE"
fi

# --- bundled skills: QTeam owns orchestration; raw upstream sources are not installed ---
for src in "$SCRIPT_DIR"/skills/*; do
  [[ -d "$src" ]] || continue
  if [[ "$(basename "$src")" == "superpowers" ]]; then
    continue
  else
    install_dir_replace "$src" "$PROJECT_ROOT/.agents/skills/$(basename "$src")"
  fi
done

# Retire competing/overlapping skills installed by QTeam <=0.3. They are
# preserved as backups but cannot remain triggerable in the live skill set.
for name in using-superpowers executing-plans subagent-driven-development \
            requesting-code-review receiving-code-review \
            finishing-a-development-branch using-git-worktrees \
            test-driven-development systematic-debugging \
            dispatching-parallel-agents; do
  if [[ -d "$PROJECT_ROOT/.agents/skills/$name" ]]; then
    mkdir -p "$PROJECT_ROOT/.codex/qteam-backups/skills"
    mv "$PROJECT_ROOT/.agents/skills/$name" \
      "$PROJECT_ROOT/.codex/qteam-backups/skills/$name.$STAMP"
  fi
done

cat <<OUT
installed codex agent-team template v$TEMPLATE_VERSION ($SOURCE_COMMIT)
project: $PROJECT_ROOT

next:
  cd "$PROJECT_ROOT"
  .codex/bin/agent-team-doctor          # verify install + codex schema
  .codex/bin/wake-agent-team "<your goal>"

finish after the workflow reaches READY_TO_FINISH:
  .codex/bin/agent-team-finish                       # report only
  .codex/bin/agent-team-finish --commit "msg" --push # explicit finish

qnote-side learning import after a run:
  tools/codex-agent-team-template/bin/import-agent-learning.py <this-repo> <run-id>
OUT
