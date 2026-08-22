# Diff Cleanup and Structural Quality Review Design

## Status

Implemented on the current feature branch; verification evidence is recorded in
the corresponding commit history and test output.

## Context

QTeam already owns durable task execution, behavior-first RED/GREEN work,
head-bound verification, conditional quality lanes, and independent spec and
standards reviews. It does not need another delivery loop or review axis.

Two practices from Cursor Team Kit at commit
`46125561306434d8a1d7745d540d8932ab0cd2a2` fill narrower gaps:

- `deslop` performs a behavior-preserving cleanup of the change a worker just
  produced.
- `thermo-nuclear-code-quality-review` tests whether a technically working
  change damages structure, ownership, boundaries, or maintainability.

The source material is MIT licensed. Any substantially copied text or code
must add Cursor's license notice to QTeam's third-party notices.

CI, PR publication, PR watching, and GitHub automation are explicitly outside
this design.

## Goals

1. Make a focused cleanup pass part of every applicable writable task after
   GREEN and before final task verification.
2. Make structural quality an explicit, mandatory part of the existing
   standards review axis.
3. Keep QTeam as the only orchestration, state, review-ledger, and completion
   authority.
4. Preserve approved behavior, task write sets, review scope, and independent
   finding closure.
5. Provide both practices as explicit user-invocable skills without allowing
   either skill to start a second implementation or review loop.

## Non-goals

- No CI watcher, CI fixer, PR creator, PR comment collector, or publication
  workflow.
- No new worker role, reviewer role, review axis, durable gate, model tier, or
  state-machine phase.
- No repository-wide cleanup initiated from a bounded task.
- No automatic history rewriting or force pushing.
- No style-only finding ledger and no mechanical ban on files over 1000 lines.
- No behavior change justified only as cleanup.

## Decisions

### 1. Keep two distinct practices

`deslop` and structural review solve different problems and run at different
times.

`deslop` is a same-worker implementation practice. It examines only the
worker's owned diff after behavior is GREEN and removes incidental complexity
introduced by that diff. It must not change observable behavior.

Structural review is an independent judgment. It examines the immutable
base/head range after integration and may create blocking standards findings
for maintainability damage. The original worker cannot attest that this review
passed and cannot close its findings.

The order is:

```text
RED commit -> minimal GREEN commit -> deslop -> focused verification
    -> cleanup commit when needed -> check/merge
    -> required deterministic quality lanes -> independent standards review
    -> finding-owned fix task -> focused re-review
```

### 2. `deslop` is injected once by the worker packet builder

Add an installed `deslop` skill for explicit invocation. Its reusable checklist
lives once in `deslop/references/diff-cleanup.md`; the skill reads that file.
Project setup copies the same bytes to `.codex/practices/deslop.md` so isolated
workers receive a repository-local, versioned practice without depending on
plugin discovery inside a child process.

Inside QTeam runs, `agent-team-worker.py` owns a single cleanup obligation
appended to applicable worker packets. It validates and captures the installed
practice bytes once, freezes them under the run by content hash, and points the
worker to that immutable snapshot. Do not duplicate the full checklist across
every role prompt.

The obligation applies when both conditions hold:

- the role can produce code or test changes: `developer`, `debugger`,
  `frontend-debugger`, `system-debugger`, `fixer`, `test-writer`, or
  `integration-tester`; and
- the work kind is `feature`, `bugfix`, `debug`, `refactor`, `test`,
  `integration`, or `experiment`.

It does not apply to `docs`, `generated`, or `learning` work. A config-only
task does not receive the automatic obligation, but the user may explicitly
invoke `deslop` when the configuration language warrants it.

The cleanup checklist is limited to new or modified lines and their smallest
necessary local context:

- remove comments that merely narrate the code or differ from local style;
- remove defensive checks, fallbacks, retries, and catch blocks that are not
  required by the approved contract or repository convention;
- remove type escapes used only to silence the type system;
- flatten avoidable nesting and delete unnecessary wrappers or helpers;
- align the change with existing local naming and control-flow conventions.

For TDD work, the worker must preserve the test-only RED and minimal GREEN
commits used by mechanical replay; it must not amend them. Cleanup changes are
committed as a focused follow-up after rerunning the task's existing focused
verification. Non-TDD work cleans the owned diff before its final task commit.

There is no self-reported `deslop_passed` state field: such a boolean would not
prove quality. Enforcement comes from the packet obligation, fresh behavior
verification, and the independent standards review.

### 3. Structural quality stays inside the standards axis

Add a user-facing `thermo-nuclear-code-quality-review` skill, but do not add a
thermo reviewer role or review ledger. Within QTeam, the skill routes to the
existing fixed-range standards review.

The authoritative rubric lives once in
`thermo-nuclear-code-quality-review/references/structural-quality.md`. The
public skill reads that reference. Project setup copies the exact bytes to
`.codex/standards/structural-quality.md`; `agent-team-review create`
automatically adds that repository-local file to every standards packet before
snapshotting sources. The packet digest, source snapshot hash, runner receipt,
and ledger therefore bind the exact rubric used by the reviewer.

The rubric gives its checks stable internal names:

- `delete-incidental-complexity`: prefer a reframing that removes branches,
  modes, helpers, or layers over moving the same complexity around;
- `no-spaghetti-growth`: new scattered special cases or feature checks in a
  shared path are structural regressions;
- `earned-abstractions`: wrappers, generic mechanisms, and indirection must
  remove more incidental complexity than they introduce;
- `explicit-boundaries`: casts, optionality, loose object shapes, and silent
  fallback must not obscure the actual invariant;
- `canonical-ownership`: logic belongs in the module that already owns the
  concept and must reuse canonical helpers when available;
- `cohesive-file-growth`: when the change pushes a source file from below
  1000 lines to above 1000 lines, the reviewer must explicitly assess whether
  the file remains cohesive or should be decomposed.

The 1000-line crossing is a strong review signal, not an automatic failure.
Generated files, declarative tables, and formats whose repository convention
is intentionally monolithic may justify it. The reviewer obtains before/after
counts from the immutable base/head range and records concrete evidence if it
becomes a finding.

The public skill explains QTeam routing, but it is not a second machine
authority because it loads the same reference file. Standards packet creation
fails with a project-refresh instruction if the installed rubric is absent or
differs from the plugin source recorded by the setup manifest.

### 4. Separate defects from optional improvement ideas

The standards ledger remains defect-only. A structural finding is valid when
the reviewed change introduces or materially worsens one of the named
conditions, or when an obvious bounded reframing is necessary to keep the
approved behavior maintainable.

A broader cleanup opportunity that predates the change is not a review
finding. It may become a bounded learning proposal, or the coordinator may ask
the user whether to replan, but it cannot block the current wave as an
unrelated optional refactor.

If the required structural fix exceeds the frozen write set, changes an
approved contract, or moves ownership across planned task boundaries, the
coordinator enters the existing decision/replanning path. The reviewer does
not authorize the expansion, and the fixer does not smuggle it into a finding
closure task.

### 5. Preserve review-intensity bounds

The structural rubric is always part of standards review, but its traversal
obeys the existing intensity:

- `compact`: changed code, focused tests, directly affected contracts, and
  before/after size of changed files;
- `full`: the same checks plus necessary callers, error paths, canonical helper
  locations, and directly affected ownership boundaries;
- `risk`: full structural scope plus the already named risk and
  rollback/failure paths.

The rubric never turns compact review into a repository-wide audit.

### 6. Keep the refactor lane deterministic and distinct

`qteam-harden` continues to own policy-triggered deterministic refactor
evidence after integration. Its deletion test and ownership assessment should
use the same vocabulary as the structural rubric, but it does not launch an
LLM review and does not attest standards quality.

The three layers are complementary:

| layer | owner | question |
|---|---|---|
| task cleanup | assigned worker via `deslop` | Did this task introduce removable local noise? |
| refactor lane | coordinator and frozen commands | Does the integrated HEAD satisfy the required deterministic property? |
| standards review | independent standards reviewer | Did the change damage structure or miss a necessary bounded simplification? |

## Component Changes

### New skill: `deslop`

Path: `plugins/qteam/skills/deslop/SKILL.md`.

The skill is behavior-preserving, diff-scoped, and subordinate to QTeam when a
run exists. An assigned worker may record bounded cleanup in a focused task
commit, but the skill cannot merge, change task scope, alter tests to make them
pass, or claim completion.

### New skill: `thermo-nuclear-code-quality-review`

Path:
`plugins/qteam/skills/thermo-nuclear-code-quality-review/SKILL.md`.

Outside a QTeam run it performs a read-only fixed-diff maintainability audit.
Inside a run it directs the caller to the existing standards packet and
finding ledger. It never spawns its own reviewer or writes findings outside
`agent-team-review`.

### Worker packet assembly

Update `plugins/qteam/bin/agent-team-worker.py` with one derived cleanup
obligation based on role and work kind. It validates installed
`.codex/practices/deslop.md`, freezes the captured bytes in the run, and points
to that content-addressed snapshot. The task record remains unchanged.

### Review policy and runner

Update `plugins/qteam/bin/agent-team-review.py` so standards packet creation
automatically includes the installed structural rubric as an immutable
standards-source snapshot. Existing project-specific `--standards-source`
arguments remain required and retain their current meaning. Spec and risk
packets do not automatically include this rubric because standards is already
mandatory. `agent-team-review.py` remains the sole review packet/receipt writer.

Do not change `agent_team_policy.py`, the core policy digest, or the review
contract digest for this feature. The packet digest and source-snapshot digest
already bind the added rubric more precisely, without invalidating historical
policy or review packets.

### Existing workflow documentation

Update project setup, refresh, uninstall, and doctor manifests to install and
verify `.codex/practices/deslop.md` and
`.codex/standards/structural-quality.md`. Update `agent-team-dev`,
`qteam-review`, `qteam-harden`, the standards reviewer role, README, plugin
descriptions, version, and third-party notices so that the public and internal
contracts agree.

No new state schema, policy version, review packet schema, review contract
version, or run migration is required because neither capability adds a
durable fact or changes policy derivation. Existing review packets retain their
frozen source set and remain immutable. New standards packets include the new
rubric snapshot; already attempted or completed packets are never rewritten.

## Failure Handling

- If cleanup changes a focused test outcome or approved behavior, revert the
  cleanup and report the conflict; do not weaken the test.
- If cleanup needs an out-of-write-set edit, stop and return the need to the
  coordinator.
- If the standards reviewer reports only a style preference or pre-existing
  unrelated debt, the finding is invalid and must be disproved through the
  existing dispute flow.
- If a valid structural finding requires broader ownership or contract change,
  enter decision/replanning before implementation.
- If a source reference and installed runtime copy differ, setup/doctor and
  tests fail; packet creation must not silently use a stale copy.

## Verification Strategy

### Skill checks

- Validate both skill frontmatters, reference files, and plugin discovery.
- Check that each skill states its QTeam routing and non-orchestration boundary.
- Check that project setup copies each authoritative reference byte-for-byte,
  refresh updates it safely, uninstall tracks it, and doctor requires it.
- Add the Cursor MIT license and third-party notice when upstream wording is
  substantially reused.

### Worker-packet tests

- Applicable role/work-kind pairs receive exactly one cleanup obligation.
- `docs`, `generated`, `learning`, and inapplicable roles receive none.
- Project-local role prompt overrides cannot remove the central obligation.
- The obligation requires post-cleanup focused verification and forbids scope
  expansion and behavior changes.

### Review-policy tests

- Every new standards packet automatically freezes the installed structural
  rubric in addition to project-specific standards sources.
- Spec and risk packets do not accidentally become duplicate structural
  reviews.
- Changing the rubric changes its source snapshot and packet digest without
  changing the core policy or invalidating historical packets.
- Compact/full/risk traversal bounds remain intact.
- Existing finding, dispute, receipt, identity, and immutable-packet tests
  continue to pass.

### Workflow tests

- A cleanup-created behavior regression is caught by focused verification.
- A concrete structural regression can be recorded as a standards finding,
  assigned to a fresh fix task, and closed only by a fresh re-review.
- An unrelated optional refactor is rejected as a standards finding.
- A source file crossing 1000 lines is explicitly assessed without becoming a
  mechanical failure.

### Final verification

Run the repository's complete test suite and plugin/project setup smoke tests.
Inspect the final diff for generated runtime copies and third-party notice
consistency.

## Rollout

Ship both skills, authoritative references, runtime copies, and internal hooks
in one version so users cannot invoke a public capability whose QTeam routing
is absent. Do not bump the policy version and do not migrate or rewrite existing
review ledgers; their frozen packet sources remain valid for the version that
created them.
