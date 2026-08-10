# Decision, handoff, and publication contract

Use this contract whenever execution needs human authority, one task produces
work for another consumer, or a run exports durable/public artifacts.

## Scoped decisions

Never represent a user dependency as vague prose such as "waiting on owner".
Create a decision gate with one concrete question, one authority, and the
smallest exact scope:

```json
{
  "schema_version": 1,
  "id": "D01",
  "status": "open",
  "question": "May the migration delete legacy rows after the backup check?",
  "authority": "user",
  "scope": {"kind": "task", "targets": ["T03"]}
}
```

Scopes are `task`, `wave`, `action`, or explicitly `global`. Action targets are
`task-start`, `wave-start`, `merge`, `learning-export`, `finish`, or `publish`.
Use `decision-put`, then `decision-resolve --outcome allow|deny --choice ...
--evidence ...` or
`decision-supersede --reason ...`. An open gate blocks only the covered
operation plus tasks that explicitly list it in `required_decisions`. A
resolved `deny` remains a blocker; only `allow` authorizes the covered action.
Before an external action that is not itself a state transition, run
`decision-check --action <action>`. `agent-team-finish --integrate` seals the
reviewed head, decisions, READY gates, task summaries, and frozen review
ledgers before any Git mutation. Its `--push` form additionally performs the
scoped `publish` decision check. Both seals bind the expected reviewed SHA and
use the run-state lock, so an already-started gate or review write cannot race
the fast-forward or push. Once sealed, only read/status checks and finish may
run.

The exact command forms are:

```bash
.codex/bin/agent-team-state --run <run> decision-put --file <decision.json>
.codex/bin/agent-team-state --run <run> decision-resolve <decision-id> \
  --outcome <allow|deny> --choice '<choice>' --evidence '<evidence>'
.codex/bin/agent-team-state --run <run> decision-supersede <decision-id> \
  --reason '<reason>'
.codex/bin/agent-team-state --run <run> decision-check --action <action>
.codex/bin/agent-team-state --run <run> decision-check --action publish --seal \
  --expected-head <reviewed-sha>
```

## Typed handoffs

Set `handoff_required: true` only when a task's result needs an explicit
continuation. Its `handoff` is exactly one of:

- `successor`: names `target_task` in a strictly later wave; source start
  requires that target still be pending/blocked. Finish waits for delivered
  successor output (`merged` or `artifact_complete`). A same/earlier-wave or
  already-delivered target is directionally invalid, and superseding the
  successor does not satisfy delivery.
- `user-decision`: names a gate that consumes the completed task's result;
  finish waits for that gate to resolve or be superseded. Do not also put a
  post-task gate in `required_decisions`, which would incorrectly block start.
- `replan`: records why the coordinator must enter `REPLANNING`; it cannot
  merge and remains blocking until `handoff-close` records an auditable reason
  and supersedes the source result.
- `no-followup`: records why no successor is correct and closes immediately.

Do not clear a handoff by deleting it or by writing an untyped note.

Use these exact task-record shapes:

```json
{"handoff_required": true, "handoff": {"kind": "successor", "target_task": "T02", "rationale": "T02 consumes the generated contract"}}
```

```json
{"handoff_required": true, "handoff": {"kind": "user-decision", "decision_id": "D-PUBLISH", "rationale": "the completed report is the decision input"}}
```

```json
{"handoff_required": true, "handoff": {"kind": "replan", "rationale": "the discovered API cannot satisfy the frozen contract"}}
```

```json
{"handoff_required": true, "handoff": {"kind": "no-followup", "rationale": "the report is the final approved artifact"}}
```

Register successor tasks and user-decision gates before starting the source
task. A gate used by `user-decision` may be open early, but `decision-resolve`
mechanically rejects it until every source task naming that gate is completed,
merged, or artifact-complete. Its scope must not be global, the source task or
wave, or `task-start`/`wave-start`, because those scopes self-deadlock the
producer. If a source is superseded before delivery, its still-open post-task
decision is superseded automatically.

A completed replan result closes only through:

```bash
.codex/bin/agent-team-state --run <run> phase REPLANNING \
  --reason '<contract discovery>'
.codex/bin/agent-team-state --run <run> handoff-close <task> \
  --reason '<why this result must not merge>' [--replacement <task>]
```

## Machine lifecycle

Use the state manager for every lifecycle transition. A feature/bugfix test
seam uses the exact keys shown here:

```json
{"id": "S01", "behavior": "caller-visible behavior", "test_paths": ["tests/exact_test.py"], "command": "pytest -q tests/exact_test.py", "red_pattern": "expected failure text"}
```

The lifecycle command forms are:

```bash
.codex/bin/agent-team-state --run <run> migrate-run
.codex/bin/agent-team-state --run <run> phase SPEC_READY
.codex/bin/agent-team-state --run <run> phase PLAN_READY
.codex/bin/agent-team-state --run <run> phase WAVE_RUNNING --wave <N>
.codex/bin/agent-team-state --run <run> task-status <task> running
.codex/bin/agent-team-state --run <run> verify-task <task>
.codex/bin/agent-team-state --run <run> phase WAVE_VALIDATING --wave <N>
.codex/bin/agent-team-check-task --run <run> --task <task>
.codex/bin/agent-team-state --run <run> phase WAVE_MERGING --wave <N>
.codex/bin/agent-team-state --run <run> task-status <task> merged \
  --commit <integration-sha>
.codex/bin/agent-team-state --run <run> phase INTEGRATION_TESTING --wave <N>
.codex/bin/agent-team-state --run <run> phase REVIEWING --wave <N>
# Create/run/complete/check the required review packets here.
.codex/bin/agent-team-state --run <run> phase LEARNING_EXPORT
.codex/bin/agent-team-state --run <run> verify-final --command '<final-command>'
.codex/bin/agent-team-state --run <run> gate learning <passed|skipped> \
  --evidence '<artifact-or-reason>'
.codex/bin/agent-team-state --run <run> boundary-check
.codex/bin/agent-team-state --run <run> phase READY_TO_FINISH
```

`migrate-run` is only for an unfinished schema-version-2/3/4/5 run; current
schema-version-6 runs do not need it. Every unfinished migrated task must be
replanned with explicit dependencies before execution resumes.

`agent-team-check-task` records a passing task as `completed`; do not duplicate
that transition. Review findings may insert `FIXING`/`RE_REVIEWING`, and a
material contract change inserts `REPLANNING`, as defined by the normative
workflow.

For each required axis, create one frozen wave packet, launch its read-only
reviewer, complete from the returned receipt path, then check the combined
gate. `risk` uses `--standards-source` and is added only when derived policy
requires it:

```bash
.codex/bin/agent-team-review --run <run> create --wave <N> --axis spec \
  --base <base-sha> --head <head-sha> --spec-source <spec-path>
.codex/bin/agent-team-review run --ledger <spec-ledger> \
  --reviewer spec-reviewer --session-id <unique-session>
.codex/bin/agent-team-review complete --ledger <spec-ledger> \
  --receipt <receipt-path>

.codex/bin/agent-team-review --run <run> create --wave <N> --axis standards \
  --base <base-sha> --head <head-sha> --standards-source <standards-path>
.codex/bin/agent-team-review run --ledger <standards-ledger> \
  --reviewer standards-reviewer --session-id <different-session>
.codex/bin/agent-team-review complete --ledger <standards-ledger> \
  --receipt <receipt-path>

# Only when derived policy requires risk:
.codex/bin/agent-team-review --run <run> create --wave <N> --axis risk \
  --base <base-sha> --head <head-sha> --standards-source <risk-context-path>
.codex/bin/agent-team-review run --ledger <risk-ledger> \
  --reviewer risk-reviewer --session-id <third-session>
.codex/bin/agent-team-review complete --ledger <risk-ledger> \
  --receipt <receipt-path>

.codex/bin/agent-team-review --run <run> check --wave <N> --head <head-sha>
```

Finish discovers the one active `READY_TO_FINISH` run; it does not take a
`--run` argument. Use exactly one of these forms:

```bash
.codex/bin/agent-team-finish
.codex/bin/agent-team-finish --integrate [--allow-default-branch] [--yes]
.codex/bin/agent-team-finish --integrate --push \
  [--allow-default-branch] [--yes]
```

The first form is report-only. `--integrate` atomically seals authorization,
then fast-forwards the base branch to that frozen reviewed SHA. `--push` is
illegal without it and adds the scoped publish authorization. On `main`,
`master`, or `trunk`, include `--allow-default-branch`.

## Compact operator packet

Use `agent-team-state --run <run> status` for updates and resume. It reports
phase, current wave, active/blocked/failed/pending tasks, exact open questions,
dependency-ready tasks, exact dependency blockers, blocking handoffs,
head-bound evidence freshness, and one deterministic next action. Use `show`
only when the full state is needed.

## Public/private and claim boundary

Runtime state, worker results, machine-specific paths, credentials, raw logs,
and private evidence stay outside committed/public artifacts. Before finish,
run `boundary-check` against the exact integration HEAD. It ignores deletions,
then scans every resulting added/modified text blob for private runtime paths,
quoted or unquoted credential assignments, recognizable keys, and
user-specific local paths. Unscannable binary blobs fail closed. The gate
records only finding kind/path and binds the result to HEAD.

Digests and published learning must state `Validation scope` and `Claim
boundary`: what commands/evidence were checked and what the evidence does not
establish. A writable worker is marked failed unless stdout contains exactly
one non-empty bounded `Validation scope:` line and one `Claim boundary:` line;
successful worker results persist both fields. Never publish raw private
reasoning or evidence merely to justify a claim.
