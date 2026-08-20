# Diagram Contract BOUNDARIES

## State ownership — exactly one writer per fact

| fact | owner | sole write path | readers |
|---|---|---|---|
| semantic element identity, label, kind | embedded Diagram Contract v1 | contract authoring step | validator, reader |
| relationship endpoints, label, kind | embedded Diagram Contract v1 | contract authoring step | validator, reader |
| rendered bounds and routes | annotated inline SVG | diagram rendering step | composition validator |
| fixed composition limits | `diagram_contract.py` profile table | plugin release | validator |
| validation result | derived report | `diagram_contract.py check` | agent, CI |

The SVG projection repeats contract IDs and renders exact validator-checked
copies of labels/stereotypes plus derived geometry. It does not own or mutate
those semantic values, endpoint identity, or relationship kind. `check`
reconciles every contract ID with exactly one rendered element before assessing
composition.

## Dependency edges — whitelist

`diagram_contract.py` -> Python standard library

`diagram_contract.py` -> `self_check.verify()` and `verify_geometry.check()`

`self_check.py` -> safe non-executable contract-script envelope only

Skill workflow -> `diagram_contract.py validate/check/inspect`

No QTeam run-state, reviewer, Web, goal, or installer module imports this skill
runtime. Plugin packaging copies it as skill data.

## Vocabulary

| concept | project word | authority |
|---|---|---|
| typed semantic source embedded in HTML | Diagram Contract | this file |
| semantic object drawn in the SVG | element | contract `elements[]` |
| typed connection between elements | relationship | contract `relationships[]` |
| rendered geometry linked to an ID | projection | SVG `data-diagram-*` attributes |
| deterministic visual-quality result | composition report | validator output |

## Non-goals

- No second diagram renderer or alternate HTML source of truth.
- No reverse engineering semantics from unannotated arbitrary SVG.
- No full UML/XMI conformance claim.
- No node/relationship contract for quantitative charts, matrices, or plots.
- No review daemon, database, finding authority, or orchestration state.
