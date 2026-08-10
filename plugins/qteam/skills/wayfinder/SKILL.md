---
name: wayfinder
description: Map a multi-session effort whose route is still foggy, resolving decision tickets until a spec can be written.
---

# Wayfinder

Wayfinding plans; it does not deliver the destination. First grill the user to
name a concrete destination. Create a shared map with Destination, Notes,
Decisions so far, Not yet specified, and Out of scope. Add only decisions that
can already be phrased precisely as tickets; leave genuine fog unsliced.

Tickets are one-session questions: research (AFK), prototype (human feedback),
grilling (human decision), or an enabling task. Claim before work, resolve at
most one per session, record the answer once in the ticket, and link a one-line
gist from the map. Newly clear fog becomes new tickets. Work beyond the
destination is closed as out of scope, not mistaken for fog.

When the frontier is empty and the route is clear, hand the accumulated
decisions to `to-spec`. QTeam remains the only implementation orchestrator.

If the destination requires multiple independently executable QTeam runs,
freeze their portfolio before starting any one run. Create the epic, prepare a
single plan shaped like `references/epic-plan-template.json`, then validate and
commit it transactionally:

```bash
.codex/bin/agent-team-artifact epic-init --epic <epic-id> --goal '<goal>'
.codex/bin/agent-team-artifact epic-plan --epic <epic-id> --file <plan.json>
.codex/bin/agent-team-artifact epic-status --epic <epic-id>
```

Every run declares its spec, `depends_on` run IDs, and stable interface
contracts with one owning run and named consumers. Keep roles unchanged:
`architect` owns contract boundaries and `parallel_planner` owns the cross-run
DAG. Each contract must be declared by exactly its owner and named consumers.
Start only the reported unblocked run with
`agent-team-state ... init --epic <epic-id>`. After that QTeam run is durably
`DONE`, record its evidence with `epic-complete-run`; only then may downstream
runs pass their mechanical init gate, and their base must contain every
predecessor's recorded finished head. A plan may be replaced only before any
run starts. Never use the epic manifest as a second task executor.
