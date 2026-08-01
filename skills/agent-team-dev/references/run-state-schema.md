---
tags: [tools, codex-agent-team-template, skills, agent-team-dev, run-state, resume, state-machine, ai]
timestamp: 2026-08-01T00:00:00.000Z
---
# Run State Schema

Every agent-team run owns one directory under the target repository. The
markdown plan is for humans; `state.json` + `events.jsonl` are the durable,
machine-readable state the coordinator resumes from. `.agents/runs/` must be
gitignored (the installer ensures this).

## Directory layout

```text
.agents/runs/<run-id>/
├── state.json          # authoritative run state (schema below)
├── events.jsonl        # append-only event log, one JSON object per line
├── plan.md             # human-readable plan (copy or link of docs/plans entry)
├── tasks/
│   ├── T01.json        # per-task record (schema below)
│   └── T02.json
├── worktrees/
│   ├── integration/    # integration-branch checkout
│   └── T01/            # task worktree, branch agent/<run-id>/T01
├── reviews/
│   ├── wave-1-spec.md
│   └── wave-1-code.md
└── learning-outbox/    # see learning-outbox-template.md
```

`<run-id>` format: `<yyyymmdd>-<slug>` (e.g. `20260801-auth-refresh`).

## state.json

```json
{
  "schema_version": 1,
  "run_id": "20260801-auth-refresh",
  "goal": "one-line goal",
  "base_branch": "main",
  "base_commit": "abc123",
  "integration_branch": "agent/20260801-auth-refresh/integration",
  "phase": "WAVE_MERGING",
  "current_wave": 2,
  "waves": {
    "1": { "status": "merged", "merge_commit": "def456", "gate_failures": 0 }
  },
  "tasks": {
    "T01": { "status": "merged" },
    "T02": { "status": "failed", "attempt": 1, "failure": "write_set_violation" }
  },
  "plan_file": "docs/plans/2026-08-01-auth-refresh.md",
  "finished": false
}
```

Task details (write sets, branches, commits) live in `tasks/<id>.json`;
`state.json.tasks` carries only status/attempt so it stays small.

## Run phases

```text
INIT → SPEC_READY → PLAN_READY
→ WAVE_RUNNING → WAVE_VALIDATING → WAVE_MERGING
→ INTEGRATION_TESTING → REVIEWING → FIXING → RE_REVIEWING
→ (next wave: WAVE_RUNNING …)
→ LEARNING_EXPORT → READY_TO_FINISH → DONE
        ↘ REPLANNING (from any gate failure that triggers replan)
```

The coordinator updates `phase` before starting the phase's work, so a crash
resumes into the phase that was interrupted, never past it.

## tasks/<id>.json

```json
{
  "id": "T01",
  "title": "Implement auth middleware",
  "status": "merged",
  "attempt": 1,
  "wave": 1,
  "parallel_group": "wave-1",
  "depends_on": [],
  "branch": "agent/20260801-auth-refresh/T01",
  "worktree": ".agents/runs/20260801-auth-refresh/worktrees/T01",
  "base_commit": "abc123",
  "commits": ["f398609"],
  "write_set": ["src/auth/**", "tests/auth/**"],
  "read_set": ["src/shared/types.ts"],
  "forbidden_paths": ["package.json", "migrations/**"],
  "env": {
    "TMPDIR": ".agents/tmp/T01",
    "PORT_BASE": "42100",
    "TEST_DB_NAME": "test_20260801_T01"
  },
  "tests": ["npm test -- auth"],
  "verification": "npm test -- auth",
  "stop_rule": "stop before editing outside write_set; report instead",
  "check_result": "pass",
  "review_findings": []
}
```

Task statuses: `pending`, `running`, `blocked`, `completed` (implemented +
check-task passed, not yet merged), `failed`, `superseded` (replaced during
replan), `merged`.

## events.jsonl

Append one line per significant transition. Minimum fields:

```json
{"ts": "2026-08-01T10:00:00+09:00", "event": "task_status", "task": "T01", "from": "running", "to": "completed"}
{"ts": "2026-08-01T10:05:00+09:00", "event": "phase", "from": "WAVE_VALIDATING", "to": "WAVE_MERGING"}
{"ts": "2026-08-01T10:06:00+09:00", "event": "gate_failure", "gate": "check-task", "task": "T02", "reason": "write_set_violation"}
```

Events are for audit and post-mortem; `state.json` alone must be sufficient to
resume.

## Resume rules (coordinator, on wake)

1. Search `.agents/runs/*/state.json` for runs with `"finished": false`.
2. If exactly one active run exists, read `state.json` and the tail of
   `events.jsonl`, then resume from `phase`. Do not re-enter brainstorming or
   re-plan completed waves unless `phase` is `REPLANNING`.
3. If multiple active runs exist, list them and ask the user which to resume
   (interactive) or fail with the list (exec mode).
4. If none exist, start a new run: create the run directory and `state.json`
   with `phase: INIT` before any other work.

## Idempotency rules

Every phase must be re-runnable after a crash without duplicating work:

- Before `git worktree add`, check whether the worktree path is already
  registered (`git worktree list`); reuse it.
- Before creating a branch, check whether it exists (`git rev-parse --verify`);
  reuse it.
- Before cherry-picking a task commit into integration, check whether it is
  already contained (`git merge-base --is-ancestor <commit> <integration>` or
  `git cherry`); skip if present.
- Before re-spawning a task agent, check `tasks/<id>.json` status; never respawn
  a task in `completed`/`merged`.
- Record every commit hash in `tasks/<id>.json` as soon as it is created.
