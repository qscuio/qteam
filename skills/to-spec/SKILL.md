---
name: to-spec
description: Synthesize approved conversation and repository context into a QTeam-ready specification without re-interviewing.
disable-model-invocation: true
---

# To Spec

Synthesize what is already known; do not restart the interview. Respect the
domain glossary and ADRs. Prefer existing public test seams and select the
highest stable seam that proves observable behavior.

The spec contains: problem statement, user-visible solution, numbered user
stories, acceptance criteria, implementation decisions without brittle code or
file trivia, testing decisions and chosen seams, constraints/invariants,
explicit out-of-scope items, assumptions, and unresolved blockers.

Write it into the run/plan artifacts and hand it to `to-tickets`. Publishing to
an external tracker requires separate user authorization and is not part of
this skill.
