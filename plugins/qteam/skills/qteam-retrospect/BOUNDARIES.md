# Product Closeout BOUNDARIES

## State ownership — exactly one writer per fact

| fact | owner | sole write path | readers |
|---|---|---|---|
| cross-run execution completion | epic manifest | `agent-team-artifact epic-complete-run` | closeout sealer, operator |
| run completion and review evidence | completed QTeam run | existing run/review state commands | closeout sealer, retrospective reviewers |
| released product head | product closeout | `product-closeout-seal` | closeout checker, improvement run |
| observed product outcome | product closeout | `product-closeout-seal` from a reviewed draft | coordinator, improvement run |
| QTeam improvement proposal | product closeout item | `product-closeout-seal`, then one coordinator decision | closeout checker, improvement run |
| canonical QTeam behavior | QTeam source repository | a separate reviewed QTeam implementation run | later QTeam releases |

A product closeout references completed run evidence by path and digest. It does
not copy, rewrite, or become a second owner of run or epic state.

## Dependency edges — whitelist

completed epic -> product closeout sealer -> immutable evidence bindings
two distinct read-only retrospective reviewers -> closeout draft -> product closeout sealer
coordinator -> one decision per improvement proposal
approved closeout brief -> separate QTeam self-improvement run
self-improvement run -> eval-first change -> versioned QTeam release

No closeout command writes qnote, another repository, completed run state,
canonical skills, worker prompts, QTeam policy, or QTeam source code.

## Vocabulary

| concept | project word | authority |
|---|---|---|
| execution portfolio spanning QTeam runs | epic | `wayfinder` and `epic.schema.json` |
| retrospective over a delivered epic release | product closeout | operator-visible product lifecycle |
| evidence-bounded observed result | outcome | learning outbox validation vocabulary |
| proposed change to QTeam behavior | improvement proposal | product closeout contract |
| assessment of an earlier QTeam change | prior improvement assessment | product feedback loop |
| implementation input containing approved proposals | improvement brief | QTeam self-improvement handoff |

`Product` does not replace or mirror `epic`: the epic owns execution; the
product closeout owns only the release retrospective over that completed epic.

## Non-goals

- No model-weight training or raw transcript accumulation.
- No automatic mutation of QTeam, qnote, canonical skills, or another repo.
- No closeout before every epic run is durably `DONE` and contained in the
  release commit.
- No approval by the retrospective author or distiller.
- No proposal without exact run evidence, a validation scope, a claim boundary,
  and an observable success criterion.
- No CI, PR, publication, or remote release behavior.
