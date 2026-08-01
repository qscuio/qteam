---
tags: [tools, codex-agent-team-template, skills, agent-team-dev, session, digest, template, task, wave, ai]
timestamp: 2026-07-16T04:35:57.000Z
---
# Task / Wave Session Digest Template

Every write-capable or review agent should return a concise digest. The coordinator passes these digests to `knowledge_distiller` only after implementation, review fixes, and verification are complete; the distiller turns them into learning-outbox proposals under `.agents/runs/<run-id>/learning-outbox/` (see `learning-outbox-template.md`) — it never writes to qnote directly.

## Task session digest

```markdown
## Task Session Digest

Task ID:
Task title:
Parallel group:
Agent:
Branch and commits:
Status:

Spec/plan summary:
Contract used or changed:
Write set:
Files changed:
Files read:
Commands run:
Tests added or updated:
Verification result:

Review findings addressed:
Debugger findings, if any:
Integration coverage:
Remaining risks:

Potential knowledge:
Potential lessons:
Potential skills:
```

## Wave session digest

```markdown
## Wave Session Digest

Wave ID:
Completed tasks:
Agents involved:
Status:

Contracts finalized:
Write-set violations or conflicts:
Merge gate result:
Focused tests run:
Integration tests run:
Review gates:
Findings fixed:
Verification result:

Reusable project knowledge:
Lessons from review/debugging:
Skill candidates:
Skipped/noisy items:
Remaining risks:
```

## Distillation rules

- `knowledge` is for stable facts: architecture, commands, APIs, module boundaries, tests, environment, platform behavior.
- `lessons` is for errors, gotchas, review findings, root causes, and anti-patterns that should prevent repeat mistakes.
- `skills` is for repeatable procedures with trigger, prerequisites, steps, validation, and failure modes.
- Do not create a skill for one-off trivia.
- Do not capture unverified assumptions, failed hypotheses, raw private reasoning, or sensitive data.
- Prefer dedupe/update over creating duplicate notes.
