---
name: qteam-review
description: Review a fixed QTeam change range on one independent axis using a finding ledger.
---

# QTeam Review

The coordinator first creates a packet with `agent-team-review create`. The
packet freezes base SHA, head SHA, merge base, three-dot diff range, commit
list, and content-addressed snapshots of spec/standards sources.

Run exactly one independent axis per reviewer. Both `spec` and `standards` are
mandatory on every gate:

- `spec`: required behavior, scope, acceptance, tests, and forbidden fallback.
- `standards` (code-quality): correctness, repository standards, architecture, error handling,
  maintainability, and regression risk.
- `risk`: only when concurrency, security, migration, compatibility, data loss,
  auth, or a public API triggers it. This additional axis never replaces either
  mandatory axis.

Read-only reviewers return bounded structured findings; the coordinator records
them with `agent-team-review add --reviewer <identity>`. A finding needs
severity, evidence, impact, owner, and fix direction. Fixers never resolve
findings; a fresh read-only reviewer decides closure, then the coordinator uses
`resolve --reviewer <identity>` with its evidence. The gate
passes only when required ledgers are complete at the current head and no
finding remains open.

Before completion, each independent reviewer writes one bounded JSON result:

```json
{"axis":"standards","verdict":"pass","findings":[]}
```

The coordinator completes the ledger with `complete --reviewer <identity>
--session-id <review-task-id> --result <json-path>`. Spec and standards must
use different reviewer identities and different session ids. The attestation
and result digest are copied into the immutable ledger; a claimed reviewer
name alone cannot pass the gate.

## Token/granularity policy

- Task level uses mechanical gates only; never launch an LLM review per task.
- A small serial change gets one compact spec pass and one compact code-quality
  pass over its final diff.
- Medium/large work gets those two passes once per integrated wave, not per
  file, commit, or task.
- Re-review uses `scope=fix` and only the finding-owned fix diff plus necessary
  context. Do not reread the whole wave.
- Do not repeat a final whole-branch review when the current HEAD is already
  covered by completed mandatory ledgers.
- Start from diffstat, commit list, and bounded task digests; expand context
  only for an evidenced risk. Output findings only, with no plan/code recap.
