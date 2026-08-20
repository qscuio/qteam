# Diagram Contract v1

Use this contract for structural diagrams whose meaning depends on named
elements and typed relationships. It turns the provable part of a diagram into
strict data while leaving layout, typography, and editorial hierarchy in the
HTML/SVG visual system.

## Contents

- [Scope](#scope)
- [Contract](#contract)
- [Semantic profiles](#semantic-profiles)
- [SVG projection](#svg-projection)
- [Composition profiles](#composition-profiles)
- [Commands](#commands)

## Scope

Required for new **static** architecture/high-level/IT/medallion/integration,
data-flow, process/swimlane, ER, tree/org/nested/layer, UML class, UML
component, and UML deployment figures. Sequence, state-machine, flowchart,
UML use-case/activity, loop, and animated figures need specialized lifeline,
curve, diamond, actor, bar, loop, or time-state semantics and are deliberately
outside v1. Quantitative figures keep their data/cell authority.

The final deliverable remains one self-contained HTML file. The contract is an
embedded non-executable JSON script. It is not a second sidecar or renderer.

## Contract

Embed exactly one strict object:

```html
<script type="application/json" data-diagram-contract>
{
  "schema_version": 1,
  "diagram_type": "architecture",
  "semantic_profile": "architecture",
  "composition_profile": "showcase",
  "title": "Request path",
  "elements": [
    {"id": "client", "label": "Client", "kind": "actor", "stereotype": "external"},
    {"id": "api", "label": "API", "kind": "service", "stereotype": "origin"}
  ],
  "relationships": [
    {"id": "request", "source": "client", "target": "api",
     "label": "HTTPS", "kind": "request"}
  ]
}
</script>
```

Escape every literal `<` in JSON strings as `\u003c`. IDs are unique safe
identifiers. `parent` and `stereotype` are optional element fields. `parent`
names a containing element and must form an acyclic containment tree;
`stereotype` preserves a lower-case semantic role shared by one geometric kind.
Unknown fields fail closed.

## Semantic profiles

Select the profile that owns relationship meaning, independently of the visual
variant. The runtime publishes the exact allowed element and relationship
kinds; `validate` rejects unknown kinds, dangling endpoints, cross-kind ID
collisions, invalid self-loops, and profile/type mismatches.

| Profile | Typical element kinds | Distinct relationship kinds |
|---|---|---|
| `architecture` | actor, service, component, store, external, queue, boundary | request, response, read, write, event, dependency, deployment, blocked |
| `data-flow` | source, process, store, sink, queue | data, event, control, feedback |
| `er` | entity, associative-entity | association, identifying |
| `uml-class` | class, interface, abstract-class, enumeration | all supported class relationships |
| `uml-component` | component, provided/required interface, port, artifact, subsystem | dependency, realization, assembly, delegation |
| `uml-deployment` | device, execution-environment, artifact, component | communication, deployment, dependency |
| `generic` | element, group | relationship |

Use `generic` only when no stronger profile represents the type. It is not an
escape hatch for misspelled or unsupported domain semantics.

## SVG projection

The contract owns labels, stereotypes, kinds, and endpoints. The SVG renders
exact validator-checked text copies plus IDs and derived geometry:

```html
<rect data-diagram-element="client" data-diagram-bounds="20,40,120,64"
      x="20" y="40" width="120" height="64"/>
<text data-diagram-element-label="client">Client</text>
<text data-diagram-element-stereotype="client">«external»</text>
<path data-diagram-relationship="request"
      data-diagram-route="140,72 220,72" d="M140 72 H220"
      fill="none" stroke="#4f5d75" stroke-width="1.2"/>
<text data-diagram-relationship-label="request">HTTPS</text>
```

Every contract element, element label, and relationship has exactly one
annotated projection. The checker cannot infer whether an unannotated
decorative SVG primitive represents another domain fact; authoring discipline
and the visual review own that residual completeness boundary. Put the element annotation on its visible
`rect`, `circle`, or `ellipse`; declared `x,y,width,height` must equal that
shape's actual geometry. Routes contain
2–16 whitespace-separated `x,y` points and describe the logical orthogonal
centerline; the annotated `line`, `polyline`, or absolute `M/H/V/L/Q` path must
resolve to exactly that route. Rounded corner control points are collapsed. Route endpoints
must land on the source and target bounds. A non-empty relationship label must
appear exactly once and match the contract text. The semantic projection uses
one non-nested SVG with a finite positive `viewBox`; all bounds, routes, and
estimated text glyph boxes must lie inside it. Semantic text is a plain `<text>`
element with explicit `x`, `y`, `font-size`, `fill`, and optional
`text-anchor`; alternate positioning, `textLength`, nested markup, SVG motion,
and `switch` are outside Contract v1.

Put semantic SVG geometry, paint, and text presentation in explicit SVG
attributes. Do not put inline CSS on a semantic projection or any of its
ancestors. Stylesheets may style the surrounding page, but a selector that can
match a semantic projection may use only neutral box/margin/padding rules; SVG
geometry, paint, text, masking, and animation stay in the checked attributes.
Ancestor selectors are limited to the validator's page-layout allowlist. An
unsupported or ambiguous selector/property fails closed; this keeps the
dependency-free coordinate binding deterministic.

When `stereotype` is present, render it exactly once as `«value»` with
`data-diagram-element-stereotype="<element-id>"`. This preserves load-bearing
roles such as a UML component stereotyped `database` or `queue` without
inventing a non-standard UML element kind.

## Composition profiles

Profiles are fixed in the shipped runtime, so an artifact cannot weaken its own
quality gate. Both profiles reject overlaps, diagonal or zero-length segments,
bad containment, and unbound geometry.

| Limit | `standard` | `showcase` |
|---|---:|---:|
| Bends per relationship | 4 | 2 |
| Total bends | 24 | 12 |
| Maximum route stretch | 3.0 | 1.6 |
| Crossings or route touches | 0 | 0 |
| Minimum element gap | 16px | 32px |
| Minimum segment | 8px | 16px |

Use `showcase` for the normal ≤9-node editorial figure. Use `standard` only
for a justified faithful import or dense detail view; it does not relax the
parent skill's connector and accessibility rules.

## Commands

Run validation before layout, then bind the completed artifact:

```bash
python3 <skill-dir>/scripts/diagram_contract.py validate model.json
python3 <skill-dir>/scripts/diagram_contract.py check diagram.html \
  --report diagram.composition.json
python3 <skill-dir>/scripts/diagram_contract.py inspect diagram.html
```

`check` also runs the packaged self-contained/accessibility check and label-mask
geometry check. A report is derived evidence and may be rebuilt; the embedded
contract and HTML remain the artifact authority. `inspect` prints the bound
element/relationship model, including optional stereotypes, for downstream
read-only tooling; it does not create another stored index.
