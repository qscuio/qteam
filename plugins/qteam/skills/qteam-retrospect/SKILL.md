---
name: qteam-retrospect
description: Use when a QTeam epic has fully delivered a product and the operator wants QTeam to learn from the cross-run outcome before the next product.
---

# QTeam Retrospect

A product closeout proposes improvements; it never edits completed runs or
canonical QTeam behavior. One-run products use the existing `LEARNING_EXPORT`.
Use this cross-run workflow only for a fully completed epic.
Each epic run still owns its approved domain, design, and implementation
knowledge through its learning outbox and the existing qnote-side importer;
the closeout references those manifests instead of becoming a second owner.

## Seal the product evidence

Confirm every epic run is `done` and its finished head is in the release:

```bash
.codex/bin/agent-team-artifact epic-status --epic <epic-id>
```

Run two independent read-only retrospective passes over durable artifacts:

- product outcome: requirements, domain decisions, ADRs, contracts, shipped
  behavior, user corrections, and rollbacks;
- QTeam behavior: late findings, rework, replanning, trajectory anomalies,
  tool failures, weak or noisy gates, and prior improvement results.

Record a distinct session identifier, bounded summary, validation scope, claim
boundary, and cited run evidence for each pass. The sealer rejects a missing
lens or the same reviewer identity on both lenses.

Reconcile their evidence into a draft shaped like
[`product-closeout-draft.json`](references/product-closeout-draft.json). Each
outcome cites regular files under its completed run. Each proposal links to an
outcome and names one target: `skill`, `worker-prompt`, `tool`, `policy`, or
`eval`. Record what earlier QTeam changes did under `prior_improvements`; an
`inconclusive` result is valid and must not be promoted as success.

```bash
.codex/bin/agent-team-artifact product-closeout-seal \
  --epic <epic-id> --release <release-commit> --file <draft.json>
.codex/bin/agent-team-artifact product-closeout-check --epic <epic-id>
```

The sealer verifies and binds the epic, release, installed QTeam project
manifest, managed runtime file set, runtime configuration, completed run state,
event logs, learning manifests, and cited evidence by digest. Do not include raw
transcripts, private reasoning, credentials, secret-bearing arguments, or
unsupported causal claims.

## Decide, then improve QTeam

The coordinator reviews proposals one at a time:

```bash
.codex/bin/agent-team-artifact product-closeout-decision \
  --epic <epic-id> --item <item-id> --outcome <approved|rejected> \
  --evidence '<bounded coordinator evidence>'
.codex/bin/agent-team-artifact product-closeout-status --epic <epic-id>
.codex/bin/agent-team-artifact product-closeout-brief --epic <epic-id>
```

`product-closeout-brief` fails while evidence is stale or decisions remain and
emits approved proposals only. Hand that brief to a separate QTeam run in the
QTeam source repository. For each approved proposal, create the held-out eval
or observable regression check first, watch it fail on the current QTeam
version, implement the smallest canonical change, run the full relevant suite,
and release a new QTeam version. A closeout never grants cross-repository write,
commit, push, or publication authority.

On a later product, assess whether those released changes helped, were neutral,
regressed behavior, or remain inconclusive. This feedback is how QTeam learns
whether its previous learning was correct instead of merely accumulating rules.
