# Exploration brief template

Use this structure for the bounded output of `qteam-explore`. Omit empty
sections; do not turn the brief into an implementation plan.

```markdown
# Exploration: <frozen question>

## Decision this informs
<one decision, owner, and why it matters>

## Boundary
- Goal:
- Known facts:
- User-proposed seeds:
- Constraints/non-goals:
- Coverage envelope used:
- Repository/external source boundary:
- Deadline policy:

## Frontier coverage
| Dimension | Selection | Result | Author / worker state | Capability / deadline / timestamps | Probes | Stop reason | Evidence or explicit gap |
|-----------|-----------|--------|--------|------------------------------------|--------|-------------|--------------------------|
| repo-native | selected / not-applicable | complete / blocked / operationally-blocked / not-run | researcher / coordinator-fallback / none (live worker) | ... | ... | ... | ... |
| external-analogs | selected / not-applicable | complete / blocked / operationally-blocked / not-run | researcher / coordinator-fallback / none (live worker) | ... | ... | ... | ... |
| adversarial | selected / not-applicable | complete / blocked / operationally-blocked / not-run | researcher / coordinator-fallback / none (live worker) | ... | ... | ... | ... |

## Evidence ledger
| ID | Question | Classification | Evidence | Confidence | Decision impact |
|----|----------|----------------|----------|------------|-----------------|
| E1 | ...      | new            | ...      | high       | ...             |

Classifications: `new`, `extension`, `duplicate`, `disproved`, `insufficient`.

## Candidate paths

### P1 — <path>
- Promotion: promoted | duplicate | disproved | insufficient
- Promotion evidence:
- Mechanism:
- Distinctness from known options:
- Evidence chain:
- Expected benefit:
- Expected decision impact:
- Prerequisites:
- Failure modes:
- Cheapest falsifier:
- Unknowns:

## Depth dossiers

### P1 — <promoted path>
- Status: complete | disproved | blocked
- Author: researcher | coordinator-fallback
- Causal mechanism:
- Strongest supporting evidence:
- Strongest contradicting evidence:
- Assumptions and dependency chain:
- Boundary interactions:
- Failure envelope:
- Cheapest decisive test:
- Attempted probes:
- Stop reason:
- Residual unknowns:
- Confidence by claim:

## Negative knowledge
- <path/question already disproved and the evidence>

## Operational blockers
- Phase / dimension / candidate:
- Status: operationally-blocked
- Worker state: live / cancel-failed
- Evidence received:
- Remaining verified capacity:
- Effect on required later phases:

## Falsification
- Status: complete | blocked
- Author: architect | coordinator-fallback
- Challenged claims:
- Survivors and rejected/deferred candidates:
- Stop reason and gaps:

If falsification is `blocked`, omit Recommendation and route only to the user or
`wayfinder`. A recommendation requires completed falsification and at least one
evidence-sufficient surviving dossier.

## Recommendation (conditional)
1. <recommended path and why>
2. <alternate path and when it wins>

Rejected paths: <why they lost; do not silently omit them>

## Decision needed
<user/value judgment, or `none` when the evidence determines the next step>

## Handoff
- Route: to-spec | to-tickets | brainstorming | wayfinder | user | stop
- If experimental:
  - Scope:
  - Metric + direction:
  - Baseline command + observed result, or `pending at <frozen-base-commit>`:
  - Guard command:
  - Held-out acceptance check:
  - Attempt budget:
  - Minimum meaningful delta:
  - Plateau rule:
```

Evidence entries must distinguish observations, source-backed inference, and
proposals. Link primary external sources directly and cite repository facts by
file and symbol or line.
