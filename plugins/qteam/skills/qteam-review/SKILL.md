---
name: qteam-review
description: Review a fixed QTeam change range on one independent axis using a finding ledger.
---

# QTeam Review

The coordinator first creates a packet with `.codex/bin/agent-team-review
--run <run> create --wave <N> --axis <axis> --base <base> --head <head>
--spec-source <path>` (use `--standards-source` for standards/risk). The
packet freezes base SHA, head SHA, merge base, three-dot diff range, commit
list, content-addressed snapshots of spec/standards sources, derived execution
tier, review intensity, a redacted trajectory summary, frozen judge-calibration
cases, and the deterministic artifact-lint report. A typed
spec with lint errors is rejected before a reviewer launches. Legacy sources
remain reviewable with an explicit untyped-source warning.

Run exactly one independent axis per reviewer. Both `spec` and `standards` are
mandatory on every gate:

- `spec`: required behavior, scope, acceptance, tests, and forbidden fallback.
- `standards` (code-quality): correctness, repository standards, architecture, error handling,
  maintainability, and regression risk.
- `risk`: only when concurrency, security, migration, compatibility, data loss,
  auth, or a public API triggers it. This additional axis never replaces either
  mandatory axis.

Read-only reviewers return bounded structured findings; `agent-team-review run`
atomically records a valid `needs-fix` result in its ledger. A finding needs
severity, evidence, impact, owner, and fix direction. Fixers never resolve
findings. In `FIXING`, create a fresh serial task whose `finding_ids` name the
open findings and whose base is current integration HEAD. After its gated merge,
create a non-empty packet with `--iteration <N>` (where `N >= 2`) and
`--scope fix`; the frozen `closure_findings` set and a
fresh read-only reviewer receipt close exactly those IDs. Manual `resolve` is
rejected. The gate passes only when required ledgers are complete at current
HEAD and no finding remains open.

The coordinator must launch each independent reviewer through
`agent-team-review run --ledger ... --reviewer ... --session-id ...`. The
runner invokes `codex exec` with the packet's immutable model/thinking profile,
provider/family identity, read-only sandbox, packet digest, and result path,
then records the actual Codex CLI version and a launch/result receipt. Do not
launch a reviewer separately and self-report its model. The receipt is rejected
unless the reviewer consistently classifies the transparent calibration canaries
correctly.

Each independent reviewer writes one bounded JSON result. Use `needs-fix` with
stable finding IDs and evidence when defects exist; that receipt cannot
complete a ledger. The runner records those findings atomically; the coordinator
assigns fixes and launches a fresh re-review iteration. A needs-fix result is:

```jsonc
{"axis":"standards","verdict":"needs-fix","trajectory_verdict":"pass","calibration_results":{"<frozen-case-id>":"<independent-classification>"},"findings":[{"id":"F-123","severity":"P1","title":"Lost error","review_evidence":"The changed call drops EIO at foo.c:42","impact":"The operation reports success after data loss","fix_direction":"propagate and test EIO","owner":"fixer"}],"resolved_ids":[],"invalid_ids":[],"upheld_ids":[],"invalid_evidence":{}}
```

A clean fix re-review returns:

```jsonc
{"axis":"standards","verdict":"pass","trajectory_verdict":"pass","calibration_results":{"<frozen-case-id>":"<independent-classification>"},"findings":[],"resolved_ids":["F-123"],"invalid_ids":[],"upheld_ids":[],"invalid_evidence":{}}
```

For an initial clean review all three closure arrays—`resolved_ids`,
`invalid_ids`, and `upheld_ids`—are empty. A fix review partitions
the packet's frozen closure set between `resolved_ids` and evidence-backed
`invalid_ids`. To dispute a false positive without changing code, create
iteration 2+ with `--scope dispute --base <head> --head <head>`; its fresh
independent reviewer must put the entire frozen set in `invalid_ids`. The
result's `invalid_evidence` object must contain one non-empty disproof rationale
for every invalidated ID and no other keys. If the dispute reviewer upholds the
finding, it returns `needs-fix`, no new findings, and the entire frozen set in
`upheld_ids`; the IDs stay open and the coordinator proceeds to a valid-finding
fix task. A fix re-review that proves the change is still insufficient uses the
same upheld result; the next iteration remains possible without inventing a
duplicate finding ID. The
coordinator completes the ledger with `complete --ledger <ledger>
--receipt <runner-receipt>`. Completion verifies the receipt, packet and result
digests, exact execution profile, session identity, and result path. Spec and standards must
use different reviewer identities and different session ids. The attestation
and result digest are copied into the immutable ledger; a claimed reviewer
name alone cannot pass the gate.

## Token/granularity policy

- Task level uses mechanical gates only; never launch an LLM review per task.
- `compact`: one spec pass and one code-quality pass over the final diff,
  affected acceptance clauses/contracts, and focused tests. No general audit.
- `full`: those same mandatory axes once per integrated wave, following only
  necessary callers, error paths, and integration cases.
- `risk`: full review plus the independent risk axis, bounded to packet risk
  flags and their rollback/failure paths.
- A valid-finding re-review uses `scope=fix` and only the non-empty,
  finding-owned fix diff plus necessary context. A false-positive re-review uses
  `scope=dispute` at unchanged HEAD and only the frozen finding evidence. Do not
  reread the whole wave in either case.
- Do not repeat a final whole-branch review when the current HEAD is already
  covered by completed mandatory ledgers.
- Start from diffstat, commit list, and bounded task digests; expand context
  only for an evidenced risk. Output findings only, with no plan/code recap.
- Inspect compact trajectory anomaly codes and counts, never raw worker logs or
  private reasoning. Use `trajectory_verdict=needs-fix` only when the evidence
  changes the code-quality/spec judgment; do not turn visibility metadata into
  an unbounded process audit.
- Do not repeat artifact-lint checks that already passed. Inspect its bounded
  warnings plus semantic behavior and risk; the absence of a typed marker alone
  is not a review finding.
- Model choice follows the packet tier: `economy` uses the run's reviewer
  economy profile, `standard` its reviewer standard profile, and `deep` its
  reviewer deep profile. The
  runner passes that model/thinking setting when spawning each fresh read-only
  reviewer; reviewers never self-escalate.
