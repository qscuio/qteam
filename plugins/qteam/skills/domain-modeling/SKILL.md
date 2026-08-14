---
name: domain-modeling
description: Use when discussing codebase terminology, writing or editing CONTEXT.md, or deciding whether a durable architectural decision needs an ADR.
---

# Domain Modeling

Use this only when changing the model, not merely reading vocabulary.

- Challenge conflicts with existing `CONTEXT.md` language.
- Replace vague or overloaded terms with one precise canonical term.
- Invent concrete edge cases to test relationships and boundaries.
- Cross-check claims against code and surface contradictions.
- Update the nearest `CONTEXT.md` immediately when a term is resolved. It is a
  glossary only: no implementation details, specs, or scratch notes.
- If `CONTEXT-MAP.md` exists, use its bounded-context locations.
- Create an ADR only if the decision is hard to reverse, surprising without
  rationale, and the result of a genuine trade-off.

Create files lazily. Domain decisions feed `to-spec`; they do not authorize
execution.
