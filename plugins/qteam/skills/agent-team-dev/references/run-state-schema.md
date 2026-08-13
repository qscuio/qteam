# Durable run state (schema version 6)

`.agents/runs/<run-id>/` is gitignored runtime state. Only the installed
`agent-team-*` commands may mutate it; hand-edited JSON and replayed WAL intents
are untrusted input.

```text
state.json                  compact authoritative run projection
events.jsonl                append-only transaction/event audit log
tasks/<id>.json             immutable facts, derived policy, evidence
decisions/<id>.json         scoped human authorization and resolution
workers/<id>.json           isolated process identity/lifecycle
reviews/wave-N-<axis>.json  frozen packet + finding ledger
verifications/              stdout/stderr evidence and SHA-256 receipts
worktrees/integration/      authoritative integration checkout
worktrees/<id>/             isolated task checkout
learning-outbox/            coordinator-decided proposals
```

State v6 freezes:

- repository/run/base/integration identity and contiguous merge provenance;
- the versioned core policy digest plus optional `.qteam/policy.json` layer;
- compact task summaries, wave policy, reversibility/risk, model profiles;
- conditional `refactor`, `hardening`, and `public-surface-qa` quality lanes;
- a coordinator-owned bounded priority queue;
- scoped decision, review, final verification, learning, and public-boundary
  gates.

Every task freezes its dependency list, exact write set, work/risk facts,
workflow shape, model/review tier, TDD/debug requirements, conditional quality
commands, and typed handoff. Dependencies must be earlier-wave tasks and become
ready only at `merged` or `artifact_complete`. A replacement may change this
contract only during `REPLANNING`.

Quality lanes are wave-level and token-bounded. Each command runs in a detached
checkout at the exact integration SHA. Every attempt retains command, exit code,
stdout/stderr paths and hashes. A refactor lane additionally needs one current-
head `quality-assess`: either a bounded `not-needed` rationale or an integrated
same-wave refactor task. Task, TDD, diagnosis, experiment, and final verification
likewise retain both output streams and bind current receipts by digest; legacy
combined-stream receipts remain readable.

The coordinator queue has at most 256 items. Only consumer `coordinator` may
claim the deterministic highest-priority batch and complete it. Queue scheduling
never bypasses dependency, task, review, or finish gates.

## State machine

```text
INIT → SPEC_READY → PLAN_READY
→ WAVE_RUNNING → WAVE_VALIDATING → WAVE_MERGING
→ INTEGRATION_TESTING → REVIEWING → FIXING → RE_REVIEWING
→ next WAVE_RUNNING or LEARNING_EXPORT
→ READY_TO_FINISH → DONE

Any active phase may enter REPLANNING, which returns only to SPEC_READY or
PLAN_READY.
```

Transactions use a singly-linked regular `.state.lock`, durable prepare record,
validated `.transaction.json`, fsync + atomic replacement, final event, and
intent removal. Recovery validates the exact event-specific write set and legal
transition before replay; an already-finalized transaction may replay only when
every target already equals its frozen value.

`DONE` is available only through `finish`. It revalidates task ownership,
integration provenance, all historical quality receipts, mandatory independent
review axes, final verification, public-boundary evidence, typed handoffs,
decision authorization, and current HEAD. Publication/local integration can be
sealed to the exact reviewed head and authorization digest.

## Resume and migration

Find unfinished state files, select one unambiguously, read `status`, then
resume its next action. `status` is the bounded operator projection; `show` is
the explicit full diagnostic projection.

Run `migrate-run` for unfinished schema 2-5 state and for schema-6 state that
still has task-policy v2, missing additive v0.12 fields, or a changed frozen
core/project policy identity. Migration is locked and atomic, rejects finished
or publication-sealed runs and active workers/reviewers, preserves immutable
historical duties monotonically, and sends active work to `REPLANNING`. It never
infers authorization or weakens a frozen project floor/quality trigger. Finished
legacy state remains readable but immutable.

Idempotent resume checks worktree/branch identity, task status, commit ancestry,
worker PID start time/result, packet head and reviewer receipt before taking an
action. Never infer ownership from a directory or a process name alone.
