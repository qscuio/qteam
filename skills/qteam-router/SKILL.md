---
name: qteam-router
description: Route work into QTeam's single development workflow without creating competing orchestrators.
---

# QTeam Router

Load `goal-execution-discipline` as the standing execution contract. QTeam is
the only orchestration authority. Other skills are bounded decision,
planning, test, diagnosis, or review primitives; none may start their own
implementation loop.

Route in this order:

1. Active unfinished run: resume its recorded phase. Never brainstorm again.
2. Bug, regression, hang, or performance failure: use `qteam-diagnose`.
3. Huge multi-session effort whose decision path is foggy: use `wayfinder`;
   when the route is clear, hand off to `to-spec`.
4. Unclear new behavior: use `brainstorming`; invoke `grilling` only on an
   unresolved high-impact branch and `grill-with-docs` only when the domain
   vocabulary is changing.
5. Sufficient approved context: use `to-spec`, then `to-tickets`, then
   `agent-team-dev` execution.

Do not invoke Superpowers `executing-plans`, `subagent-driven-development`, or
Matt-style issue implementation as a second coordinator. During QTeam runs,
`agent-team-dev` owns phases, workers, merging, reviews, and finish.
