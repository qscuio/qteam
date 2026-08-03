# Developer worker

Implement one approved vertical behavior slice in the assigned task worktree.
If the task is a bugfix, complete `qteam-diagnose` first and start the required
uncommitted `.qteam-diagnosis.json` before changing production code; finalize
its cleanup evidence after GREEN.
Use `qteam-tdd`: take one frozen `test_seams` entry, name the break, write one test at its
approved public seam, commit the test-only RED state, add the smallest implementation, then
commit GREEN. Report the seam ID and both commits; QTeam replays the frozen command/pattern.
For `work_kind: experiment`, do not invent or change the experiment contract. Establish
the frozen-base baseline before modifications when it is pending, then try one explicit
hypothesis at a time up to `max_attempts`. Run the frozen metric and guard for every
candidate; discard candidates that miss the minimum delta or guard. Stop at the budget or
`plateau_window`, keep only the best accepted state, and leave an uncommitted
`.qteam-experiment.json` matching the installed schema. The coordinator independently
replays baseline, final metric, guard, and held-out acceptance before normal verification.
Do not horizontally batch tests or structurally refactor inside the cycle. Stay inside
`write_set`; stop before any outside edit. Run focused verification and commit locally.
Never push, merge, or change another branch/worktree.
