# Durable run state (schema version 5)

`.agents/runs/<run-id>/` is runtime state and is gitignored. The coordinator
must mutate it through `agent-team-state`, never by editing JSON.

```text
state.json                 authoritative small run state
events.jsonl               append-only audit events
tasks/<id>.json            full task records
decisions/<id>.json        exact human question, authority, scope, resolution
workers/<id>.json          worker identity/lifecycle
workers/<id>.result.json   durable exit result
reviews/wave-N-<axis>.json immutable packet + finding ledger
reviews/sources/<sha>.source content-addressed frozen review input
reviews/results/*.json     bounded reviewer verdict artifacts
reviews/receipts/*.json    read-only runner launch/result attestations
verifications/*-tdd-*.log  replayed RED/GREEN evidence
verifications/*-diagnosis-red.log replayed failure evidence
worktrees/integration/     integration checkout
worktrees/<id>/            task checkouts
learning-outbox/           reviewed proposals
```

Canonical JSON Schemas are installed under `.codex/schemas/`. State version 5
requires run identity, base/integration refs, a contiguous per-task integration
provenance ledger, phase, compact task status map,
derived wave policies, run model profiles, review-risk state, and `finished`.
Task records require work/risk facts, a derived immutable policy, branch, exact
worktree, non-empty write set, forbidden paths, verification command, and an
immutable `depends_on` list. Each dependency must name an already-registered
task in a strictly earlier wave. `PLAN_READY`, wave start, and task start
revalidate the graph; a predecessor satisfies the execution gate only when its
status is `merged` or `artifact_complete`. Dependency IDs and wave placement
may change only through task replacement during `REPLANNING`.
Feature/bugfix records also require structured test seams; bugfix/debug records
require a frozen diagnosis command and failure pattern. A passing mechanical
gate requires head-bound verification plus all required replayed TDD and
diagnosis evidence.
Feature/bugfix records also carry a deduplicated twelve-dimension scenario
matrix. Tasks may name exact `required_decisions` and a typed handoff; open
decision gates block only their declared task/wave/action/global scope, while
finish rejects unresolved successor, decision, or replan handoffs.
Fresh tasks created during `FIXING` additionally require unique `finding_ids`,
serial execution, current integration HEAD as base, and a policy no stronger
than the frozen wave; otherwise QTeam requires `REPLANNING`.

State also carries `final_verification`, `reviews`, `learning`, and
`public_boundary` gates.
`READY_TO_FINISH` requires the first two `passed` and learning either `passed`
or explicitly `skipped`; the publication boundary must be `passed` at the same
integration HEAD. Every non-pending gate update includes evidence.

Immediately before local integration or publication, finish records a
purpose-tagged seal bound to the exact reviewed SHA and a digest of decisions,
gates, task summaries, and review ledgers. State and review writers recheck
that seal while holding the same run-state lock, preventing a pre-existing
writer from committing between preflight and the external Git operation.

## State machine

```text
INIT → SPEC_READY → PLAN_READY
→ WAVE_RUNNING → WAVE_VALIDATING → WAVE_MERGING
→ INTEGRATION_TESTING → REVIEWING → FIXING → RE_REVIEWING
→ next WAVE_RUNNING or LEARNING_EXPORT
→ READY_TO_FINISH → DONE

Any active gate may enter REPLANNING, which returns only to SPEC_READY or
PLAN_READY.
```

`agent-team-state` validates every phase/task transition, locks `.state.lock`,
persists a write-ahead `.transaction.json`, writes projections through fsync +
atomic rename, appends a transaction-tagged event, then clears the intent.
Every command recovers an interrupted intent before reading or mutating state.
`DONE` is available only through its `finish` command; it requires
`READY_TO_FINISH` and every task `merged` or `superseded`.

Task statuses:

```text
pending → running → completed → merged
   │          ├── blocked → pending/running/failed/superseded
   │          └── failed → pending/superseded
   └── superseded
```

## Resume

Find unfinished state files. With one, read state plus the event tail and
resume its current phase. With multiple, ask the user (or fail unattended).
With none, call `agent-team-state init` before other work.

For an unfinished schema-version-2, schema-version-3, or schema-version-4 run, call
`agent-team-state migrate-run`.
The migration is one locked transaction across all task records and state.
Merged/superseded/artifact tasks retain conservative historical policy;
unfinished tasks are blocked until replaced during `REPLANNING` with explicit
dependencies. The migration does not infer that a legacy missing field means
the task had no prerequisites. It validates task identity, task/state status
projection, and any existing dependency graph before one atomic write, and it
invalidates a stale publication seal from the older schema.
Legacy resolved decisions without a typed `allow`/`deny` outcome are reopened;
QTeam never infers authorization from free-form choice text.

Idempotent resume checks registered worktrees and existing branches before
creation, task status before worker spawn, commit ancestry before cherry-pick,
review packet head SHA before reuse, and worker PID start time/result before
assuming a process still owns a record.

`status` emits the compact operator packet: phase/wave, active and blocked
tasks, dependency-ready tasks, exact dependency blockers, open questions,
blocking handoffs, current integration HEAD, freshness of code-bearing gates,
and one next action. During `WAVE_RUNNING`, that action never starts a task from
a future wave. `show` remains the full debug projection. `boundary-check`
ignores deletions and scans resulting
added/modified text blobs for private runtime paths, recognizable or assigned
credentials, and user-specific local paths; binary blobs fail closed. Its
head-bound report records finding kind/path without copying secret values.

Task verification is executed by `agent-team-state verify-task` inside the
exact task worktree and records command, exit code, log, timestamp, and task
HEAD SHA. Final verification uses `verify-final` in the exact integration
worktree. Review and finish gates revalidate the current integration HEAD;
mandatory axes also require distinct reviewer/session receipts produced by the
packet-bound read-only runner. Every real wave must be reviewed, and the union
of valid wave/fix packets must include each recorded merge commit; risk packets
obey the same range rule. Each integration delta must byte-match the checked
task diff and is recorded in a contiguous ownership chain; finish rejects any
commit or change not owned by exactly one gated task. Merged task check/merge
commits must remain ancestors of the frozen finish SHA.
Arbitrary evidence strings cannot mark those gates passed.
