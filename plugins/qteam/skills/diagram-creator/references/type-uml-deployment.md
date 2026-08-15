# UML deployment diagram

Use for runtime topology: physical/virtual nodes, execution environments,
deployed artifacts, and communication paths. It answers where software runs,
not how components are designed internally.

Load `uml-notation.md` first.

## Visual grammar

- A node is a modest offset-outline box labeled `«device»`, `«node»`, or
  `«executionEnvironment»` plus its instance/type name.
- Execution environments nest inside their hosting node.
- Deployed artifacts are file-corner rectangles labeled `«artifact»` inside
  the environment that owns the deployment.
- Communication paths are solid undirected associations labeled with protocol,
  port, or trust property. Deployment dependencies from artifact to node are
  dashed arrows only when containment cannot show placement.
- Use zones for trust or region boundaries only when those boundaries are
  explicitly part of the source.

## Budget

8 nodes/environments, 10 artifacts, 12 communication paths, 3 zones. Split
logical component design from physical placement instead of overloading one
canvas.

## Type-specific failures

- Treating a component as a machine or a machine as a component.
- Showing an artifact outside every node with no deployment relationship.
- Inventing protocols, ports, regions, replicas, or trust zones.
- Using perspective effects or shadows that overpower semantic containment.
