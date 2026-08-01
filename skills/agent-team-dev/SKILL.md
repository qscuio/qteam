---
name: agent-team-dev
description: Execute approved software work through QTeam's isolated workers, durable state machine, mechanical gates, independent reviews, and safe finish.
---

# QTeam Development Workflow

This is the single normative orchestration source. QTeam is the only
orchestration authority. Superpowers and Matt-derived skills are bounded
primitives; their independent implement/review/finish loops do not run here.
`goal-execution-discipline` is the standing scope, evidence, mandatory dual-
review, and completion contract; it constrains this workflow but does not
orchestrate it.

## Authority and role boundary

The main session is coordinator: it owns requirements, decisions, state,
integration, gates, and final judgment. It does not write business code.

Native subagents are read-only and must always be spawned with
`fork_turns="none"` and a bounded packet:

- `researcher`, `architect`, `parallel_planner`, `test_designer`
- `spec_reviewer`, `standards_reviewer`, optional `risk_reviewer`

Native subagents have no per-agent cwd boundary. Therefore every writable role
must run through `.codex/bin/agent-team-worker`, which starts an independent
`codex exec -C <task-worktree> --sandbox workspace-write` process:

- `developer`, domain debugger roles, `test-writer`, `integration-tester`
- `fixer`, `knowledge-distiller`

Never spawn a writable native subagent and merely ask it to change directory.

## Wake and route

Use `qteam-router`. Before design or code, find unfinished run states. Resume
exactly one active run from its phase; do not brainstorm or redo merged work.
Multiple active runs require user selection (or fail in unattended mode).

For a new non-trivial run:

```bash
.codex/bin/agent-team-state --run <run-id> init --goal '<goal>' \
  --plan-file docs/plans/<plan>.md
```

Never edit `state.json`, `events.jsonl`, or task status by hand. Use
`agent-team-state`; its locked atomic writes are the durable source of truth.

## Intake primitives

- Failure-driven work begins with `qteam-diagnose`.
- Unclear new behavior begins with `brainstorming`; use `grilling` only on an
  unresolved high-impact decision.
- Use `grill-with-docs` when ubiquitous language changes.
- Use `wayfinder` only for work larger than one session whose decision route is
  genuinely foggy. It hands decisions to `to-spec`; it does not implement.
- With sufficient context, use `to-spec` without interviewing again.

Move state through `SPEC_READY` and `PLAN_READY` only after those artifacts
exist.

## Plan and task records

Use `to-tickets` and `parallel_planner` to build vertical slices and a blocking
DAG. Each machine task record includes:

- ID, title, purpose, approved spec excerpt and acceptance behavior
- dependency DAG, wave, and cross-task contracts
- task branch and exact worktree path
- `write_set`, `read_set`, `forbidden_paths`, and any explicitly serialized
  `allow_shared_surfaces`
- namespaced TMPDIR/ports/database/compose/build resources
- focused and integration cases, exact verification command, stop rule

Shared interfaces, schemas, migrations, lock/build/config/generated files,
global fixtures, and snapshots are serial. Tests for a behavior live in its
feature slice, not in a concurrent horizontal "tests" task.

Run `test_designer` before workers and fold its public seam, cases, failure
paths, and acceptance commands into records. Materialize records only with:

```bash
.codex/bin/agent-team-state --run <run-id> task-put --file <task.json>
```

## Isolation and wave execution

Every writable task uses worktree-per-task and a task branch from the current
integration head, including small serial work. Namespace runtime resources even
with worktrees. There is no shared-tree writer fallback.

For each wave:

1. Transition to `WAVE_RUNNING`; confirm dependencies are merged and write
   sets/resources do not overlap.
2. Idempotently create/reuse branches and registered worktrees.
3. Launch writable roles with `agent-team-worker spawn`. Give one record and a
   bounded instruction, then `wait`/`status`; do not pass conversation history.
4. Workers stay inside their worktree/write set, run focused verification,
   commit locally, and never push or merge. The coordinator records structured
   evidence by running `agent-team-state verify-task <id>` at the exact task HEAD.
5. Transition to `WAVE_VALIDATING`. For every task run
   `agent-team-check-task --run ... --task ...`. It rejects empty diffs,
   forbidden/out-of-write-set changes, undeclared shared surfaces, dirty
   worktrees, and missing successful verification evidence.
6. A passing check atomically marks the task `completed`. Transition to
   `WAVE_MERGING` and
   cherry-pick its commits into integration in dependency order. Mark it
   `merged`. The state gate records either ancestry or Git patch-equivalence.
   If conflict resolution changes the patch, create and mechanically gate a
   dedicated integration-fix task; do not hide the edit inside the merge.
7. Transition to `INTEGRATION_TESTING`. On integration, serially run combined
   focused tests, then `test-writer` for missing focused/regression coverage and
   `integration-tester` for real cross-boundary behavior. These are isolated
   worker tasks too; do not let them write the live integration worktree.

All phase and task status changes go through `agent-team-state`.

## Review gate

Transition to `REVIEWING`. For each axis create an immutable packet:

```bash
.codex/bin/agent-team-review --run <run-id> create --wave N --axis spec \
  --base <base-sha> --head <head-sha> --spec-source <path>
```

Run `spec_reviewer` and `standards_reviewer` independently in fresh bounded
contexts. Run `risk_reviewer` only for concurrency, security, migration,
compatibility, data loss, authorization, authentication, or public API risk.
Reviewers use `qteam-review` and the JSON finding ledgers. Each returns a
bounded JSON verdict; complete its ledger with the reviewer identity, distinct
invocation/session id, and result path. The gate rejects two mandatory axes
that share an identity or invocation.

Review granularity is wave-level (or once on the final diff for a small serial
change), never per task/file/commit. Re-review only the finding-owned fix diff
and required context. If current HEAD is already covered by both mandatory
ledgers, do not spend tokens on a duplicate final branch review.

Every confirmed finding becomes an owned fresh fix task; fixers do not resolve
their own findings. Transition through `FIXING` and `RE_REVIEWING`, add
regression coverage, re-review at the new fixed head, and close or invalidate
findings with evidence. The wave cannot pass until `agent-team-review check`
reports all required ledgers complete and current.

## Learning and finish

After the last reviewed wave and final integration verification, transition to
`LEARNING_EXPORT`. Run the distiller in an isolated worker; it writes only
`.qteam-learning-outbox/` in its task worktree. After success, use
`agent-team-worker harvest` to copy that symlink-free artifact into the run
outbox. Keep reusable, deduplicated, verified, non-sensitive proposals; never
overwrite canonical skills.

Record task/final verification through `verify-task` / `verify-final`; review
status only through `agent-team-review check`; and learning through
`agent-team-state gate learning ... --evidence ...`. Learning may be explicitly
skipped with a reason. All code-bearing gates are bound to the current Git HEAD.
The state machine rejects `READY_TO_FINISH` until these preconditions hold.

Transition to `READY_TO_FINISH`. `agent-team-finish` is report-only by default.
`--commit` integrates locally; `--push` additionally pushes and is illegal
without `--commit`. Default branches require `--allow-default-branch`. After
successful integration/push, finish atomically marks the run `DONE` and
`finished: true`.

## Failure and progress rules

- Outside-write-set need: stop and mark `blocked`; only coordinator may
  re-scope or supersede the task.
- Same gate/cause twice: transition to `REPLANNING`; do not brute-force a third
  attempt.
- Same review finding without progress: escalate to architect/debugger or
  replan. There is no arbitrary retry cap.
- Worker lost/failed: preserve logs/result, mark failed, and diagnose; do not
  silently fall back to a native writer or shared tree.
- Resume idempotently: reuse worktrees/branches, never respawn completed or
  merged tasks, and skip commits already contained in integration.

## Hard rules and done

No workaround, silent fallback, speculative fix, weakened test, undeclared
write, shared resource, direct state JSON edit, merge-before-gate, self-review,
or push without explicit finish flags.

A task is done only when its declared behavior and tests are committed, its
structured verification and mechanical gate pass, commits are merged, and its
findings are closed. A run is done only when all tasks are merged/superseded,
final integration verification passes, required review ledgers close, learning
runs or is explicitly recorded as skipped, and safe finish changes the state
from `READY_TO_FINISH` to `DONE`.
