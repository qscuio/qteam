---
name: to-spec
description: Synthesize approved conversation and repository context into a QTeam-ready specification without re-interviewing.
---

# To Spec

Synthesize what is already known; do not restart the interview. Respect the
domain glossary and ADRs. Incorporate any approved `qteam-explore` evidence
brief without converting low-confidence proposals into requirements. Prefer
existing public test seams and select the highest stable seam that proves
observable behavior.

If the public seam or interface shape is materially unresolved, do not invent
it during synthesis. Return one scoped decision to the coordinator (or an
architectural design branch) and resume synthesis after it is resolved.

Begin a QTeam-owned spec with `<!-- qteam-artifact: spec-v1 -->`. The spec
contains: problem statement, user-visible solution, numbered `US-*` user
stories, acceptance criteria, implementation decisions without brittle code or
file trivia, testing decisions and chosen seams, constraints/invariants,
explicit out-of-scope items, assumptions, and unresolved blockers.

Use stable `AC-*` identifiers and prefer observable Given/When/Then criteria.
Before approval, run
`.codex/bin/agent-team-artifact lint --kind spec --file <spec>`. Errors block
handoff; warnings are bounded semantic-review inputs, not automatic defects.

Write it into the run/plan artifacts and hand it to `to-tickets`. Publishing to
an external tracker requires separate user authorization and is not part of
this skill.
