---
name: deslop
description: Use when an implementation has reached GREEN and its diff needs a final behavior-preserving cleanup before verification or handoff.
---

# Deslop

Remove incidental complexity introduced by the current change without altering
its intended behavior.

## Procedure

1. Identify the task base and inspect only the current task diff plus enough
   surrounding code to understand local conventions.
2. Read [references/diff-cleanup.md](references/diff-cleanup.md) and apply every
   relevant check.
3. Make the smallest edits that delete needless comments, defensive padding,
   type-system escapes, nesting, duplication, or foreign idioms introduced by
   the diff.
4. Review the cleaned diff again, then rerun the focused verification that made
   the implementation GREEN.
5. Report what was removed and the verification result. If no cleanup was
   warranted, say so directly.

## Guardrails

- Preserve behavior. A bug discovery is not permission to expand a cleanup
  pass into an unplanned fix.
- Preserve test-only RED and minimal GREEN commits. For TDD work, record cleanup
  as a focused follow-up commit; do not amend away the evidence. For non-TDD
  work, clean before the final task commit.
- Stay inside the approved write set. Report valuable cleanup outside it rather
  than editing it.
- Do not perform broad rewrites, speculative abstraction, formatting churn, or
  generated-file cleanup.
- Passing tests do not make newly introduced slop acceptable, even under a
  deadline. Complete the bounded cleanup and rerun focused verification.
- If cleanup changes behavior or a focused test outcome, revert that cleanup
  and report the conflict; never weaken the test.
- In an active QTeam run, QTeam remains the sole orchestration authority. Do
  not create another workflow, reviewer, or agent.
