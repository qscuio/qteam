# Diff cleanup checklist

Apply this checklist to code introduced or materially changed by the task.
Match the surrounding codebase rather than imposing a foreign style.

## Remove

- Comments that narrate obvious syntax, repeat names, announce edits, or use a
  tone inconsistent with nearby code. Keep comments that explain a non-obvious
  invariant, constraint, or reason.
- Defensive branches, validation, fallback, or `try`/`catch` padding on trusted
  internal paths when the local contract already guarantees the condition.
- Casts, `any`, warning-suppression tricks, or dummy assignments used only to
  silence a type checker or compiler instead of expressing the real contract.
- Deep nesting that a local early return, guard clause, or direct expression
  can simplify.
- Duplicate helpers, pass-through wrappers, temporary variables, one-use
  abstractions, and verbose control flow that do not improve meaning.
- Generic or cross-language idioms that fight the file's established naming,
  ownership, error-handling, resource-lifetime, or data-structure conventions.

## Preserve

- Required boundary validation, security checks, compatibility behavior,
  synchronization, lifetime management, and error propagation.
- Deliberate structure that documents an invariant or keeps ownership clear.
- Generated artifacts unless the task explicitly owns their generator and
  regeneration is part of verification.

When uncertain whether a deletion is behavior-neutral, leave it in place and
report the uncertainty. Deslop is a cleanup pass, not a license to guess.
