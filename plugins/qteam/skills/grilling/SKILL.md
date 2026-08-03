---
name: grilling
description: Stress-test an unresolved plan or design branch through a one-question-at-a-time interview.
---

# Grilling

Interview the user one decision at a time until the selected design branch is
shared and explicit. Walk dependencies in order and include a recommended
answer with each question. Discover repository facts yourself; ask the user
only for decisions. Do not ask bundles of questions and do not implement.

Inside QTeam this is a decision primitive called by `qteam-router`,
`brainstorming`, `architect`, or `wayfinder`. When the branch is resolved,
return the decision to the coordinator; never start a separate plan/execution
workflow.
