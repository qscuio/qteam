# Visual and interaction contract

## Composition

- The map is an orientation surface, not a deployment claim. Say explicitly
  when structures are modules or roles rather than processes.
- Keep footprints disjoint. Painter order is grid `gx + gy`, then `gx`.
- Largest measured structures may be taller, but size is not importance.
- Stores use the flat `store` treatment. External systems sit outside the city.
- Ordinary edges use the engine's elbow route. Add the fewest `via` points.
- One accent identifies the active structure or trace step; do not color every
  block as equally important.
- The SVG uses each structure's two-character code and `X01`, `X02`, ... for
  external systems. Full names are never squeezed into the geometric layer;
  they remain complete and wrapping in the rail and detail panel.

## Required interaction states

- Default overview with visible title, statistics, structure rail, map, and
  `What / How / Invariant` detail.
- Structure selection through both a map block and keyboard-focusable rail
  button.
- `#structure=<id>` selects a structure deterministically.
- `#inside=<id>` displays children when the structure has them.
- `#external=<id>` selects an external system and explains its relationship.
- `#trace=<n>` selects a deterministic one-based trace step.
- Back/next/close controls never move outside trace bounds.

## Accessibility and fallback

- SVG has `role="img"`, a first-child title, description, and resolving
  `aria-labelledby`.
- Dynamic detail uses polite live-region semantics.
- Every interaction has a native button or link path and visible keyboard
  focus.
- Reduced-motion mode disables moving flow markers and transitions.
- The no-JavaScript and print summaries contain the overview invariant and
  complete structure list.
- Information is never encoded by color alone.

## Review views

Inspect default, selected structure, drill-down (if any), middle trace (if any),
narrow viewport, reduced motion, and print. Reject clipped labels, unreadable
edges, unexplained isolated structures, empty panels, stale counts, broken hash
states, or any browser console error.
