---
name: wayfinder
description: Map a multi-session effort whose route is still foggy as a shared graph of decision tickets, then resolve the frontier until a spec or executable QTeam portfolio can be written. Use for work too large or uncertain for one session; do not use for a known implementation plan.
---

# Wayfinder

Wayfinding plans; it does not deliver the destination. First use `$grilling`
and `$domain-modeling` to name a concrete destination. The destination may be a
spec, a locked decision, or an executable portfolio. It fixes the scope of
every ticket.

## The map

Create one canonical map in the connected issue tracker, labelled
`wayfinder:map`; use the repository's documented local-markdown tracker only
when no issue tracker is available. The map is an index, not a store. It has:

- **Destination** — the observable end of wayfinding;
- **Notes** — domain constraints and skills each session should consult;
- **Decisions so far** — one linked one-line gist per closed ticket;
- **Not yet specified** — in-scope fog that cannot yet be phrased precisely;
- **Out of scope** — consciously excluded work that never graduates.

Refer to maps and tickets by their readable linked names in human-facing text,
not by bare IDs. Keep each full decision in exactly one ticket; the map links
and gists it instead of copying it.

When no tracker is connected, use the executable serial fallback in
[`references/local-tracker.md`](references/local-tracker.md) and start from
[`references/local-map-template.md`](references/local-map-template.md). It
keeps the same status/owner/blocking semantics in one committed Markdown map.
It does not pretend to provide atomic concurrent claims: if another session
may write the same file, stop and connect a tracker with native ownership.

## Decision tickets and frontier

Tickets are one-session questions, not implementation slices:

- **research** (AFK) — a primary-source fact outside the current context;
- **prototype** (HITL) — a cheap concrete artifact needed for human reaction;
- **grilling** (HITL) — a human decision using `$grilling` and
  `$domain-modeling`;
- **task** (AFK or HITL) — enabling work that must happen before a decision can
  be made, not delivery of the destination.

Every ticket is a child of the map and has exactly one type. Claim it before
work by using the tracker's assignee/claim mechanism. Use native blocking edges
and query the **frontier**: open, unblocked, unclaimed children. Do not emulate
dependencies in prose when the tracker has a native relationship.

Resolve at most one non-research ticket per session. Post the answer once as the
resolution, close the ticket, and link a one-line gist from the map. A HITL
ticket cannot be resolved by an agent pretending to be the human. Newly clear
fog becomes fresh tickets in a create-then-wire pass. Work beyond the
destination is closed and linked from Out of scope, not mistaken for fog.

## Fog of war

Fog is in scope but not yet precise. The test is whether the question can be
stated now, not whether it can be answered now:

- If the question is precise, create a ticket even when it is blocked.
- If the question is still vague, keep one bounded note under Not yet
  specified; do not prematurely slice it.
- If the work is beyond the destination, put it Out of scope. It never
  graduates unless the destination itself changes.

An empty frontier with remaining fog is not completion. Resolve the decision
that can sharpen the fog or revise the destination with the user.

## Two modes

### Chart the map

1. Use `$grilling` and `$domain-modeling` to name the destination.
2. Explore breadth-first. If the whole route is already clear and fits one
   session, stop: Wayfinder is unnecessary.
3. Create the map and only the tickets whose questions are precise now.
4. Create ticket identities first, then wire native blocking edges.
5. Dispatch bounded read-only research tickets to researchers when useful.
6. Stop. Charting does not also resolve a decision.

### Work the frontier

1. Load the low-resolution map, not every ticket body.
2. Use the user's named ticket or the first frontier ticket; claim it first.
3. Load related ticket detail only as needed and use the skills named in Notes.
4. Record one resolution, close the ticket, and append its linked gist.
5. Create/wire newly visible questions and remove graduated fog from the map.

Concurrent sessions may change the frontier. Re-query it immediately before a
claim and never take a ticket another owner has claimed.

When the frontier and fog are empty and the route is clear, hand the linked
decisions to `$to-spec`. QTeam remains the only implementation orchestrator.

## Multi-run QTeam portfolio

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

## Completion check

- [ ] Destination is concrete and still matches the current scope.
- [ ] Every open ticket is a decision/enabling question, not a build slice.
- [ ] Native dependencies make the current frontier mechanically visible, or
      the serial local fallback passes all five `local-tracker.md` checks.
- [ ] Claimed tickets have one owner; HITL answers came from the human.
- [ ] Decisions live in tickets and appear only as linked gists on the map.
- [ ] Genuine fog is not prematurely sliced; out-of-scope work cannot graduate.
- [ ] Empty frontier plus remaining fog is not mistaken for completion.
- [ ] Final handoff targets `$to-spec` or a frozen QTeam epic, not direct
      implementation from the map.
