---
name: brainstorming
description: Explore unclear requirements and compare viable designs before QTeam execution.
---

# Bounded Brainstorming

This is a QTeam design primitive, not an execution workflow. Inspect repository
facts before asking questions. Clarify goal, constraints, success criteria,
non-goals, users, failure behavior, and 2–3 viable approaches. Ask one decision
frontier at a time, recommend an answer for each, and use `grilling` only for a
genuinely unresolved high-impact branch.

Choose the smallest design path that matches the risk: a spike produces only a
throwaway/prototype experiment and an approval decision; a bounded change gets
a short design; an architectural change gets full alternatives, domain model,
spec, and ticket DAG. Every path remains approval-gated before shipping.

Produce a concise approved design/spec input and return control to
`qteam-router`. Do not spawn implementers, write an implementation plan, create
worktrees, review code, or finish a branch.
