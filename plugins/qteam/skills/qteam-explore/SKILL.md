---
name: qteam-explore
description: Discover and deeply investigate evidence-backed paths, ideas, mechanisms, and knowledge beyond the user's stated options. Use when the destination is meaningful but the solution frontier is unknown, the user requests deep/broad research, adjacent approaches or external primary research may reveal better options, or a measurable experiment should be proposed before QTeam planning. This is a bounded read-only discovery primitive; it never implements, merges, or becomes a second orchestrator.
---

# QTeam Explore

Expand the solution frontier without expanding the approved goal. QTeam remains
the only orchestration authority; this skill produces evidence and candidate
paths for the coordinator, not implementation work.

## Non-negotiable runtime boundary

A frozen packet deadline overrides coverage depth and source completeness. The
coordinator—not the worker—owns it; deep/broad research never authorizes an
open-ended turn. Before dispatch, follow the capability classification,
deadline, cancellation, and fallback algorithm in
`references/research-frontier-rule.md`. Never claim an end-to-end deadline that
the worker backend cannot enforce.

## Ownership

The coordinator freezes the exploration question, scope, constraints, and
coverage envelope. Use existing native `researcher` agents for bounded evidence
lanes. Prefer a fresh researcher with `fork_turns="none"`; when finite agent
capacity requires reuse, queue the work, disclose the reuse, and rotate depth
candidates away from their originating researcher when possible. Use
`architect` only to
falsify and compare the surviving paths. No new role is needed because the work
is read-only and has the same evidence lifecycle as research.

Lane independence comes from a frozen packet, hidden peer conclusions, and
separate evidence—not from creating an unbounded number of personas. Each
researcher packet declares `frontier_phase: breadth|depth`, contains one
named breadth dimension or one promoted depth candidate, and carries only the
frozen boundary, source policy, required evidence fields, probe limit, runtime
deadline, and stop rule. Do not pass another live lane's conclusions; fresh
lanes reduce anchoring and fake consensus.
The coordinator alone deduplicates and promotes candidates, then gives the
architect the frozen boundary and surviving dossiers—not researcher identities
or vote counts.

If answering a question requires changing code, establishing a missing
baseline, benchmarking a candidate, or building a prototype, stop discovery
and propose a normal QTeam task. Its
writes run through an isolated developer worktree, TDD where required,
mechanical gates, and independent review. Never run an autoresearch-style
commit/revert loop beside `agent-team-dev`.

## Choose the route

- Known destination and known approach: skip exploration and continue normally.
- Desired behavior is unclear: use `brainstorming` before exploring solutions.
- Destination is clear but the available paths are not: run evidence discovery.
- An explicit deep/broad request or several credible mechanism families: run
  the full research frontier.
- A candidate has an objective metric: produce a frozen experiment proposal.
- The remaining choice is subjective or changes product scope: show the
  evidence and ask the user; do not invent authority from confidence.

## Run the research frontier

Record the question, current known facts, user-suggested options, non-goals,
allowed repository/external sources, and the decision the result will inform.
Treat the user's options as seeds, not as the boundary of the search.

For an explicit deep/broad request or any search requiring more than one bounded
lookup, read and follow
`references/research-frontier-rule.md`. Execute its
`BREADTH -> PROMOTE -> DEPTH -> FALSIFY -> HANDOFF` state machine. Use the
smallest coverage envelope that can expose the relevant mechanism families;
an explicit deep/broad request selects `frontier`. No unbounded mode is allowed.

## Explore by information gain

Build a question queue across only the relevant dimensions:

1. direct repository and specification evidence;
2. adjacent implementations or mechanisms that solve the same constraint;
3. external primary sources, standards, papers, or official documentation;
4. counterexamples and failure cases that could disprove a candidate;
5. constraint inversion: what becomes possible if one assumed limitation is
   removed, relaxed, or moved to another boundary.

For each probe, predict what answer would change the decision, investigate one
bounded slice, and record evidence before interpretation. Classify each result
as `new`, `extension`, `duplicate`, `disproved`, or `insufficient`. Preserve
useful negative knowledge so later agents do not repeat failed searches.

Label every statement as observed fact, source-backed inference, or proposal.
Repository claims need file/symbol evidence; external claims need direct links
to primary sources. Confidence reflects evidence quality, not persona count.

Stop when the selected breadth dimensions and promoted depth candidates satisfy
the frontier rule, the decision has enough evidence, the packet's probe limit
is reached, or two consecutive probes produce no new/extended result. At any
stop boundary, return the required card or dossier with an honest status and
explicit gaps; never wait indefinitely for a perfect source set. Do not
rephrase duplicate ideas.

## Falsify and synthesize

Give the architect only the frozen question, constraints, and surviving depth
dossiers. For each candidate require: mechanism, evidence chain,
expected benefit, prerequisites, failure modes, cheapest falsifier, and what
remains unknown. Reject candidates that merely rename the goal, depend on an
unverified premise, or move risk outside the declared scope.

Freeze the architect packet's runtime deadline. If it expires, record
falsification as `blocked`, make no recommendation, and hand off only to the
user or `wayfinder`; coordinator opinion cannot replace independent
falsification.

Write one exploration brief using
`references/exploration-brief-template.md`. Recommend at most three paths and
state why rejected paths lost. A recommendation is not user approval.

## Experiment handoff

When a path can be tested mechanically, hand `to-spec`/`to-tickets` a proposal
with exact scope, metric name and direction, a baseline command plus either an
already observed trustworthy result or `pending at <frozen-base-commit>`, guard
command, held-out acceptance check, bounded attempt count, minimum meaningful
delta, and plateau stop rule. The working metric selects candidates; the
held-out check independently accepts behavior so the task cannot overfit its
own score. A normal isolated experiment task establishes or replays the baseline
at that frozen commit before any modification; read-only discovery never invents
or obtains a missing result by writing or benchmarking.

When no mechanical acceptance signal exists, hand off the evidence brief to
`brainstorming`, `wayfinder`, or the user decision instead of pretending the
search converged.
