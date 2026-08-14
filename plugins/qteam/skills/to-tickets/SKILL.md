---
name: to-tickets
description: Convert an approved spec into independently verifiable vertical slices and a blocking DAG for QTeam.
---

# To Tickets

Break the spec into tracer-bullet vertical slices, not layer-by-layer work.
Each slice delivers a narrow complete path through the relevant layers and is
demoable or verifiable on its own and fits one fresh implementation context.
Put genuinely enabling prefactors first. Group small, independent, same-shape
changes only when one bounded packet and one verification contract stay
clearer than repeated tickets.

A wide refactor is the deliberate exception to forced vertical slicing: use an
expand phase that introduces the new shape compatibly, bounded migrate batches,
then a contract phase that removes the old path. Each batch must remain
independently verifiable and reversible; do not hide a repository-wide rewrite
inside one ticket.

For every slice record title, purpose, user stories, acceptance criteria,
dependencies, contract, write/read sets, forbidden paths, shared surfaces,
resource namespaces, focused tests, integration tests, verification command,
and stop rule. Wire a blocking DAG and safe waves; shared schemas, migrations,
locks, build files, generated surfaces, and global fixtures are serial.

The human-readable slices and machine task records are QTeam inputs. Do not
start workers or publish tracker tickets independently.

Begin the human-readable ticket artifact with
`<!-- qteam-artifact: tickets-v1 -->`. Give every slice a stable `T*` ID and
explicit `depends_on`, `Requirements`, `Done when`, and `Verify` fields. Before
`PLAN_READY`, run `.codex/bin/agent-team-artifact lint --kind tickets --file
<tickets>`. These fields are checked independently for every task. This cheap
gate catches structural and dependency-reference defects before an LLM
reviewer sees the wave.

For a mechanically measurable candidate, emit `work_kind: experiment` and an
exact `experiment` object containing `goal`, `metric` (`name`, `direction`,
`command`, numeric observed `baseline` or `null`, and `minimum_delta`),
`guard_command`, `holdout_command`, `max_attempts`, and `plateau_window`.
`null` means the coordinator must establish the baseline at the frozen
`base_commit` before modifications. The worker writes `.qteam-experiment.json`;
the coordinator replays baseline, final metric, guard, and held-out acceptance
through `agent-team-state experiment-put` before normal task verification.
