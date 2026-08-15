# UML activity diagram

Use for behavior whose meaning depends on actions, guarded branching,
concurrency, object flow, or responsibility partitions. For simple decision
logic use Flowchart; for handoffs without UML semantics use Swimlane.

Load `uml-notation.md` first.

## Visual grammar

- Initial node: filled circle. Activity final: filled circle with outer ring.
- Actions: softly squared rectangles with verb-first labels.
- Decision/merge: diamond. Put guards on outgoing edges, never inside the
  diamond; guards must be mutually intelligible even if not formally complete.
- Fork/join: thick bar perpendicular to the flow. A fork has one incoming and
  multiple outgoing edges; a join has multiple incoming and one outgoing.
- Object nodes: rectangles with typed noun labels; object flows are visually
  distinct from control flows and named when ambiguity exists.
- Optional partitions group ownership but do not duplicate action labels.

## Budget

9 actions, 3 decision/merge nodes, 2 fork/join pairs, 4 partitions, 12 flows.
Split exception handling or a concurrent branch into a detail diagram when it
breaks the budget.

## Type-specific failures

- Using a fork bar as a decorative section divider.
- Leaving decision edges without guards.
- Connecting concurrent branches through a merge diamond instead of a join.
- Using an activity diagram for class or deployment structure.
- Autoplay animation that is required to understand the static model.
