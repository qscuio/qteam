# UML component diagram

Use for replaceable software units, their contracts, ports, and dependencies.
For a general system overview without interface semantics, prefer Architecture.

Load `uml-notation.md` first.

## Visual grammar

- Components are rectangles carrying either the UML component glyph or
  `«component»`; use one treatment consistently.
- Ports are small squares on a component boundary.
- A provided interface is a lollipop (circle); a required interface is an open
  socket. An assembly connector joins a required socket to a provided circle.
- Interfaces may instead be classifier boxes connected by realization and
  usage dependencies when names need more space.
- Group components inside one restrained subsystem/package boundary only when
  the boundary answers the diagram's main question.

## Supported semantics

Component, interface, port, provided/required interface, assembly connector,
realization, dependency/usage, package/subsystem boundary.

When a component has a load-bearing domain role such as a database or queue,
keep `kind: component` and record the role in Diagram Contract v1 as a rendered
stereotype (`stereotype: database` / `stereotype: queue`). Do not silently
collapse that role into an untyped box.

## Budget

9 components, 8 interfaces, 12 connectors, 2 subsystem boundaries. If an
interface has more than three consumers, make it an explicit classifier rather
than stacking lollipops.

## Type-specific failures

- Decorating architecture boxes with lollipops that have no named contract.
- Connecting two required interfaces or two provided interfaces.
- Using a data-flow arrow where a dependency is meant.
- Mixing runtime hosts into the same view; use Deployment for placement.
