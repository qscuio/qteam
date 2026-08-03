# Scenario Coverage Matrix

Before implementation, assess every dimension below. For each applicable
dimension retain at most one strongest, decision-changing scenario and link it
to one or more approved seam IDs. For a non-applicable dimension, leave
`scenario` and `seam_ids` empty and give a concrete rationale. Deduplicate
equivalent scenarios; this matrix bounds test design and does not authorize a
test per helper or permutation.

| Dimension | Question |
| --- | --- |
| happy-path | Does the main caller-visible outcome work? |
| error-path | Does the most consequential expected failure behave correctly? |
| boundary | What zero/empty/min/max or transition boundary can break it? |
| abuse-security | What hostile or malformed use is in scope? |
| scale | What realistic volume changes the mechanism? |
| concurrency | What interleaving, duplicate, or race matters? |
| temporal | What expiry, ordering, retry, or clock behavior matters? |
| data-variation | Which representative input shape changes behavior? |
| permissions | Which identity/role boundary changes the result? |
| integrations | Which external or cross-component contract can drift? |
| recovery | What partial failure or restart must recover safely? |
| state-transitions | Which legal/illegal lifecycle transition matters? |

Every row uses:

```json
{
  "dimension": "boundary",
  "applicability": "applicable",
  "scenario": "boundary: an empty request is rejected without creating state",
  "seam_ids": ["request-create"],
  "rationale": "catches validation after side effects"
}
```

For an applicable row, `scenario` starts with the exact `<dimension>: `
prefix. Because every dimension appears exactly once, this makes literal
cross-dimension duplicate scenarios structurally impossible in both the JSON
schema and runtime validator. When one case covers several dimensions, keep it
under the strongest dimension and mark overlaps not-applicable with a concrete
deduplication rationale.

Coverage is saturated when all twelve dimensions have an applicable scenario
or an explicit non-applicability rationale and equivalent scenarios have been
deduplicated. Missing relevant coverage requires re-planning; it is not filled
by spawning more roles or producing more prose.
