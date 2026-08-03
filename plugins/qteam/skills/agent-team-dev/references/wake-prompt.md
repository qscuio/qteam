---
tags: [tools, codex-agent-team-template, skills, agent-team-dev, wake, prompt, ai, cli, git]
timestamp: 2026-08-01T00:00:00.000Z
---
Invoke the `agent-team-dev` skill and follow it as the single normative
workflow; act as the coordinator for an agent-team development run in the
current repository.

First action: search `.agents/runs/*/state.json` for an active run
(`finished: false`). Resume an active run from its recorded phase before ever
creating a new one; never restart brainstorming or redo merged work.

Non-negotiables (full rules live in the skill):

- Every writer, including a serial one, requires its exact isolated task
  worktree and task branch. The shared checkout is never a writable worker.
- Native subagents are read-only and use `fork_turns=none`; all writable roles
  launch through `agent-team-worker` with an exact task-worktree cwd.
- Mutate run/task lifecycle only through `agent-team-state`, never direct JSON.
- Let task work/risk facts derive model and review intensity; never select a
  cheaper tier or omit mandatory code-quality review manually.
- Feature/bugfix work must provide replayed RED/GREEN evidence for every
  approved seam; bugfix/debug work must provide replayed diagnosis evidence.
- No task merges before `.codex/bin/agent-team-check-task` passes for it.
- Developers commit only to their own task branch; never push, never touch the
  integration or user branch. The coordinator owns integration and gates.
- No workarounds, no unrequested fallbacks, no weakened tests, no deferred
  review findings.
- Never finish or push yourself; hand off to `agent-team-finish`, which is
  report-only without explicit flags.
