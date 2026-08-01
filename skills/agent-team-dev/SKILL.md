---
name: agent-team-dev
description: "Use for complex software development with Codex subagents: brainstorm, write an implementation plan, execute task-by-task in isolated worktrees, run mechanical and review gates, and export learning."
tags: [tools, codex-agent-team-template, skills, agent-team-dev, skill, agent, team, development, workflow, ai]
timestamp: 2026-08-01T00:00:00.000Z
---
# Agent Team Development Workflow

Use this skill when the user wants a feature, refactor, bug fix, migration, or
multi-step coding task done through a disciplined agent team.

**This file is the single normative source for the workflow.** The README
explains the architecture, the wake prompt only invokes this skill, and agent
TOMLs only describe role differences. When any of them disagree with this file,
this file wins.

**Orchestration authority:** this skill overrides the per-task review cadence of
`subagent-driven-development` when running parallel waves. Reviews run at wave
gates as defined here, not per task.

## Operating model

The main Codex session is the coordinator. Keep requirements, decisions, task
status, and final judgment in the main thread. Spawn subagents only for bounded
work. The coordinator does not edit business code; it owns orchestration,
integration (cherry-picks/merges), gates, and commits.

Process skills as backbone: `brainstorming` for unclear requirements,
`writing-plans` for the plan, `verification-before-completion` before declaring
done. Custom agents as roles:

- `researcher`: read-only evidence gathering.
- `architect`: read-only design and migration strategy.
- `parallel_planner`: read-only DAG/wave planner for safe parallel execution.
- `developer`: implementation of one scoped task in its own worktree; owns the
  task's focused tests; commits locally to its task branch only.
- `debugger` / `frontend_debugger` / `system_debugger`: failure reproduction and
  root-cause analysis for general / frontend / system domains.
- `tester`: two modes — read-only test designer before a wave; serial gap-fill
  test writer after a wave merges. Never writes concurrently with developers.
  (Its sandbox stays workspace-write for gap-fill mode, so designer-mode
  read-only-ness is enforced by instruction and the coordinator's timing, not
  by the sandbox.)
- `integration_tester`: serial, post-merge only; cross-module/API/DB/service/
  CLI/IPC/e2e coverage on the integration branch.
- `spec_reviewer`: spec/plan compliance review (read-only).
- `code_reviewer`: correctness/architecture/security review (read-only).
- `knowledge_distiller`: post-verification distillation into the run's
  learning outbox (writes only under `.agents/runs/<run-id>/learning-outbox/`).

## Run state and resume (do this first)

Every run is recorded under `.agents/runs/<run-id>/` as defined in
`references/run-state-schema.md`. On wake, **before anything else**:

1. Search `.agents/runs/*/state.json` for an active run (`finished: false`).
2. If one exists, resume from its `phase`. Do not restart brainstorming, do not
   re-plan completed waves, do not redo merged tasks.
3. If none exists, create the run directory and `state.json` (`phase: INIT`)
   before starting work.

Update `state.json` at every phase transition and task status change, and
append to `events.jsonl`. All phases must be idempotent per the schema's
idempotency rules — a restarted coordinator must never duplicate worktrees,
cherry-picks, or completed tasks.

Keep raw diffs and logs out of the main thread: use diff stats, gate script
output, and bounded digests; read full diffs only for targeted inspection.

## Isolation model

**Parallel waves always use worktree-per-task. A shared tree is allowed only
for strictly serial execution** (small tasks: one developer at a time).

Non-overlapping source write sets are not isolated execution: concurrent tasks
can still collide on generated files, formatters, shared test databases, ports,
`dist/`/`target/`/cache directories, package-manager locks, and on testing
against another task's half-written code. Therefore:

- Each parallel task gets its own worktree at
  `.agents/runs/<run-id>/worktrees/<task-id>` on branch
  `agent/<run-id>/<task-id>`, forked from the integration branch head.
- Each task gets namespaced runtime resources, assigned in the plan:
  `TMPDIR=.agents/tmp/<task-id>`, a port range, a per-task test DB name,
  compose project name, and worktree-local build dirs when applicable.
- Developers run only task-scoped verification inside their worktree.
  Repo-wide builds, full suites, integration tests, shared-DB tests, and real
  service tests run serially on the integration branch after merging.
- The integration branch `agent/<run-id>/integration` is created from
  `base_commit` and checked out in `worktrees/integration`. The user's own
  checkout is left untouched until finish.

**Commit rules (replaces "implementers never commit"):** a developer commits
locally to its own task branch — that is how work is handed to the coordinator.
A developer must never push, never merge, never touch the integration branch or
any other task's branch, and never commit on the user's branch. The coordinator
owns integration: after mechanical validation it cherry-picks task commits into
the integration branch in dependency order. Only the finish gate moves anything
to the user's branch or a remote.

## Default workflow

### 1. Intake and brainstorm

If resuming an active run, skip to its phase. If the user supplied an approved
spec and plan, record them in the run directory and skip to planning/execution.
Otherwise:

- Clarify the real goal, constraints, success criteria, and non-goals.
- Spawn `researcher` when repository context or existing behavior is unknown.
- Spawn `architect` when design choices are non-trivial.
- Spawn the appropriate debugger when the task starts from a failure.
- Produce a concise design/spec; set `phase: SPEC_READY`.

### 2. Write the plan

Create or update a plan file when the task is more than a trivial one-file fix.

For any plan with more than one implementation task, spawn `parallel_planner`
before execution. The planner must convert the plan into a dependency DAG and
safe parallel waves. See `references/parallel-plan-template.md`.

Each task must include:

- Task ID.
- Title and purpose.
- Files or modules likely involved.
- Behavior change.
- `depends_on`.
- `write_set`.
- `read_set`.
- `forbidden_paths`.
- `conflicts_with`.
- `parallel_group`.
- Task branch (`agent/<run-id>/<task-id>`) and worktree path.
- Resource namespaces (TMPDIR, port range, test DB name, compose project,
  build dir) when the task touches runtime resources.
- Contract with other tasks.
- Required focused tests.
- Required integration tests or justification.
- Verification command.
- Stop rule for edits outside `write_set`.
- Review gate.

Keep tasks small enough to review independently.

Parallel planning rules:

- Only tasks in the same `parallel_group` may run concurrently.
- A task may start only after all `depends_on` tasks are `merged`.
- Parallel tasks always run in separate worktrees on separate task branches;
  two write-heavy agents must not have overlapping `write_set` paths even so.
- Shared interfaces, schemas, migrations, generated files, package/build/config
  files, global fixtures, snapshots, and shared test infrastructure are serial
  by default.
- Tests belong to the feature task (`src/auth/**` + `tests/auth/**`); never
  schedule a concurrent second writer for a task's tests.
- Any agent that needs to edit outside its `write_set` must stop and report.
- The coordinator owns integration; review runs at wave gates on the
  integration diff, not after each individual parallel task.
- Integration tests run serially at wave merge gates when behavior crosses
  real boundaries.

Before execution, materialize each task into `tasks/<id>.json` with the fields
above. Spawn `tester` in read-only test-designer mode to state required
behaviors, failure paths, regression risks, and acceptance commands per task;
fold its output into the task records. Set `phase: PLAN_READY`.

### 3. Execute wave-by-wave

For every wave:

1. Set `phase: WAVE_RUNNING`. Confirm every task in the wave has all
   `depends_on` tasks `merged` and write sets do not overlap.
2. Create (or reuse, on resume) each task's branch and worktree from the
   integration head; record them in `tasks/<id>.json`.
3. Spawn one `developer` per implementation task in the wave; give each
   developer exactly one task and only its task record fields: task ID, spec
   excerpt, worktree path and branch, `write_set`, `read_set`,
   `forbidden_paths`, resource namespaces, stop rule, and verification command.
4. If a task is failure-driven, spawn the appropriate debugger before
   implementation and require a root-cause summary.
5. Wait for every developer in the wave; each hands off local commits on its
   task branch plus a bounded digest.
6. Set `phase: WAVE_VALIDATING`. Per task, run
   `.codex/bin/agent-team-check-task`; it must pass before the task may merge.
   It checks: changed files within `write_set` (rename/delete aware), no
   `forbidden_paths` touched, no undeclared generated/shared surfaces, and a
   clean task worktree. A failing task goes to `blocked` or `failed` and does
   not enter the merge.
7. Set `phase: WAVE_MERGING`. The coordinator cherry-picks each passing task's
   commits into the integration branch in dependency order, resolving
   conflicts itself (the only code the coordinator touches). Record the merge
   commit and set the task `merged`.
8. Set `phase: INTEGRATION_TESTING`. Serially on the integration branch: run
   the wave's focused tests together; spawn `tester` in gap-fill mode if the
   wave left focused-test gaps; spawn `integration_tester` (serial writer, own
   write set) when behavior crosses module, API, DB, service, CLI, IPC,
   frontend-backend, or e2e boundaries.
9. Set `phase: REVIEWING`. Spawn `spec_reviewer` and `code_reviewer` in
   parallel on the integration diff for the wave.
10. Set `phase: FIXING`. Fix every confirmed review issue before moving on —
    task-local findings go back to the original task's developer in a fresh
    fix worktree; cross-cutting findings become new fix tasks. Each fix passes
    check-task and is cherry-picked like any task.
11. Add or update focused and integration tests for every confirmed review
    issue as applicable.
12. Set `phase: RE_REVIEWING`. Re-review the fix diff until findings close
    (progress rule below).
13. Run the relevant verification commands on the integration branch.
14. Collect concise task/wave session digests from implementation, testing,
    debugging, and review agents.
15. Run the learning gate: spawn `knowledge_distiller` to write outbox
    proposals; review and mark them approved/rejected; save only reusable,
    deduplicated, non-sensitive items and skip noisy or one-off items.
16. Update `state.json` and the plan status: wave completion, check-task
    results, write-set violations or conflict resolutions, review gates, and
    learning gate result.

Do not start the next wave until the current wave has passed its mechanical
validation, merge gate, review gate, and learning gate.

### 4. Finish

After the last wave: final verification on the integration branch, a final
learning export (`phase: LEARNING_EXPORT`), then `phase: READY_TO_FINISH`.
Hand off to the user: `agent-team-finish` is report-only by default; moving
commits to the user's branch requires `--commit`, pushing requires `--push`,
and the default branch additionally requires `--allow-default-branch`.

## Failure and replan rules

- **Stop rule fired** (task needs to edit outside its `write_set`): the task
  goes to `blocked`. The developer must not widen its own write set. The
  coordinator re-scopes (adjust write set or split the task), marks replaced
  tasks `superseded`, updates the plan, then resumes.
- **Same gate fails twice on the same cause**: set `phase: REPLANNING` and
  return to `writing-plans` / `parallel_planner` with the failure evidence. Do
  not brute-force a third attempt.
- **Review findings must close, with progress**: after a fix, re-review the fix
  diff. There is no fixed retry cap, but if the same finding recurs without
  progress, escalate to an `architect`/`debugger` investigation or replan
  instead of iterating.
- **Finding ownership**: task-local → the original developer in a fresh fix
  worktree; cross-cutting → a new fix task with its own record; the coordinator
  fixes only integration conflicts, never business logic.
- Repeated stop-rule hits across tasks mean the decomposition is wrong:
  re-run `parallel_planner` with the new evidence.

## Token discipline

Coordinator fan-out / digest fan-in. Filesystem run state is the source of
truth; pass only bounded context.

- Do not spawn an agent unless its output can change a decision or reduce risk.
- Do not pass conversation history to subagents; pass the task record fields.
- Require bounded digests (see `references/session-digest-template.md`), never
  narrative transcripts.
- No peer-to-peer agent chat; agents report to the coordinator.
- Reviewers start from the plan, wave diff, and task digests; expand only when
  risk requires it.
- Skip `parallel_planner`, run infrastructure beyond a minimal `state.json`,
  and multi-agent waves for small tasks.
- Prefer 2–4 concurrent developers per wave; more requires justification.

Suggested scale — small: coordinator + one developer (serial, shared tree
allowed) + one reviewer. Medium: researcher/architect as needed + developer +
tester + both reviewers. Large: parallel planner + 2–4 developers in worktrees
+ integration tester + reviewers + knowledge distiller.

## Learning gate

The sandbox is scoped to the target repository, so no agent can write to qnote
directly. Learning capture is a two-step outbox
(see `references/learning-outbox-template.md`):

1. **Inside the run**: after review fixes and verification pass,
   `knowledge_distiller` writes proposals to
   `.agents/runs/<run-id>/learning-outbox/` (manifest.json, knowledge.md,
   lessons.md, skill-proposals/, evidence/). The coordinator reviews proposals,
   marks each approved/rejected in the manifest, and records the outcome in
   `state.json`.
2. **Outside the run**: import approved items into qnote with
   `tools/codex-agent-team-template/bin/import-agent-learning.py` (run from
   qnote) or manually. The importer verifies evidence, dedupes, and never
   overwrites a canonical skill — skill changes land as proposals.

Categories: `knowledge` (stable project/domain facts), `lessons` (mistakes,
root causes, anti-patterns), `skills` (repeatable procedures with trigger,
prerequisites, steps, validation, failure modes). Distill only verified work;
never capture unverified assumptions, private reasoning, noisy logs, secrets,
or one-off trivia; prefer dedupe/update over duplicates. Do not let learning
capture block urgent fixes; capture a minimal lesson and mark follow-up.

## Hard rules

- Do not insert workaround code.
- Do not add fallback behavior unless the approved spec explicitly requires it.
- Do not defer review findings.
- Do not mark a task complete without tests or a written justification.
- Do not weaken tests to make them pass.
- Do not replace integration coverage with mocks when the work is about a real
  boundary.
- Parallel writers require isolated worktrees; a shared tree is serial-only.
- No merge before `agent-team-check-task` passes for that task.
- Developers commit only to their own task branch; never push, never merge,
  never touch the integration or user branch.
- The coordinator owns integration, gates, and finish; it does not edit
  business code.
- Debugger agents never ship speculative fixes; require reproduction, root
  cause, and regression coverage or explicit verification.
- Update `state.json` and `events.jsonl` at every transition; all phases must
  be idempotent under restart.
- Never finish or push without the explicit finish flags.

## Done criteria

A task is done only when: implementation matches the approved spec and plan;
`agent-team-check-task` passed; its commits are merged to the integration
branch; spec and code review have no unresolved required findings; failure-
driven work has documented reproduction and root cause; required focused tests
exist; integration coverage exists or is explicitly ruled out with
justification; and `tasks/<id>.json` is `merged`.

The run is done only when all tasks are `merged` or explicitly `superseded`,
final verification passed on the integration branch, the learning gate ran (or
was explicitly skipped), and `state.json` is `READY_TO_FINISH` with `finished`
set true after the finish gate.
