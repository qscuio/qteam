---
name: thermo-nuclear-code-quality-review
description: Use when a branch needs an unusually strict maintainability review focused on structural simplification, abstraction quality, file growth, and boundary ownership.
---

# Thermo-Nuclear Code Quality Review

Review the current change for structural quality, not merely functional
correctness. Seek high-leverage simplifications that delete incidental concepts,
branches, layers, or ownership ambiguity while preserving intended behavior.

## Review method

1. Establish the exact base/head range and inspect the diff with enough
   surrounding code to understand canonical ownership and local architecture.
2. Read [references/structural-quality.md](references/structural-quality.md) and
   apply every check to each meaningful change.
3. Prefer a small number of high-conviction, actionable findings over cosmetic
   nits. Each finding must identify diff evidence, maintainability impact, and a
   bounded fix direction.
4. Pass when the diff creates no defensible structural defect. Do not manufacture
   findings merely to sound strict.

## Boundaries

- Findings are defect-only: a regression introduced by the diff or a problem
  that the changed behavior materially depends on. Unrelated pre-existing debt
  may be noted separately but cannot block this change.
- A clear structural simplification can be required even when tests pass. Avoid
  speculative rewrites whose benefit cannot be stated concretely.
- File-size thresholds are review signals, never automatic failures.
- Respect the approved review range and write set. If the sound fix crosses an
  ownership boundary, require replanning rather than silently widening scope.
- In an active QTeam run, use the existing `standards` review axis and its
  finding ledger. QTeam remains the sole orchestration authority; do not create
  a second reviewer, axis, or workflow.
