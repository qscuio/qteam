# Research frontier rule

Use this rule when the user explicitly requests deep/broad research or when a
single lookup cannot expose and compare the plausible solution frontier. QTeam
remains the only coordinator; existing read-only researchers execute frozen
lanes and the architect performs final falsification.

## Multi-agent fit gate

Use more than one researcher only when at least two evidence lanes are genuinely
independent: they have distinct source/tool boundaries, do not need each
other's live conclusions, can persist their outputs as separate referenced
artifacts, and can be synthesized after they finish. If the question requires
one shared evolving context, tight sequential dependencies, or real-time
cross-agent steering, use one researcher or serial lanes. Never add agents just
to increase activity or fill capacity.

This boundary follows Anthropic's public production finding that multi-agent
research is strongest for breadth-first independent directions and much weaker
for tightly dependent work, especially coding. Their reported fixed agent/tool
counts are observations from one research system, not QTeam defaults; QTeam's
coverage envelope and available safe capacity remain authoritative. See
<https://www.anthropic.com/engineering/multi-agent-research-system>.

## State machine

```text
BREADTH -> PROMOTE -> DEPTH -> FALSIFY -> HANDOFF
```

- `BREADTH`: discover distinct mechanisms without ranking across lanes.
- `PROMOTE`: let the coordinator deduplicate and apply the evidence gate.
- `DEPTH`: trace one promoted candidate per cold lane.
- `FALSIFY`: let the architect challenge the surviving dossiers.
- `HANDOFF`: route evidence to a decision, spec, ticket, or experiment.

Never let a researcher promote its own candidate, compare persona votes, see
another live lane's conclusions, or start a new lane.

Fresh researcher context is preferred, not an excuse to create an unbounded
agent tree. When runtime capacity is smaller than the selected coverage, queue
lanes and reuse an idle read-only researcher with a newly frozen packet. Record
the reuse as a coverage limitation, and when an alternative worker exists, do
not assign a researcher to deepen a candidate it proposed. Architect
falsification must account for any remaining anchoring risk.

## Coverage envelope

Choose coverage for decision quality, not output length:

- `focused`: one breadth dimension; deepen at most one promoted candidate.
- `standard`: two independent breadth dimensions; deepen at most two candidates.
- `frontier`: all three breadth dimensions; deepen every promoted candidate,
  with at most three candidates and mandatory architect falsification.

Use `frontier` for an explicit deep/broad request, irreversible or high-impact
architecture decisions, or a solution space with several credible mechanism
families. No mode may exceed three live researcher lanes; queue later lanes
instead of spawning around that limit.

The three breadth dimensions are:

1. `repo-native`: current constraints, unused seams, and structurally different
   approaches already supported by the repository.
2. `external-analogs`: primary implementations, standards, papers, or official
   designs that solve the same underlying constraint.
3. `adversarial`: counterexamples, historical failures, constraint inversion,
   and conditions under which a plausible path loses.

Before dispatch, mark each dimension `selected` or `not-applicable` with a
reason. Do not silently omit a relevant dimension.

## Frozen lane packet

Every packet contains only:

- `frontier_phase`: `breadth` or `depth`;
- one precise objective and the decision it informs;
- frozen question, decision, known facts, seeds, constraints, and non-goals;
- source/tool boundary, required output format, and evidence labels;
- one breadth `dimension`, or one promoted `candidate` for depth;
- required output fields, a probe limit, deadline capability
  (`runtime-enforced|coordinator-observed|unsupported`), a runtime/observation
  deadline, and the saturation/stop rule.

Unless the coordinator records a domain-specific reason to change it, a
breadth packet allows at most four decision-changing probes and a depth packet
at most six. A probe answers one frozen question that could change promotion or
falsification; opening several pages for that answer is not several probes.
This is a work boundary, not token accounting.

The coordinator, not the lane, owns deadline enforcement. Preflight the runtime
before dispatch:

- `runtime-enforced`: the backend accepts a launch-time execution timeout. A
  hard end-to-end deadline may start at dispatch.
- `coordinator-observed`: the backend returns a cancellable handle but cannot
  bound the synchronous launch call. The observation deadline starts when the
  handle is acquired; record launch latency separately.
- `unsupported`: there is no timely cancellable handle. Do not launch; create
  the phase-appropriate fallback immediately with `stop_reason:
  deadline-unenforceable`.

Never claim that `coordinator-observed` bounds launch latency. Unless the
runtime offers a stricter task deadline, allow no more than two consecutive wait
cycles of at most 60 seconds without a progress receipt. Freeze any different
deadline before dispatch. No wait cycle may outlive the applicable deadline,
and a progress receipt never extends it. The second consecutive no-progress
cycle triggers cancellation immediately with `stop_reason: no-progress`, even
when the later absolute deadline has not arrived. At any deadline, cancel once;
do not send the worker a reminder or a request to finalize. Then apply the
verified-termination versus `cancel-failed` gate below.

For a dispatched worker that may still be running, take the
coordinator-fallback path only after cancellation succeeds or termination is
independently verified. An `unsupported` packet that was never launched needs
no cancellation. If cancellation unexpectedly fails, record `stop_reason:
cancel-failed` and do not claim that the worker stopped or released capacity.
Dispatch a queued phase only when independent capacity is verified; otherwise
the frontier is operationally blocked and must return to the user.

A breadth lane returns coverage gaps, evidence-ledger rows, negative knowledge,
and at most two candidate cards. A candidate card contains `id`, mechanism,
distinctness from known options, evidence chain, expected decision impact,
prerequisites, failure modes, cheapest falsifier, and unknowns.

If a breadth worker misses its deadline and cancellation succeeds or termination
is independently verified, record that dimension as
`status: blocked`, `author: coordinator-fallback`, `stop_reason:
worker-deadline`, with only evidence already received and no candidate cards.
This is explicit missing coverage, not breadth research.

When deadline capability is `unsupported`, create the same artifact without
launching and use `stop_reason: deadline-unenforceable`.

A depth lane investigates exactly one candidate and always returns a dossier.
The dossier contains `status: complete|disproved|blocked`, causal mechanism,
strongest supporting and contradicting evidence, assumptions, dependency chain,
boundary interactions, failure envelope, cheapest decisive test, attempted
probes, stop reason, residual unknowns, and confidence by claim. It does not
open unrelated candidate branches. At a tool, source, or probe boundary, return
a `blocked` dossier from the evidence already gathered instead of waiting or
silently dropping the candidate. A blocked dossier satisfies protocol
accounting but cannot survive as a recommendation; the architect must reject or
defer it as `insufficient`.

If the worker does not return at the deadline, cancel the lane. After successful
cancellation or independently verified termination, the coordinator creates a
fallback dossier with `status: blocked`, `author:
coordinator-fallback`, candidate identity, evidence received before dispatch or
in progress receipts, `attempted_probes: unknown` when no receipt exists,
`stop_reason: worker-deadline`, and every unreturned claim listed as unknown.
This envelope closes protocol accounting only; it is not a researcher result or
evidence that the candidate was deeply investigated.

For `unsupported` deadline capability, do not dispatch depth; use the same
fallback dossier with `stop_reason: deadline-unenforceable`.

The architect receives a frozen falsification packet with the same deadline
capability contract. If it misses the deadline and cancellation succeeds or
termination is independently verified, record
`falsification_status: blocked`, `author: coordinator-fallback`, and
`stop_reason: worker-deadline`. No candidate survives independent falsification
in that run, so make no recommendation and hand off only to the user or
`wayfinder`.

## Promotion gate

Promote a candidate only when all are true:

1. it is a distinct mechanism rather than renamed user intent;
2. direct repository or primary-source evidence supports its central premise;
3. it remains inside the frozen goal and constraints;
4. resolving it could materially change the decision;
5. it has a concrete falsifier or a clearly named missing fact.

Classify failed candidates as `duplicate`, `disproved`, or `insufficient`, with
evidence. Preserve them as negative knowledge instead of sending them to depth.

## Completion rules

Stop a breadth lane when its selected dimension is covered, its probe limit is
reached, or two consecutive probes produce no `new` or `extension` evidence.
Stop a depth lane when the candidate's decisive claims are supported/disproved,
its probe limit is reached, the cheapest next check requires a
write/benchmark/user decision, or two consecutive probes only repeat existing
evidence. Every stop path returns the required output with its status, attempted
probes, stop reason, and gaps. A missed runtime deadline initiates cancellation
immediately; take the fallback path only after verified termination, otherwise
record `cancel-failed`. Do not ask the same worker to finalize again.

The coordinator completes the frontier only after every selected breadth
dimension has coverage or an explicit gap, every promoted candidate has a
depth dossier (including an honest `blocked` dossier), and the architect has
falsified the complete survivors and rejected/deferred blocked ones, or the
architect deadline has produced an explicit blocked falsification with no
recommendation. Report uncovered dimensions and unresolved claims; do not
convert missing research into confidence or call a blocked candidate deeply
validated.

A live worker with `stop_reason: cancel-failed` is not a completed fallback. If
it prevents any required breadth, depth, or falsification dispatch, stop with an
operational blocker rather than hand off a partial recommendation.
