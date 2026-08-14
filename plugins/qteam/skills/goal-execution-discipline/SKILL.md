---
name: goal-execution-discipline
description: "Global QTeam execution contract for end-to-end goal work: preserve scope, reject workarounds, verify evidence, review independently, and report status honestly."
---

# Goal Execution Discipline

This is a standing safety and completion contract, not an orchestrator. During
a QTeam run, `agent-team-dev` owns phases, roles, workers, merging, and finish;
this skill constrains how every participant behaves.

## Front gate

Before edits, inspect the active branch and dirty worktree, read repository
instructions and relevant domain skills, and preserve exact failure evidence
for debug/fix work. Never copy credentials, private logs, or secrets into run
artifacts, commits, learning, or summaries.

## Scope and design integrity

- Preserve the user's approved design and full scope. Do not silently narrow,
  simplify, defer, or substitute an easier design.
- Do not add workaround paths, silent fallbacks, bypasses, speculative fixes,
  compatibility behavior, caches, retries, timeouts, or feature-disablement to
  make a gate green unless the approved spec explicitly requires that behavior.
- Fix root causes and change only the owned surface. Preserve legacy behavior
  unless removal is approved.
- If the design conflicts with repository evidence, stop before code, present
  the evidence and alternative, and wait for the user's decision.

## Execution and evidence

Keep QTeam task/phase state current through `agent-team-state`. Complete every
planned slice, not only the first convenient subset. For a failure, preserve
the exact RED symptom before the change and exact GREEN command/output after.
Run focused and final verification proportional to risk.

For an explicit autonomous goal, use `qteam-goal` as a projection of that
durable state. Native Codex/Claude/Cursor continuation is a session lease, not
completion evidence. Wait on one checkpoint call instead of spending model
turns polling, and never force a new primary session merely because a run is
long.

Never claim completion from assertion alone. A failing or unavailable command
is reported verbatim with its impact and remaining risk.

## Mandatory independent reviews

After implementation, always run both fresh-context axes:

1. `spec`: missing scope, wrong ownership, workaround/fallback, incomplete plan
   items, and missing verification;
2. `standards` (mandatory code-quality review): correctness, error handling,
   concurrency, API contracts, build/install integration, architecture,
   maintainability, and tests.

`risk` is an additional axis for triggered high-risk changes; it never replaces
either mandatory axis. Fix every valid finding and retest. Mark an invalid
finding invalid only with evidence. Material fixes are re-reviewed.

## Status honesty

`DONE` means all requested work is complete, gates pass, both mandatory review
axes close, and no valid finding remains. If work stops earlier, report
`DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`, exactly what remains, and
the command/evidence causing it. Never narrate partial progress as completion.
