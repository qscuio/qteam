# UML notation contract

Use this reference whenever the user explicitly asks for UML. It defines the
supported semantic subset. The selected `type-uml-*.md` file defines the
layout. The output is editorial HTML + inline SVG; it is not PlantUML source,
XMI, or a machine-verifiable UML model.

## Semantic priority

Notation meaning outranks decoration. Never replace two different UML
relationships with the same arrow merely because that is visually simpler.
When the request needs an unsupported construct, either split the view or list
the omission beside the deliverable. Do not claim complete UML conformance.

## Shared labels

- Stereotypes use guillemets: `«interface»`, `«component»`, `«device»`.
- Visibility is `+` public, `-` private, `#` protected, `~` package.
- Multiplicity stays at the relevant association end: `1`, `0..1`, `*`,
  `1..*`.
- Guards use brackets: `[authorized]`. Effects follow `/`.
- Keep labels horizontal and close to the semantic endpoint they qualify.

## Relationship markers

Define separate SVG markers for every relationship used.

| Relationship | Stroke | Marker direction |
|---|---|---|
| Association | solid | none by default; optional open navigability arrow |
| Dependency | dashed | open arrow points to supplier |
| Generalization | solid | hollow triangle points to parent |
| Realization | dashed | hollow triangle points to interface/specification |
| Aggregation | solid | hollow diamond sits at the whole |
| Composition | solid | filled diamond sits at the whole |

The diamond is attached to the owning/whole end, never the part. A hollow
triangle is not a filled flow arrow. If markers overlap a box, move the path
endpoint outward; do not hide the marker behind the node.

## Geometry and disclosure

- UML relations still obey the parent skill's orthogonal connector and fan-out
  rules. A relation's marker may alter only its last segment.
- Use the same relation treatment consistently across the whole diagram.
- Put one compact notation key below the canvas when more than two UML
  relationship kinds appear.
- Preserve requested names, types, multiplicities, guards, stereotypes, and
  deployment assignments. Never invent them to fill a layout.
- If the source is ambiguous, mark the uncertainty in an editorial note rather
  than silently choosing a stronger semantic relation.

## UML taste gate

- [ ] Every arrowhead or diamond points to the semantically correct end.
- [ ] Association, dependency, generalization, realization, aggregation, and
      composition remain visually distinct.
- [ ] Stereotypes, guards, multiplicities, and visibility symbols are literal.
- [ ] The chosen diagram contains only one dominant semantic question.
- [ ] Unsupported or simplified notation is disclosed.
