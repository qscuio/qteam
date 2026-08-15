# UML use-case diagram

Use to show external actors and the user-visible goals a system supports. It is
not a process flow or a screen map.

Load `uml-notation.md` first.

## Visual grammar

- Draw one named system boundary as a restrained rectangular region.
- Actors sit outside the boundary. Use a simple stick figure plus a readable
  role name; systems may use a rectangular `«actor»` form.
- Use cases are concise verb phrases inside ellipses.
- Associations are solid and usually undirected.
- `«include»` is a dashed open arrow from the base use case to the always-used
  included use case.
- `«extend»` is a dashed open arrow from the optional extension to the base.
- Actor/use-case generalization uses a solid hollow triangle toward the parent.

## Budget

4 actors, 9 use cases, 10 associations, and at most 3 include/extend relations.
Split by actor journey or subsystem when the boundary becomes a web.

## Type-specific failures

- Turning ordered steps into use cases; use Activity or Flowchart instead.
- Putting actors inside the system boundary.
- Reversing `include` or `extend` arrows.
- Naming internal implementation tasks instead of actor goals.
- Using color as the only distinction between actor and use case.
