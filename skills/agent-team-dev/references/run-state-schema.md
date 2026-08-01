# Durable run state (schema version 2)

`.agents/runs/<run-id>/` is runtime state and is gitignored. The coordinator
must mutate it through `agent-team-state`, never by editing JSON.

```text
state.json                 authoritative small run state
events.jsonl               append-only audit events
tasks/<id>.json            full task records
workers/<id>.json          worker identity/lifecycle
workers/<id>.result.json   durable exit result
reviews/wave-N-<axis>.json immutable packet + finding ledger
reviews/sources/<sha>.source content-addressed frozen review input
reviews/results/*.json     bounded reviewer verdict artifacts
worktrees/integration/     integration checkout
worktrees/<id>/            task checkouts
learning-outbox/           reviewed proposals
```

Canonical JSON Schemas are installed under `.codex/schemas/`. State version 2
requires run identity, base/integration refs, phase, compact task status map,
and `finished`. Task records require a branch, exact worktree, non-empty write
set, forbidden paths, and verification command. A passing mechanical gate also
requires `verification_evidence` containing at least one object with a command
and `exit_code: 0`.

State also carries `final_verification`, `reviews`, and `learning` gates.
`READY_TO_FINISH` requires the first two `passed` and learning either `passed`
or explicitly `skipped`; every non-pending gate update includes evidence.

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

Idempotent resume checks registered worktrees and existing branches before
creation, task status before worker spawn, commit ancestry before cherry-pick,
review packet head SHA before reuse, and worker PID start time/result before
assuming a process still owns a record.

Task verification is executed by `agent-team-state verify-task` inside the
exact task worktree and records command, exit code, log, timestamp, and task
HEAD SHA. Final verification uses `verify-final` in the exact integration
worktree. Review and finish gates revalidate the current integration HEAD;
mandatory axes also require distinct reviewer/session attestations, and merged
task check/merge commits must remain ancestors of the frozen finish SHA.
Arbitrary evidence strings cannot mark those gates passed.
