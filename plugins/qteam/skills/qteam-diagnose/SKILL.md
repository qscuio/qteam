---
name: qteam-diagnose
description: Diagnose application, frontend, system, build, integration, flaky, and performance failures through a tight feedback loop, minimized reproduction, ranked falsifiable hypotheses, root-cause tracing, and regression evidence. Use before proposing or implementing any bug fix.
---

# QTeam Diagnose

Read `CONTEXT.md` and relevant ADRs when present, plus the exact error/trace,
recent changes, and one known-working analogue. Do not propose a fix before
completing the evidence phases.

If the frozen diagnosis command depends on a new regression test that does not
exist at task base, first follow `qteam-tdd` steps 1–3 to create its test-only
RED commit, then return here and diagnose at that commit. Do not implement
GREEN until the root cause and ranked hypothesis evidence are complete.

1. Use the task's frozen `diagnosis_command` and `failure_pattern` as the fast,
   deterministic, agent-runnable feedback loop for the user's exact symptom.
   During planning prefer a failing test, CLI/curl fixture, browser
   script, trace replay, small harness, fuzz/property loop, automated bisection,
   or differential comparison—in that order when applicable. For a flaky bug,
   raise and measure reproduction rate instead of pretending it is stable.
2. Run it RED at the report's `repro_commit` and capture exact output. QTeam
   mechanically replays the command and checks the failure pattern when the
   report is ingested. If no red-capable loop is possible,
   stop and request the missing environment or artifact; do not theorize from
   an unrepeatable anecdote.
3. Minimize inputs, callers, state, configuration, and steps one variable at a
   time until every remaining element is necessary for RED.
4. Write 3–5 hypotheses ranked consecutively from 1. Give each a falsifiable
   prediction and the smallest discriminating check. Use one debugger probe or
   boundary log rather than broad logging. Prefix temporary instrumentation
   with a unique `[QTEAM-DEBUG-<id>]` marker.
5. Trace the winning hypothesis backward across callers/data boundaries to the
   original trigger. State the causal chain, ownership boundary, and why nearby
   alternatives were falsified. Fix the source, not the visible symptom.
6. Start `.qteam-diagnosis.json` using the contract in
   [diagnosis-report.md](references/diagnosis-report.md). Keep it uncommitted
   and set its cleanup field to the exact cleanup/verification obligation. The
   coordinator runs `.codex/bin/agent-team-state --run <run> diagnosis-put
   <task> --file <task-worktree>/.qteam-diagnosis.json`, which validates,
   records, and consumes it.
7. For a fix with an existing reproduction, use `qteam-tdd` to turn the
   minimized repro into a RED regression test at the correct seam. If the
   prelude already created that RED commit, resume `qteam-tdd` at GREEN.
   Implement one root-cause change, prove GREEN, then rerun the original loop.
8. Remove every `[QTEAM-DEBUG-...]` marker and throwaway harness before the
   mechanical gate. `.codex/bin/agent-team-state --run <run> verify-task <task>`
   records original-loop GREEN at exact task
   HEAD; `.codex/bin/agent-team-check-task --run <run> --task <id>` proves marked
   instrumentation is gone. The report records
   the cleanup plan and preventive architectural lesson before it is consumed.

After three failed fix attempts on the same causal chain, stop and return to
QTeam replanning; do not stack a fourth speculative change. Role prompts select
domain tools without weakening this workflow.
