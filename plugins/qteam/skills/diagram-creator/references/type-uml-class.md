# UML class diagram

Use for static structure: classifiers, their visible members, and typed
relationships. For database ownership and cardinality without methods or
inheritance, prefer ER/data model.

Load `uml-notation.md` first.

## Visual grammar

- Draw each classifier as a crisp three-compartment rectangle: name,
  attributes, operations. Omit an empty compartment instead of filling it.
- Put `«interface»`, `«enumeration»`, or another stereotype above the name.
- Italicize an abstract classifier name and abstract operations; also allow
  `{abstract}` when the font treatment would be ambiguous.
- Members use a compact mono line: `- cache: Cache`, `+ find(id: ID): Item`.
- Place parent classes and interfaces above children; place wholes left or
  above parts. Route lateral associations around inheritance trunks.

## Supported semantics

Class, abstract class, interface, enumeration; attributes and operations;
association, navigability, dependency, generalization, realization,
aggregation, composition; role names and multiplicities.

Do not imply ownership with containment alone. Do not use composition unless
the part's lifecycle is owned by the whole.

## Budget

- 7 classifiers, 10 relationships, 6 visible members per classifier.
- Collapse routine members behind `…` only when the omitted count is disclosed.
- Split large models by package or bounded context; link an overview to detail.

## Type-specific failures

- Every line ends in the same arrowhead.
- Attribute types are confused with relationship labels.
- Members are microscopic so the whole codebase fits on one canvas.
- Aggregation and composition diamonds appear on the part end.
- Layout implies inheritance that the connectors do not state.
