---
name: grilling
description: Stress-test an unresolved plan or design branch through dependency-ordered question frontiers.
---

# Grilling

Interview in rounds. In each round, ask the current frontier of independent
decisions; hold dependent questions for a later round. Number every question,
give 2–3 mutually exclusive choices, and recommend one with its trade-off.
Keep the frontier small enough to answer coherently. Wait for the full round
before advancing.

Discover repository and external facts yourself; ask the user only for choices
that require their authority. Missing fact research may run in the background
and must not block an unrelated frontier question. Do not implement.

Inside QTeam this is a decision primitive called by `qteam-router`,
`brainstorming`, `architect`, or `wayfinder`. When the branch is resolved,
return the decision to the coordinator; never start a separate plan/execution
workflow.
