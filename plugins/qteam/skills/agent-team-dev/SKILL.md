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

For a run declared by `wayfinder` inside an epic, add `--epic <epic-id>`.
Initialization mechanically refuses unknown runs, unfinished predecessors, or
a base commit outside the epic lineage. After safe finish reaches durable
`DONE`, run `.codex/bin/agent-team-artifact epic-complete-run --epic <epic-id>
--run <run-id>` to unlock downstream runs.

Never edit `state.json`, `events.jsonl`, or task status by hand. Use
`agent-team-state`; its locked atomic writes are the durable source of truth.
Use its compact `status` packet for operator updates and resume instead of
dumping the full state.
If an unfinished run has schema version 2, 3, 4, or 5, or schema 6 with legacy
policy-v2/additive/core-layer identity, run `migrate-run` once. Finished or
publication-sealed runs cannot migrate, and active workers/reviews must first be
settled. It preserves durable provenance and historical quality duties,
assigns conservative policy where needed, and
forces every unfinished task through `REPLANNING`/`task-put` so the coordinator
must restore explicit dependencies before execution resumes.

## Intake primitives

- Failure-driven work begins with `qteam-diagnose`.
- Policy-triggered refactor, hardening, and public-surface QA use
  `qteam-harden`. It freezes mechanical evidence and never creates another
  review or orchestration loop.
- A clear destination with an unknown solution frontier begins with
  `qteam-explore`. Its read-only evidence brief may widen candidate paths, but
  it cannot widen approved scope or launch a write loop.
- Unclear new behavior begins with `brainstorming`; use `grilling` only on an
  unresolved high-impact decision.
- Use `grill-with-docs` when ubiquitous language changes.
- Use `wayfinder` only for work larger than one session whose decision route is
  genuinely foggy. It hands decisions to `to-spec`; it does not implement.
- With sufficient context, use `to-spec` without interviewing again.
- A measurable exploration candidate becomes a normal bounded QTeam task with
  a frozen `experiment` object: goal; metric name, direction, command, observed
  baseline or `null`; minimum delta; guard and held-out commands; attempt budget;
  and plateau window. The task establishes or replays its baseline at the frozen
  base commit before modifications, then uses isolated workers and the normal
  mechanical and review gates.

Move state through `SPEC_READY` only after the typed spec passes
`agent-team-artifact lint --kind spec`; move through `PLAN_READY` only after the
typed ticket artifact passes `lint --kind tickets`. The later spec-review packet
replays and freezes spec lint automatically, so a structurally invalid typed
artifact cannot spend an LLM review call.

## Plan and task records

Use `to-tickets` and `parallel_planner` to build vertical slices and a blocking
DAG. Each machine task record includes:

- ID, title, purpose, approved spec excerpt and acceptance behavior
- immutable `depends_on` task IDs, wave, and cross-task contracts
- task branch and exact worktree path
- `write_set`, `read_set`, `forbidden_paths`, and any explicitly serialized
  `allow_shared_surfaces`
- namespaced TMPDIR/ports/database/compose/build resources
- `work_kind`, factual `risk_flags`, and an optional conservative
  `reversibility` declaration (never a hand-selected model); derivation may
  raise but never lower the effective reversibility class
- for `work_kind: experiment`, the complete frozen `experiment` object; workers
  may not change its metric, guards, budget, or stopping rule
- focused and integration cases, exact verification command, stop rule
- for each feature/bugfix, structured `test_seams` with stable ID, public
  behavior, RED-only paths, focused command, and expected failure pattern
- for each feature/bugfix, all twelve `scenario_coverage` dimensions with at
  most one strongest applicable scenario linked to approved seam IDs, or an
  explicit non-applicability rationale
- for each bugfix/debug task, deterministic `diagnosis_command` and
  `failure_pattern`
- exact `required_decisions`; and when continuation is material,
  `handoff_required: true` plus one typed successor/user-decision/replan/
  no-followup handoff
- `quality_commands` for every lane in the deterministically derived
  `required_quality_lanes`; each command proves the lane property on the
  integration surface rather than merely printing a claim

Shared interfaces, schemas, migrations, lock/build/config/generated files,
global fixtures, and snapshots are serial. Tests for a behavior live in its
feature slice, not in a concurrent horizontal "tests" task.

Run `test_designer` before workers and fold its public seam, cases, failure
paths, and acceptance commands into records. Materialize records only with:

```bash
.codex/bin/agent-team-state --run <run-id> task-put --file <task.json>
```

Register tasks in topological order: every `depends_on` target must already be
registered and must be in a strictly earlier wave. `PLAN_READY` revalidates the
complete graph; unknown, self, cyclic, same-wave, and later-wave dependencies
fail closed. Once registered, a task's dependency IDs and wave are frozen;
changing either requires an explicit transition to `REPLANNING` and
`task-put --replace`.

Before a covered operation, record each unresolved human question with
`decision-put`. Open gates block only their exact task/wave/action/global scope;
resolve them with recorded choice and evidence, never by editing state or
assuming silence. Read [interaction-contract.md](references/interaction-contract.md)
when a user decision, task continuation, status update, or public export is in
scope.

`task-put` deterministically derives the immutable policy. Low-risk bounded
work uses `economy` with compact review; judgment-heavy/broad work uses
`standard` with full review; concurrency, security, migration, data-loss, auth,
compatibility, or public-API work uses `deep` + mandatory risk review. Default
profiles are Terra/low, Terra/medium, and Sol/high and can be overridden only
at run `init`; planners and workers do not choose them ad hoc. A wave of four
or more otherwise-economy tasks upgrades its review to standard/full once,
without upgrading each isolated worker.

The same facts derive `lean`, `standard`, or `hardened` workflow shape. Standard
behavior/debug/refactor/integration work adds a refactor gate; hardened work additionally
adds a hardening gate; compatibility/public API adds public-surface QA. A
versioned `.qteam/policy.json` may only raise the shape floor or add lane
triggers. Init freezes the effective project layer and digests in run state.

## Isolation and wave execution

Every writable task uses worktree-per-task and a task branch from the current
integration head, including small serial work. Namespace runtime resources even
with worktrees. There is no shared-tree writer fallback.

For each wave:

1. Transition to `WAVE_RUNNING`; the state manager proves every dependency is
   `merged` or `artifact_complete` and that the selected tasks belong to the
   wave. Task start repeats both checks under the state lock. Also confirm
   write sets/resources do not overlap.
2. Idempotently create/reuse branches and registered worktrees.
3. Launch writable roles with `agent-team-worker spawn`. Give one record and a
   bounded instruction, then `wait`/`status`; do not pass conversation history.
4. Workers stay inside their worktree/write set, run focused verification,
   commit locally, and never push or merge. Feature/bugfix work makes a
   test-only RED commit and minimal GREEN commit per approved seam; the
   coordinator runs `verify-tdd-cycle` with the seam ID and RED/GREEN commits
   to replay the frozen command at both commits. Bugfix/
   debug work also supplies `.qteam-diagnosis.json`; `diagnosis-put` replays
   the frozen RED reproduction, records the ranked causal evidence, and
   consumes the report. Experiment work supplies `.qteam-experiment.json`;
   `experiment-put` independently replays the base-commit metric and the final
   metric, guard, and held-out command before consuming it. Then the
   coordinator runs `verify-task <id>` at exact task HEAD.
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
8. Load `qteam-harden` when the wave policy lists quality lanes. Run
   `quality-check --wave N --lane <lane>` for each one. This replays only the
   frozen, deduplicated commands at exact integration HEAD. A failed/stale lane
   requires an owned task and blocks review; it is not a reason to weaken the
   command or add a per-task reviewer.

All phase and task status changes go through `agent-team-state`.

The coordinator may maintain a durable priority queue with `queue-put`,
`queue-claim`, and `queue-complete`. One claim returns only the highest-priority
pending batch, up to the explicit limit. Queue records improve scheduling and
observability; dependencies, phases, and run state remain authoritative, and
workers never route work directly to peers.

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
bounded JSON verdict. Launch every reviewer through `agent-team-review run`,
which binds the actual read-only Codex process to the packet model, reasoning
profile, provider/family, runner version, digest, session ID, and result path.
The packet also freezes a redacted trajectory summary and axis-specific
calibration canaries. They are transparent consistency checks, not secret or
adversarial benchmarks. Complete the ledger only with the runner receipt; a
judge that fails the canaries cannot attest the change. The gate
rejects two mandatory axes that share an identity or invocation, fake waves,
empty ranges for merged work, and packets that omit a wave merge commit.
If Codex upgrades after packet creation but before any review attempt, recreate
the same packet with `--refresh-runner`. Refresh is rejected once any receipt,
finding, attempt, or completion exists.

The packet-derived intensity controls context, not whether quality review
happens. `compact` reads only the final diff, affected clauses/contracts, and
focused tests; `full` follows necessary callers/error paths once per wave;
`risk` adds the third axis bounded to named risks. Spawn reviewers with the
packet execution tier and run model profiles. Never expand compact/full review
into a repository-wide audit without concrete defect evidence.

Review granularity is wave-level (or once on the final diff for a small serial
change), never per task/file/commit. Re-review only the finding-owned fix diff
and required context. If current HEAD is already covered by both mandatory
ledgers, do not spend tokens on a duplicate final branch review.

Every confirmed finding becomes an owned fresh serial fix task with `finding_ids`
and current integration HEAD as its base; fixers do not resolve their own
findings. If the task would raise the frozen wave policy, enter `REPLANNING`.
Otherwise gate and merge it in `FIXING`, enter `RE_REVIEWING`, and create a
non-empty `scope=fix` packet. Only its fresh reviewer receipt can close the
packet's exact frozen finding set. The wave cannot pass until `agent-team-review check`
reports all required ledgers complete and current.

## Learning and finish

After the last reviewed wave, transition to `LEARNING_EXPORT`. Run final
integration verification there. Then run the distiller in an isolated worker;
it writes only
`.qteam-learning-outbox/` in its task worktree. After success, use
`agent-team-worker harvest` to copy that symlink-free artifact into the run
outbox. Keep reusable, deduplicated, verified, non-sensitive proposals; never
overwrite canonical skills. A harvested eval remains a candidate until the
coordinator records an explicit decision:

```bash
.codex/bin/agent-team-state --run <run-id> learning-item-decision <item-id> \
  --outcome approved --evidence '<bounded coordinator evidence>'
```

Use `--outcome rejected` for unsupported candidates. From the qnote root,
import approved items with
`<target-repo>/.codex/bin/import-agent-learning <target-repo> <run-id>`.
Confirmed corrections, trajectory anomalies, review findings, rollback events,
and tool failures may additionally become typed `eval-cases/*.json`. Each case
must name an agent/dependency/mixed attribution and bind a regular file inside
the same run by exact SHA-256; harvest and import reject invented or stale
evidence.

Before final review, compare approved requirements, design, and tickets with
the implementation. If there is drift, create a proposal shaped like
`references/spec-drift-template.json`. First open every `decision_id` as a
user-owned action decision whose only target is `finish`. Seal the report to
`.agents/runs/<run>/spec-drift.json` at the durable integration head with
`agent-team-artifact drift-seal`; sealing attaches the exact change digest to
each still-open decision. Only then resolve the decisions. Run `drift-check`
after resolution; it passes only when every exact proposal is explicitly
allowed and the run head, report, sources, and decision bindings are still
fresh. Finish mechanically repeats this bound check. The report is always
`proposal-only`; edit approved artifacts only in an owned `REPLANNING` task.
Never silently rewrite approved history. If no drift exists, no report is
required; the mandatory spec review remains the semantic proof.

If any task derives `hard-to-reverse`, obtain the current bound subject only
after the reviewed integration head and task outcomes are stable:

```bash
.codex/bin/agent-team-state --run <run> reversibility-subject
```

Create a user-owned action decision covering `finish` with that exact subject,
then resolve it explicitly. A later head, task outcome, or hard-task policy
change makes the authorization stale and blocks `READY_TO_FINISH`.

Record task/final verification through `verify-task` / `verify-final`; review
status only through `agent-team-review check`; and learning through
`agent-team-state gate learning ... --evidence ...`. Learning may be explicitly
skipped with a reason. All code-bearing gates are bound to the current Git HEAD.
The state machine rejects `READY_TO_FINISH` until these preconditions hold.

Run `agent-team-state --run <run-id> boundary-check` on the exact integration
HEAD after final artifacts are ready. It fails on committed runtime/private
state, recognizable credentials, or user-specific machine paths and records a
head-bound `public_boundary` gate. Do not weaken the scan; remove or privatize
the offending artifact.

Transition to `READY_TO_FINISH`. `agent-team-finish` is report-only by default.
`--integrate` fast-forwards locally; `--push` additionally pushes and is illegal
without `--integrate`. Default branches require `--allow-default-branch`. After
successful integration/push, finish atomically marks the run `DONE` and
`finished: true`.

## Operator surfaces

Run `.codex/bin/agent-team-web --run <run>` (or `./qteam serve <repo> --run
<run>`) for the local Web operator plane. Without `--token-file` it is read-only.
With a mode-0600 token it exposes only fixed CLI actions with bearer + CSRF
protection. It binds numeric loopback only; remote access uses an SSH tunnel or
exact trusted HTTPS proxy with that token. Raw logs are opt-in because they may contain
tool output or secrets.

Herdr is an optional display/session backend only. From inside Herdr, use
`.codex/bin/agent-team-session open --run <run> --mode web|watch`. The adapter
creates a pane and launches QTeam's own surface; it never starts workers,
reviews, worktrees, or state transitions. QTeam does not depend on tmux and
does not fall back to it.

## Failure and progress rules

- Outside-write-set need: stop and mark `blocked`; only coordinator may
  re-scope or supersede the task.
- Three failed fixes on the same causal chain:
  transition to `REPLANNING`; do not stack a fourth speculative change.
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
findings are closed. Required typed handoffs must also have a completed
successor, resolved decision, or explicit no-followup rationale. A run is done
only when all tasks are merged/superseded,
final integration verification passes, required review ledgers close, learning
runs or is explicitly recorded as skipped, the public-boundary check is fresh,
and safe finish changes the state
from `READY_TO_FINISH` to `DONE`.
