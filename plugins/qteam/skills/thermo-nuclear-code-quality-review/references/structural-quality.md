# Structural quality rubric

Apply these checks to the reviewed diff. Functional correctness is necessary
but does not excuse a structural regression.

## `delete-incidental-complexity`

Look for a reframing that removes concepts, modes, helpers, branches, or layers
instead of rearranging them. Require it only when the simpler path is concrete
and bounded.

## `no-spaghetti-growth`

Reject scattered special cases, unrelated conditionals, nullable modes, or
feature checks bolted into busy flows when a cohesive owner can absorb them.

## `earned-abstractions`

Thin wrappers, one-implementation interfaces, generic managers, cast-heavy
contracts, and magic dispatch must buy clear separation or reuse. Prefer direct,
boring code when they do not.

## `explicit-boundaries`

Flag unclear invariants, silent fallback, partial state updates, unnecessary
optionality, or orchestration that obscures error, type, lifetime, concurrency,
or atomicity boundaries.

## `canonical-ownership`

Keep behavior and state in the module that owns the concept. Reuse canonical
helpers and reject duplicated policy, feature leakage into shared paths, or
pass-through layers that create a second source of truth.

## `cohesive-file-growth`

Review whether a growing file remains one cohesive unit. Crossing from below
1000 lines to above 1000 lines is a strong signal to examine decomposition, not
an automatic defect. Generated, declarative, or intentionally monolithic files
may justify the size when splitting would damage ownership or readability.

## Finding bar

A finding must point to changed lines or a dependency activated by them,
explain the concrete maintenance risk, and propose a direction that reduces
complexity. Do not report taste, optional polish, or unrelated historical debt
as a defect.
