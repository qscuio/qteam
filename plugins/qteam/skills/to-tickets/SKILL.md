---
name: to-tickets
description: Convert an approved spec into independently verifiable vertical slices and a blocking DAG for QTeam.
---

# To Tickets

Break the spec into tracer-bullet vertical slices, not layer-by-layer work.
Each slice delivers a narrow complete path through the relevant layers and is
demoable or verifiable on its own. Put genuinely enabling prefactors first.

For every slice record title, purpose, user stories, acceptance criteria,
dependencies, contract, write/read sets, forbidden paths, shared surfaces,
resource namespaces, focused tests, integration tests, verification command,
and stop rule. Wire a blocking DAG and safe waves; shared schemas, migrations,
locks, build files, generated surfaces, and global fixtures are serial.

The human-readable slices and machine task records are QTeam inputs. Do not
start workers or publish tracker tickets independently.

For a mechanically measurable candidate, emit `work_kind: experiment` and an
exact `experiment` object containing `goal`, `metric` (`name`, `direction`,
`command`, numeric observed `baseline` or `null`, and `minimum_delta`),
`guard_command`, `holdout_command`, `max_attempts`, and `plateau_window`.
`null` means the coordinator must establish the baseline at the frozen
`base_commit` before modifications. The worker writes `.qteam-experiment.json`;
the coordinator replays baseline, final metric, guard, and held-out acceptance
through `agent-team-state experiment-put` before normal task verification.
