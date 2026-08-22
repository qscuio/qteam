---
name: qteam-router
description: Route work into QTeam's single development workflow without creating competing orchestrators.
---

# QTeam Router

Load `goal-execution-discipline` as the standing execution contract. QTeam is
the only orchestration authority. Other skills are bounded decision,
planning, test, diagnosis, or review primitives; none may start their own
implementation loop.

Artifact-only requests may use `$isometric` for an evidence-backed whole-repo
architecture map, `$diagram-creator` for a focused static technical/UML visual,
`$show-me` for an interactive teaching UI, or `$handoff` for an explicit
session/owner change without creating a QTeam run. If one is a deliverable of
an active run, keep it inside that run's task/write-set/review lifecycle; the
artifact skill does not bypass execution gates.

Before starting or resuming execution, require the repository runtime marker
`.codex/qteam-project.json` and executable `.codex/bin/agent-team-state`. If
either is absent, do not create partial run state: tell the operator to run
`./qteam setup <repository>` from a QTeam checkout, then start a new Codex task
so the plugin and read-only role configuration are both loaded.

Route in this order:

1. Active unfinished run: resume its recorded phase. Never brainstorm again.
2. Fully delivered multi-run epic whose product should improve QTeam: use
   `qteam-retrospect`. It seals cross-run evidence and approved proposals; it
   never edits completed runs or canonical QTeam behavior.
3. Bug, regression, hang, or performance failure: use `qteam-diagnose`.
4. Clear destination but unknown solution space, or an explicit request to find
   paths/ideas/knowledge beyond the stated options: use `qteam-explore`. It
   produces a bounded evidence brief; an explicit deep/broad request uses its
   full research-frontier rule. It never starts an implementation loop.
5. Huge multi-session effort whose decision path is foggy: use `wayfinder`.
   When it decomposes into multiple independently executable specs, create one
   QTeam epic manifest with cross-run dependencies and stable contracts. Hand
   each unblocked run to `to-spec`; do not create an epic for a single run.
6. Unclear new behavior: classify it before `brainstorming`:
   - spike: a disposable learning task/experiment whose result returns for
     approval, never an unreviewed shipping shortcut;
   - bounded: a short design for a local, reversible change;
   - architectural: full alternatives, domain model, spec, and ticket DAG.
   Invoke `grilling` only on an unresolved high-impact branch and
   `grill-with-docs` only when the domain vocabulary is changing.
7. Sufficient approved context: use `to-spec`, then `to-tickets`, then
   `agent-team-dev` execution.

Inside execution, route only policy-triggered quality lanes to `qteam-harden`.
Do not invoke it as a blanket extra review. The Web UI and optional Herdr pane
are operator displays over the same run; neither changes this routing order.

Do not invoke Superpowers `executing-plans`, `subagent-driven-development`, or
Matt-style issue implementation as a second coordinator. During QTeam runs,
`agent-team-dev` owns phases, workers, merging, reviews, and finish.
